"""带成本回测引擎：信号 → 订单意图 → 可成交性检查 → 成交模拟 → 组合指标。

交易规则（A 股约束，均有专项测试）：
- 涨停价收盘不可买入、跌停价收盘不可卖出（价格触板即按拒单处理，保守口径）；
- 停牌不可开仓/平仓（平仓顺延至下一可交易日）；
- T+1：当日买入当日不可卖——本引擎持仓期 ≥ 1 个交易日，天然满足；
- 入场价 = 决策日收盘（严格 available_at 门控后）×（1+滑点）；出场同理向下。
- 多头、等权、全仓换仓；无杠杆。
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from asg_domain.constants import LIMIT_PRICE_EPS
from asg_domain.enums import FillStatus
from asg_event_study.returns import PriceBook

from asg_backtest.costs import CostModel


@dataclass
class OrderIntentRow:
    trade_date: dt.date
    security_id: str
    side: str  # buy / sell
    intended_qty: float
    signal_value: float
    available_at_used: dt.datetime | None = None


@dataclass
class FillRow:
    intent: OrderIntentRow
    status: FillStatus
    fill_price: float | None = None
    fill_qty: float = 0.0
    costs: dict = field(default_factory=dict)
    reason: str = ""


@dataclass
class BacktestResult:
    nav_curve: list[tuple[dt.date, float]]
    daily_returns: list[float]
    order_intents: list[OrderIntentRow]
    fills: list[FillRow]
    metrics: dict
    cost_grid_note: str = ""


def _tradability_for_entry(book: PriceBook, security_id: str, day: dt.date) -> tuple[FillStatus | None, float | None]:
    bar = book.bar_on(security_id, day)
    if bar is None:
        return FillStatus.REJECTED_NO_BAR, None
    if bar.suspended:
        return FillStatus.REJECTED_SUSPENDED, None
    if bar.limit_up is not None and bar.close >= bar.limit_up - LIMIT_PRICE_EPS:
        return FillStatus.REJECTED_LIMIT_UP, None
    return None, bar.close


def _exit_day_and_status(book: PriceBook, security_id: str, entry_day: dt.date, horizon: int, *, max_carry: int = 10) -> tuple[dt.date | None, FillStatus | None, float | None]:
    """出场日：entry 后第 horizon 个可交易日；若当日跌停/停牌则顺延（最多 max_carry 个交易日）。"""
    series = [b for b in book.series(security_id) if not b.suspended and b.trade_date > entry_day]
    if len(series) < horizon:
        return None, FillStatus.REJECTED_NO_BAR, None
    candidate = series[horizon - 1]
    carry = 0
    while carry < max_carry:
        if candidate.limit_down is not None and candidate.close <= candidate.limit_down + LIMIT_PRICE_EPS:
            # 跌停无法卖出，顺延到下一可交易日
            nxt = [b for b in series if b.trade_date > candidate.trade_date]
            if not nxt:
                return None, FillStatus.REJECTED_NO_BAR, None
            candidate = nxt[0]
            carry += 1
            continue
        return candidate.trade_date, None, candidate.close
    return None, FillStatus.REJECTED_LIMIT_DOWN, None


def run_backtest(
    *,
    price_book: PriceBook,
    signal_rows: list[dict],
    cost_model: CostModel | None = None,
    initial_capital: float = 1_000_000.0,
    horizon: int = 1,
    top_k: int = 3,
    min_abs_signal: float = 0.05,
    long_only: bool = True,
    seed: int = 0,
) -> BacktestResult:
    """信号行: {security_id, decision_day, signal, available_at_used?}。

    规则：每个决策日，取 |signal| 排名前 top_k 且 |signal| >= min_abs_signal 的证券；
    信号 > 0 买入持有 horizon 个交易日；signal < 0 在 long_only 时跳过（记录为 skip），
    非 long_only 时按反向持仓处理（简化为做空收益镜像，仅研究口径）。
    组合按「每个事件独立一等份资金」近似（等权事件组合），资金约束用 total_slots 缓解。
    """
    cm = cost_model or CostModel()
    intents: list[OrderIntentRow] = []
    fills: list[FillRow] = []
    pnl_events: list[tuple[dt.date, float, dict]] = []  # (exit_day, event_pnl_ratio, costs)
    slot_capital = initial_capital / max(top_k, 1)

    # 按决策日分组
    by_day: dict[dt.date, list[dict]] = {}
    for row in signal_rows:
        by_day.setdefault(row["decision_day"], []).append(row)

    for day in sorted(by_day):
        rows = [r for r in by_day[day] if abs(r["signal"]) >= min_abs_signal]
        rows.sort(key=lambda r: abs(r["signal"]), reverse=True)
        for r in rows[:top_k]:
            sec = r["security_id"]
            sig = float(r["signal"])
            if long_only and sig <= 0:
                continue
            intents.append(
                OrderIntentRow(
                    trade_date=day,
                    security_id=sec,
                    side="buy" if sig > 0 else "short",
                    intended_qty=slot_capital,
                    signal_value=sig,
                    available_at_used=r.get("available_at_used"),
                )
            )
            status, close = _tradability_for_entry(price_book, sec, day)
            if status is not None:
                fills.append(FillRow(intent=intents[-1], status=status, reason="entry 不可成交"))
                continue
            entry_price = cm.effective_entry_price(close)
            exit_day, exit_status, exit_close = _exit_day_and_status(price_book, sec, day, horizon)
            if exit_status is not None or exit_day is None or exit_close is None:
                fills.append(
                    FillRow(
                        intent=intents[-1],
                        status=exit_status or FillStatus.REJECTED_NO_BAR,
                        fill_price=entry_price,
                        reason="exit 不可成交",
                    )
                )
                continue
            exit_price = cm.effective_exit_price(exit_close)
            gross_ratio = exit_price / entry_price - 1.0
            if intents[-1].side == "short":
                gross_ratio = -gross_ratio
            entry_amount = slot_capital
            exit_amount = slot_capital * (1.0 + gross_ratio)
            buy_costs = cm.buy_cost(entry_amount)
            sell_costs = cm.sell_cost(abs(exit_amount))
            total_costs = round(buy_costs["total"] + sell_costs["total"], 2)
            net_ratio = (exit_amount - sell_costs["total"]) / (entry_amount + buy_costs["total"]) - 1.0
            fills.append(
                FillRow(
                    intent=intents[-1],
                    status=FillStatus.FILLED,
                    fill_price=entry_price,
                    fill_qty=slot_capital,
                    costs={**buy_costs, "sell_total": sell_costs["total"]},
                    reason="ok",
                )
            )
            pnl_events.append((exit_day, net_ratio, {"total": total_costs, "buy": buy_costs, "sell": sell_costs}))

    # 组合净值：事件在出场日结算（资金复用近似：按日聚合事件收益等权）
    pnl_by_day: dict[dt.date, list[float]] = {}
    costs_total = 0.0
    for exit_day, ratio, c in pnl_events:
        pnl_by_day.setdefault(exit_day, []).append(ratio)
        costs_total += c["total"]
    all_days = sorted(pnl_by_day)
    nav = initial_capital
    nav_curve: list[tuple[dt.date, float]] = []
    daily_rets: list[float] = []
    for d in all_days:
        day_ret = sum(pnl_by_day[d]) / len(pnl_by_day[d])
        nav *= 1.0 + day_ret
        nav_curve.append((d, round(nav, 2)))
        daily_rets.append(day_ret)

    metrics = portfolio_metrics(daily_rets, initial_capital, nav, len(fills), costs_total, intents)
    return BacktestResult(
        nav_curve=nav_curve,
        daily_returns=daily_rets,
        order_intents=intents,
        fills=fills,
        metrics=metrics,
    )


def portfolio_metrics(
    daily_rets: list[float],
    initial_capital: float,
    final_nav: float,
    n_fills: int,
    costs_total: float,
    intents: list[OrderIntentRow],
) -> dict:
    n = len(daily_rets)
    if n == 0:
        return {"n_days": 0, "total_return": 0.0, "sharpe": None, "max_drawdown": 0.0, "n_fills": n_fills, "costs_total": round(costs_total, 2)}
    total_return = final_nav / initial_capital - 1.0
    mean = sum(daily_rets) / n
    var = sum((r - mean) ** 2 for r in daily_rets) / max(n - 1, 1)
    std = math.sqrt(var)
    sharpe = (mean / std) * math.sqrt(252) if std > 0 else None
    # 最大回撤（基于日收益序列）
    nav = 1.0
    peak = 1.0
    mdd = 0.0
    for r in daily_rets:
        nav *= 1.0 + r
        peak = max(peak, nav)
        mdd = max(mdd, 1.0 - nav / peak)
    n_filled = n_fills
    turnover = n_filled / max(n, 1)
    wins = sum(1 for r in daily_rets if r > 0)
    return {
        "n_days": n,
        "total_return": round(total_return, 6),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "max_drawdown": round(mdd, 6),
        "win_rate_days": round(wins / n, 4),
        "turnover_per_day": round(turnover, 3),
        "n_fills": n_filled,
        "costs_total": round(costs_total, 2),
    }
