"""时间语义（ADR-003）。

约定：全系统 DB 与内存中的 naive datetime 一律表示东八区（Asia/Shanghai，无夏令时）。
跨时区数据必须在 connector 入口归一。任何 aware datetime 进入领域层前先转 naive 东八区。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

CN_TZ_OFFSET = timedelta(hours=8)

MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)


def ensure_naive_cn(dt: datetime) -> datetime:
    """把 aware datetime 归一为东八区 naive；naive 原样返回（视为已是东八区）。"""
    if dt.tzinfo is not None:
        return _to_cn_naive(dt)
    return dt


def _to_cn_naive(dt: datetime) -> datetime:
    from datetime import timezone

    return dt.astimezone(timezone(CN_TZ_OFFSET)).replace(tzinfo=None)


def market_close(dt: datetime) -> datetime:
    """该自然日的 15:00 收盘时刻（是否交易日由日历层判断）。"""
    return datetime.combine(dt.date(), AFTERNOON_CLOSE)


def availability_from_publish(
    published_at: datetime,
    *,
    next_trading_day: callable | None = None,
) -> datetime:
    """由发布时间推 available_at。

    规则（保守）：
    - 交易日 15:00 收盘前发布 → 当日 15:00 起（收盘后即可用于次日决策）；
    - 交易日 15:00 后发布 / 周末 / 节假日发布 → 下一交易日 15:00。

    next_trading_day: 接收 date 返回下一交易日 date 的回调（由 TradingCalendar 提供）。
    未提供且需要跨日时抛 CalendarError，避免调用方忘记接日历。
    """
    from .errors import CalendarError

    d = published_at.date()
    weekday_ok = d.weekday() < 5
    if weekday_ok and published_at.time() <= AFTERNOON_CLOSE:
        return market_close(published_at)
    if next_trading_day is None:
        raise CalendarError(
            "published_at 需要跨日推断 available_at，但未提供交易日历回调"
        )
    nxt = next_trading_day(d)
    return datetime.combine(nxt, AFTERNOON_CLOSE)


def decision_time_for_event_day(event_day: date) -> datetime:
    """事件日可执行交易的决策时点：当日 15:00 收盘（与 available_at 的
    「当日 15:00 收盘可得」约定一致；成交按收盘价模拟）。

    信号仅能使用 available_at <= decision_time 的文本；盘后才可得的文本
    自动落到次一交易日决策。
    """
    return datetime.combine(event_day, AFTERNOON_CLOSE)


def add_days(d: date, days: int) -> date:
    return d + timedelta(days=days)


def date_range(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)
