"""available_at 门控的收益与异常收益计算（反前视偏差核心模块）。

设计不变量（有专项测试）：
1. 信号/特征只能通过 `bars_visible_to` 获取决策时点前可见的行情；
2. 事件收益（结果变量）允许使用决策日之后的价格——但只经由 `forward_return`，
   该函数绝不暴露给特征构建路径；
3. 停牌日无成交：收益按「最近可成交收盘价」衔接（业界惯例），并在结果中标注 carried。
"""

from __future__ import annotations

import bisect
import datetime as dt
from dataclasses import dataclass

from asg_domain.errors import AvailabilityError


@dataclass(frozen=True)
class Bar:
    security_id: str
    trade_date: dt.date
    open: float
    high: float
    low: float
    close: float
    pre_close: float | None
    limit_up: float | None
    limit_down: float | None
    suspended: bool


class _SecurityIndex:
    """单证券索引：O(1) 日期寻址 + carry 收盘前缀（语义与线性扫描逐位一致）。"""

    __slots__ = ("bar_by_date", "carry", "dates", "series", "suspended_before", "trading_days", "trading_pos")

    def __init__(self, series: list[Bar]) -> None:
        # series 已按 trade_date 升序（PriceBook 构造时排序校验）
        self.series = series
        self.dates = [b.trade_date for b in series]
        self.bar_by_date = {b.trade_date: b for b in series}
        self.trading_days = [b.trade_date for b in series if not b.suspended]
        self.trading_pos = {d: i for i, d in enumerate(self.trading_days)}
        # carry[i] = series[0..i] 中最近一个非停牌 bar 的 (close, trade_date)；None 表示尚无
        carry: list[tuple[float, dt.date] | None] = []
        best: tuple[float, dt.date] | None = None
        for b in series:
            if not b.suspended:
                best = (b.close, b.trade_date)
            carry.append(best)
        self.carry = carry
        # suspended_before[i] = series[0..i-1] 中停牌 bar 数（前缀计数）
        sf = [0]
        for b in series:
            sf.append(sf[-1] + (1 if b.suspended else 0))
        self.suspended_before = sf


