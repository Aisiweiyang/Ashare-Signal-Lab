"""akshare 适配器：默认停用、可选依赖缺失降级、DataFrame 归一化（全部无网络）。"""

from __future__ import annotations

import datetime as dt
import sys

import pytest
from asg_connectors.akshare_market import AkshareDailyBarsConnector
from asg_domain.errors import ConnectorError, ConnectorUnavailableError


class _FakeDF:
    """最小 DataFrame 替身：columns + to_dict("records")，避免测试依赖 pandas。"""

    def __init__(self, records: list[dict]):
        self._records = records
        self.columns = list(records[0].keys()) if records else []

    def __len__(self):
        return len(self._records)

    def to_dict(self, _orient: str) -> list[dict]:
        return [dict(r) for r in self._records]


def _fake_akshare_module(records_by_symbol: dict[str, list[dict]]):
    class _FakeAk:
        @staticmethod
        def stock_zh_a_hist(symbol, period, start_date, end_date, adjust):
            assert period == "daily" and adjust == "qfq"
            return _FakeDF(records_by_symbol.get(symbol, []))

    return _FakeAk()


def _records():
    return [
        {
            "日期": "2026-03-02",
            "开盘": 10.0,
            "收盘": 10.5,
            "最高": 10.8,
            "最低": 9.9,
            "成交量": 1000,
            "成交额": 10500.0,
            "涨跌额": 0.5,
        },
        {
            "日期": "2026-03-03",
            "开盘": 10.5,
            "收盘": 10.5,
            "最高": 10.5,
            "最低": 10.5,
            "成交量": 0,
            "成交额": 0.0,
            "涨跌额": 0.0,
        },
        {
            "日期": "2026-03-04",
            "开盘": 10.5,
            "收盘": 11.55,
            "最高": 11.6,
            "最低": 10.4,
            "成交量": 2000,
            "成交额": 23100.0,
            "涨跌额": 1.05,
        },
    ]


class TestDisabledByDefault:
    def test_default_off_blocks_fetch(self, monkeypatch):
        monkeypatch.delenv("ASG_AKSHARE_ENABLED", raising=False)
        c = AkshareDailyBarsConnector()
        with pytest.raises(ConnectorUnavailableError, match="默认停用"):
            c.fetch_price_bars("600000", start=dt.date(2026, 3, 1), end=dt.date(2026, 3, 31))

    def test_env_gate_does_not_override_explicit_false(self, monkeypatch):
        monkeypatch.setenv("ASG_AKSHARE_ENABLED", "true")
        c = AkshareDailyBarsConnector(enabled=False)
        assert c.meta.enabled is False

    def test_env_gate_enables(self, monkeypatch):
        monkeypatch.setenv("ASG_AKSHARE_ENABLED", "1")
        assert AkshareDailyBarsConnector().meta.enabled is True


class TestOptionalDependency:
    def test_missing_akshare_degrades_with_hint(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", None)  # import → ImportError
        c = AkshareDailyBarsConnector(enabled=True, min_interval_seconds=0.0)
        with pytest.raises(ConnectorUnavailableError, match="extra market"):
            c.fetch_price_bars("600000", start=dt.date(2026, 3, 1), end=dt.date(2026, 3, 31))


class TestNormalization:
    def _connector(self) -> AkshareDailyBarsConnector:
        return AkshareDailyBarsConnector(enabled=True, min_interval_seconds=0.0)

    def test_bar_mapping_limits_and_suspension(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", _fake_akshare_module({"600000": _records()}))
        bars = self._connector().fetch_price_bars(
            "600000", start=dt.date(2026, 3, 1), end=dt.date(2026, 3, 31)
        )
        assert [b.trade_date for b in bars] == [
            dt.date(2026, 3, 2),
            dt.date(2026, 3, 3),
            dt.date(2026, 3, 4),
        ]
        first, suspend_day, third = bars
        # 首行前收 = 收盘 - 涨跌额
        assert first.pre_close == pytest.approx(10.0)
        # ±10% 涨跌停按前收计算并保留两位
        assert first.limit_up == pytest.approx(11.0)
        assert first.limit_down == pytest.approx(9.0)
        # 零成交量 → 如实标记停牌
        assert suspend_day.suspended is True
        assert third.suspended is False
        # 前收链：次日前收 = 前日收盘
        assert third.pre_close == pytest.approx(suspend_day.close)

    def test_missing_core_column_raises(self):
        c = self._connector()
        bad = _FakeDF([{"日期": "2026-03-02", "开盘": 10.0, "收盘": 10.5}])  # 缺最高/最低
        with pytest.raises(ConnectorError, match="核心列"):
            c.to_market_bars("600000", bad)

    def test_empty_frame_returns_empty(self):
        c = self._connector()
        assert c.to_market_bars("600000", _FakeDF([])) == []
        assert c.to_market_bars("600000", None) == []

    def test_invalid_range_rejected(self):
        c = self._connector()
        with pytest.raises(ValueError, match="日期区间"):
            c.fetch_price_bars("600000", start=dt.date(2026, 3, 31), end=dt.date(2026, 3, 1))
