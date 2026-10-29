"""合成行情生成器：事件反应注入 + 涨跌停 + 停牌 + 公共市场因子。

反应注入规则（写明以便审计）：
reaction_return = market_factor + idio_noise + alpha * signal_strength(latent)

signal_strength ∈ [-1.35, +1.35]，由潜在标签决定：
  direction: bullish +1 / bearish -1 / neutral 0
  vs_expectation 乘数: above 1.2 / below 1.2 / inline 0.5 / no_expectation 0.7
  certainty 乘数: certain 1.0 / conditional 0.75 / speculative 0.45

该机制让「相对预期 × 确定性」携带真实增量信息（H1 可被诚实检验），
但噪声、市场因子与标签恢复误差可能使其在实验中不显著——这正是要如实报告的部分。
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
from asg_domain.calendar import TradingCalendar
from asg_domain.enums import Certainty, Direction, VsExpectation

from .synthetic import SYNTHETIC_SECURITIES, SyntheticEvent

VS_MULT = {
    VsExpectation.ABOVE: 1.2,
    VsExpectation.BELOW: 1.2,
    VsExpectation.INLINE: 0.5,
    VsExpectation.NO_EXPECTATION: 0.7,
    VsExpectation.UNKNOWN: 0.0,
}
CERT_MULT = {
    Certainty.CERTAIN: 1.0,
    Certainty.CONDITIONAL: 0.75,
    Certainty.SPECULATIVE: 0.45,
    Certainty.UNKNOWN: 0.0,
}


def signal_strength(latent_direction: str, latent_vs: str, latent_cert: str) -> float:
    dir_sign = {"bullish": 1.0, "bearish": -1.0}.get(latent_direction, 0.0)
    return dir_sign * VS_MULT.get(VsExpectation(latent_vs), 0.0) * CERT_MULT.get(Certainty(latent_cert), 0.0)


def decision_day_for_publish(published_at: _dt.datetime, precision: str, cal: TradingCalendar) -> _dt.date:
    """发布时间 → 首个可执行决策的交易日（与 ingest.availability_for 同一口径）。

    - datetime 精度：available_at 由 availability_from_publish 推出（盘中 → 当日 15:00；
      盘后/非交易日 → 次交易日 15:00），决策日 = available_at 当日；
    - date 或 unknown 精度：保守视为次交易日 15:00 可得（发布当天任何时刻都可能发布），
      决策日 = 次交易日。
    """
    from asg_domain.timeutils import availability_from_publish

    if precision != "datetime":
        return cal.next_trading_day(published_at.date())
    available = availability_from_publish(published_at, next_trading_day=cal.next_trading_day)
    return cal.next_trading_day(available.date(), include_self=True)


def generate_price_bars(
    *,
    seed: int,
    events: list[SyntheticEvent],
    n_days: int = 180,
    alpha: float = 0.012,
    idio_vol: float = 0.018,
    market_vol: float = 0.010,
    suspend_rate: float = 0.015,
    calendar: TradingCalendar | None = None,
) -> dict[str, list[dict]]:
    """生成后复权意义下的日行情（open/high/low/close + 涨跌停价 + 停牌标记）。

    返回 {symbol: [bar_dict...]}，bar 含 trade_date 与 reaction 信息（仅诊断字段不含标签值本身）。
    """
    cal = calendar or TradingCalendar()
    rng = np.random.default_rng(seed + 1)  # 与文本生成 seed 解耦但可复现
    start = cal.next_trading_day(_dt.date(2026, 1, 5), include_self=True)
    days = cal.trading_days(start, _dt.date(start.year + 3, 12, 31))[:n_days]

    # 每个事件的反应注入日
    inject: dict[tuple[str, _dt.date], float] = {}
    for ev in events:
        if ev.latent.direction == Direction.UNKNOWN:
            continue
        dday = decision_day_for_publish(ev.published_at, ev.published_precision, cal)
        # 决策日收盘才建仓 → 反应发生在决策日之后第一个交易日的收益中；
        # 同时部分信息在决策日盘中已被交易（惯例：跳跃打在决策日次日）。
        rday = cal.next_trading_day(dday)
        key = (ev.symbol, rday)
        s = signal_strength(ev.latent.direction.value, ev.latent.vs_expectation.value, ev.latent.certainty.value)
        # 转载与首发同簇同事件：inject 相同 key 直接叠加会放大，改为取最大（同事件不重复计）
        inject[key] = max(inject.get(key, 0.0), s)

    bars: dict[str, list[dict]] = {}
    market_ret = rng.normal(0.0002, market_vol, size=len(days))
    for sym, _ex, _name, _ind in SYNTHETIC_SECURITIES:
        price = float(rng.uniform(12, 60))
        # 每只股票自身的事件日映射
        own_inject = {d: s for (s_, d), s in inject.items() if s_ == sym}
        series: list[dict] = []
        prev_close = price
        for i, d in enumerate(days):
            suspended = bool(rng.random() < suspend_rate)
            if suspended:
                series.append(
                    {
                        "trade_date": d, "open": prev_close, "high": prev_close,
                        "low": prev_close, "close": prev_close, "pre_close": prev_close,
                        "volume": 0.0, "amount": 0.0, "suspended": True,
                        "limit_up": round(prev_close * 1.10, 2), "limit_down": round(prev_close * 0.90, 2),
                    }
                )
                continue
            jump = alpha * own_inject.get(d, 0.0)
            ret = float(market_ret[i] + rng.normal(0.0, idio_vol) + jump)
            close = max(0.5, prev_close * (1.0 + ret))
            # 主板 ±10% 涨跌停（简化：全部按 ±10%）
            limit_up = round(prev_close * 1.10, 2)
            limit_down = round(prev_close * 0.90, 2)
            close = min(max(close, limit_down), limit_up)
            close = round(close, 2)
            open_p = round(max(0.5, prev_close * (1.0 + rng.normal(0.0, 0.006))), 2)
            high = round(max(open_p, close) * (1.0 + abs(rng.normal(0.0, 0.004))), 2)
            low = round(min(open_p, close) * (1.0 - abs(rng.normal(0.0, 0.004))), 2)
            volume = float(rng.uniform(5e6, 8e7))
            series.append(
                {
                    "trade_date": d, "open": open_p, "high": high, "low": low,
                    "close": close, "pre_close": prev_close, "volume": volume,
                    "amount": volume * close, "suspended": False,
                    "limit_up": limit_up, "limit_down": limit_down,
                }
            )
            prev_close = close
        bars[sym] = series
    return bars