class PriceBook:
    """只读价格簿：按证券组织的 Bar 序列（日期升序，构造时校验）。

    阶段 11：内部按证券建日期索引（bar_by_date / trading_pos / carry 前缀），
    消除 forward_return 热路径上的每次线性扫描；返回值语义与线性扫描版本
    逐位一致（回归由既有 unit/leakage 测试锁定）。
    """

    def __init__(self, bars: list[Bar]) -> None:
        by_sec: dict[str, list[Bar]] = {}
        for b in bars:
            by_sec.setdefault(b.security_id, []).append(b)
        self._idx: dict[str, _SecurityIndex] = {}
        for sec, series in by_sec.items():
            series.sort(key=lambda b: b.trade_date)
            dates = [b.trade_date for b in series]
            if len(set(dates)) != len(dates):
                raise ValueError(f"{sec} 存在重复交易日行情")
            self._idx[sec] = _SecurityIndex(series)

    def securities(self) -> list[str]:
        return sorted(self._idx)

    def series(self, security_id: str) -> list[Bar]:
        idx = self._idx.get(security_id)
        return idx.series if idx else []

    def bar_on(self, security_id: str, day: dt.date) -> Bar | None:
        idx = self._idx.get(security_id)
        return idx.bar_by_date.get(day) if idx else None

    # ------------------------------------------------ 特征可见性（防前视）
    def bars_visible_to(self, security_id: str, asof: dt.datetime) -> list[Bar]:
        """决策时点 asof 前可见的行情：仅含 trade_date 当日 15:00 <= asof 的 bar。

        特征构建必须且只能使用本方法。任何试图在特征中使用未来行情的代码
        在这里就被物理隔离。
        """
        from asg_domain.timeutils import AFTERNOON_CLOSE

        cutoff_date = asof.date()
        visible: list[Bar] = []
        idx = self._idx.get(security_id)
        for b in (idx.series if idx else []):
            bar_available = dt.datetime.combine(b.trade_date, AFTERNOON_CLOSE)
            if bar_available <= asof and b.trade_date <= cutoff_date:
                visible.append(b)
        return visible

    def close_visible_to(self, security_id: str, asof: dt.datetime) -> float | None:
        vis = self.bars_visible_to(security_id, asof)
        return vis[-1].close if vis else None

    # ------------------------------------------------ 结果变量（仅回测/事件研究）
    def close_on_or_carry(self, security_id: str, day: dt.date) -> tuple[float, dt.date] | None:
        """day 当日收盘；停牌/缺失则取 day 前最近收盘（carry），返回 (价格, 实际日期)。"""
        idx = self._idx.get(security_id)
        if idx is None:
            return None
        pos = bisect.bisect_right(idx.dates, day) - 1
        if pos < 0:
            return None
        return idx.carry[pos]

    def forward_return(
        self,
        security_id: str,
        decision_day: dt.date,
        horizon_days: int,
        *,
        require_tradable_entry: bool = False,
    ) -> dict | None:
        """决策日收盘 → horizon_days 个交易日后的持有收益（结果变量专用）。

        - 决策日无 bar 或停牌 → None（停牌日不存在可参照收盘）；
        - require_tradable_entry=True 时，涨停收盘的入场同样拒绝（保守口径）；
        - 持有窗内含停牌 → carried_exit=True（出场价按 carry 口径衔接）。
        """
        idx = self._idx.get(security_id)
        if idx is None:
            return None
        entry_bar = idx.bar_by_date.get(decision_day)
        if entry_bar is None or entry_bar.suspended:
            return None
        if require_tradable_entry and entry_bar.limit_up is not None and entry_bar.close >= entry_bar.limit_up:
            return None
        tidx = idx.trading_pos.get(decision_day)
        if tidx is None:
            return None
        exit_idx = tidx + horizon_days
        if exit_idx >= len(idx.trading_days):
            return None
        exit_day = idx.trading_days[exit_idx]
        exit_pair = self.close_on_or_carry(security_id, exit_day)
        if exit_pair is None or entry_bar.close <= 0:
            return None
        exit_close, _actual_exit = exit_pair
        entry_limit_up = entry_bar.limit_up is not None and entry_bar.close >= entry_bar.limit_up
        # 持有窗 (decision_day, exit_day] 内的停牌数（前缀计数 O(1)，语义同线性扫描）
        pe = bisect.bisect_left(idx.dates, decision_day)
        px = bisect.bisect_left(idx.dates, exit_day)
        carried = (idx.suspended_before[px + 1] - idx.suspended_before[pe + 1]) > 0
        return {
            "ret": exit_close / entry_bar.close - 1.0,
            "entry_date": decision_day,
            "exit_date": exit_day,
            "carried_exit": carried,
            "limit_up_entry": entry_limit_up,
        }


def equal_weight_index(price_book: PriceBook, days: list[dt.date]) -> dict[dt.date, float]:
    """等权基准：每日全体证券收益的等权平均，净值从 1 开始。"""
    nav = 1.0
    out: dict[dt.date, float] = {}
    prev: dict[str, float] = {}
    # 初始化为首个交易日前的最近收盘
    for sec in price_book.securities():
        series = [b for b in price_book.series(sec) if not b.suspended]
        if series:
            prev[sec] = series[0].close
    first = True
    for d in days:
        rets: list[float] = []
        for sec in price_book.securities():
            bar = price_book.bar_on(sec, d)
            if bar is None or bar.suspended:
                continue
            base = prev.get(sec, bar.pre_close or bar.open)
            if base and base > 0:
                rets.append(bar.close / base - 1.0)
            prev[sec] = bar.close
        day_ret = sum(rets) / len(rets) if rets else 0.0
        if first:
            out[d] = 1.0
            first = False
        else:
            nav *= 1.0 + day_ret
            out[d] = nav
    return out


def abnormal_returns(
    event_rets: dict[str, float],
    benchmark_rets: dict[str, float],
) -> dict[str, float]:
    """按事件键对齐的异常收益：个股持有收益 − 同窗基准收益。"""
    out: dict[str, float] = {}
    for k, r in event_rets.items():
        if k in benchmark_rets:
            out[k] = r - benchmark_rets[k]
    return out


def assert_no_lookahead(feature_available_at: dt.datetime, decision_time: dt.datetime, what: str) -> None:
    """特征门控断言：不满足直接抛 AvailabilityError。"""
    if feature_available_at is None:
        raise AvailabilityError(f"{what}: available_at 缺失")
    if feature_available_at > decision_time:
        raise AvailabilityError(f"{what}: available_at={feature_available_at} 晚于决策时点 {decision_time}")
