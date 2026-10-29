"""交易日历（fixture 性质：内置 2024-2026 中国大陆节假日近似表，可通过 CSV 覆盖）。

注意：2026 年节假日安排以国务院当年公布为准，内置表为编写时点的近似 fixture，
正式研究必须通过 `TradingCalendar.load_csv` 载入交易所官方日历（见 DATA_CARD.md）。
"""

from __future__ import annotations

import csv
from datetime import date
from functools import lru_cache
from pathlib import Path

from .errors import CalendarError
from .timeutils import date_range

# 近似节假日表（非交易日；周末单独按 weekday 处理）
BUILTIN_CN_HOLIDAYS: frozenset[date] = frozenset(
    date.fromisoformat(d)
    for d in (
        # 2024
        "2024-01-01",
        "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15",
        "2024-02-16",
        "2024-04-04", "2024-04-05",
        "2024-05-01", "2024-05-02", "2024-05-03",
        "2024-06-10",
        "2024-09-16", "2024-09-17",
        "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-07",
        # 2025
        "2025-01-01", "2025-01-31",
        "2025-02-03", "2025-02-04",
        "2025-04-04",
        "2025-05-01", "2025-05-02", "2025-05-05",
        "2025-06-02",
        "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07",
        "2025-10-08",
        # 2026（近似，见模块 docstring）
        "2026-01-01", "2026-01-02",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
        "2026-04-06",
        "2026-05-01", "2026-05-04", "2026-05-05",
        "2026-06-19",
        "2026-09-25",
        "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
    )
)


class TradingCalendar:
    """交易日历：weekday 未到周末且不在节假日集合中即为交易日。

    只回答「某日期是否交易日 / 上下一个交易日 / 区间内交易日序列」；
    不回答交易时段（那是 timeutils 的事）。
    """

    def __init__(self, holidays: frozenset[date] = BUILTIN_CN_HOLIDAYS):
        self._holidays = holidays

    @classmethod
    def load_csv(cls, path: str | Path, column: str = "holiday_date") -> TradingCalendar:
        """从 CSV（一列 ISO 日期）加载节假日；文件不存在抛 CalendarError。"""
        p = Path(path)
        if not p.exists():
            raise CalendarError(f"节假日 CSV 不存在: {p}")
        days: set[date] = set()
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            for i, row in enumerate(csv.DictReader(f)):
                raw = (row.get(column) or "").strip()
                if not raw:
                    continue
                try:
                    days.add(date.fromisoformat(raw))
                except ValueError as e:
                    raise CalendarError(f"节假日 CSV 第 {i + 2} 行日期非法: {raw!r}") from e
        return cls(frozenset(days))

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self._holidays

    def next_trading_day(self, d: date, *, include_self: bool = False) -> date:
        cur = d if include_self else date.fromordinal(d.toordinal() + 1)
        for _ in range(40):  # 最长约 40 天连续假期兜底，防止死循环
            if self.is_trading_day(cur):
                return cur
            cur = date.fromordinal(cur.toordinal() + 1)
        raise CalendarError(f"40 天内找不到交易日，日历数据异常（起始 {d}）")

    def prev_trading_day(self, d: date, *, include_self: bool = False) -> date:
        cur = d if include_self else date.fromordinal(d.toordinal() - 1)
        for _ in range(40):
            if self.is_trading_day(cur):
                return cur
            cur = date.fromordinal(cur.toordinal() - 1)
        raise CalendarError(f"40 天内找不到交易日，日历数据异常（起始 {d}）")

    def trading_days(self, start: date, end: date) -> list[date]:
        if start > end:
            return []
        return [d for d in date_range(start, end) if self.is_trading_day(d)]

    def offset_trading_days(self, d: date, offset: int) -> date:
        """d 之后（offset>0）第 offset 个交易日；offset<0 向前。offset=0 返回 d 本身（若非交易日则抛错）。"""
        if offset == 0:
            if not self.is_trading_day(d):
                raise CalendarError(f"{d} 不是交易日")
            return d
        cur = d
        step = 1 if offset > 0 else -1
        for _ in range(abs(offset)):
            cur = self.next_trading_day(cur) if step > 0 else self.prev_trading_day(cur)
        return cur


@lru_cache(maxsize=8)
def default_calendar() -> TradingCalendar:
    return TradingCalendar()
