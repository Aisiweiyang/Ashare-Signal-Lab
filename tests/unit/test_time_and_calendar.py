"""时间语义与交易日历测试（核心时间模块，分支覆盖目标 ≥85%）。"""

from __future__ import annotations

import datetime as dt

import pytest
from asg_domain.calendar import BUILTIN_CN_HOLIDAYS, TradingCalendar
from asg_domain.errors import AvailabilityError, CalendarError
from asg_domain.timeutils import (
    availability_from_publish,
    decision_time_for_event_day,
    ensure_naive_cn,
    market_close,
)


class TestTradingCalendar:
    def test_weekday_is_trading(self):
        cal = TradingCalendar()
        assert cal.is_trading_day(dt.date(2026, 3, 2)) is True  # 周一
        assert cal.is_trading_day(dt.date(2026, 3, 7)) is False  # 周六
        assert cal.is_trading_day(dt.date(2026, 3, 8)) is False  # 周日

    def test_holiday_excluded(self):
        cal = TradingCalendar()
        assert cal.is_trading_day(dt.date(2026, 1, 1)) is False  # 元旦
        assert cal.is_trading_day(dt.date(2026, 2, 17)) is False  # 春节近似表内

    def test_next_trading_day_across_weekend(self):
        cal = TradingCalendar()
        nxt = cal.next_trading_day(dt.date(2026, 3, 6))  # 周五
        assert nxt == dt.date(2026, 3, 9)  # 周一

    def test_next_trading_day_across_holiday(self):
        cal = TradingCalendar()
        nxt = cal.next_trading_day(dt.date(2025, 12, 31))  # -> 2026 元旦假期
        assert nxt == dt.date(2026, 1, 5)  # 1/1-1/2 假 + 1/3-1/4 周末

    def test_prev_trading_day_include_self(self):
        cal = TradingCalendar()
        assert cal.prev_trading_day(dt.date(2026, 3, 9), include_self=True) == dt.date(2026, 3, 9)
        assert cal.prev_trading_day(dt.date(2026, 3, 9)) == dt.date(2026, 3, 6)

    def test_offset_trading_days(self):
        cal = TradingCalendar()
        days = cal.trading_days(dt.date(2026, 3, 2), dt.date(2026, 3, 13))
        assert cal.offset_trading_days(days[0], 3) == days[3]
        assert cal.offset_trading_days(days[3], -3) == days[0]
        with pytest.raises(CalendarError):
            cal.offset_trading_days(dt.date(2026, 3, 7), 0)  # 非交易日

    def test_trading_days_range_and_empty(self):
        cal = TradingCalendar()
        assert cal.trading_days(dt.date(2026, 3, 8), dt.date(2026, 3, 7)) == []
        days = cal.trading_days(dt.date(2026, 3, 6), dt.date(2026, 3, 9))
        assert days == [dt.date(2026, 3, 6), dt.date(2026, 3, 9)]

    def test_load_csv_and_missing(self, tmp_path):
        p = tmp_path / "hol.csv"
        p.write_text("holiday_date\n2026-12-25\n2026-12-28\n", encoding="utf-8")
        cal = TradingCalendar.load_csv(p)
        assert cal.is_trading_day(dt.date(2026, 12, 25)) is False
        assert cal.is_trading_day(dt.date(2026, 12, 24)) is True
        with pytest.raises(CalendarError):
            TradingCalendar.load_csv(tmp_path / "missing.csv")

    def test_load_csv_bad_date(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("holiday_date\nnot-a-date\n", encoding="utf-8")
        with pytest.raises(CalendarError):
            TradingCalendar.load_csv(p)

    def test_builtin_table_not_empty(self):
        assert len(BUILTIN_CN_HOLIDAYS) > 30


class TestAvailability:
    def test_intraday_publish_same_day_close(self):
        pub = dt.datetime(2026, 3, 2, 10, 30)
        avail = availability_from_publish(pub, next_trading_day=TradingCalendar().next_trading_day)
        assert avail == dt.datetime(2026, 3, 2, 15, 0)

    def test_publish_at_close_boundary(self):
        pub = dt.datetime(2026, 3, 2, 15, 0)
        avail = availability_from_publish(pub, next_trading_day=TradingCalendar().next_trading_day)
        assert avail == dt.datetime(2026, 3, 2, 15, 0)  # <=15:00 算当日

    def test_after_close_next_trading_day(self):
        cal = TradingCalendar()
        pub = dt.datetime(2026, 3, 6, 19, 0)  # 周五晚
        avail = availability_from_publish(pub, next_trading_day=cal.next_trading_day)
        assert avail == dt.datetime(2026, 3, 9, 15, 0)  # 下周一

    def test_weekend_publish(self):
        cal = TradingCalendar()
        pub = dt.datetime(2026, 3, 7, 9, 0)  # 周六
        avail = availability_from_publish(pub, next_trading_day=cal.next_trading_day)
        assert avail == dt.datetime(2026, 3, 9, 15, 0)

    def test_needs_calendar_callback(self):
        pub = dt.datetime(2026, 3, 6, 19, 0)
        with pytest.raises(CalendarError):
            availability_from_publish(pub, next_trading_day=None)

    def test_decision_time_is_close(self):
        assert decision_time_for_event_day(dt.date(2026, 3, 2)) == dt.datetime(2026, 3, 2, 15, 0)
        assert market_close(dt.datetime(2026, 3, 2, 12, 0)) == dt.datetime(2026, 3, 2, 15, 0)


class TestTimezone:
    def test_aware_converted_to_cn_naive(self):

        utc = dt.UTC
        aware = dt.datetime(2026, 3, 2, 2, 30, tzinfo=utc)  # 北京时间 10:30
        naive = ensure_naive_cn(aware)
        assert naive == dt.datetime(2026, 3, 2, 10, 30)
        assert naive.tzinfo is None

    def test_naive_passthrough(self):
        n = dt.datetime(2026, 3, 2, 10, 30)
        assert ensure_naive_cn(n) is n

    def test_availability_error_is_raiseable(self):
        with pytest.raises(AvailabilityError):
            raise AvailabilityError("x")
