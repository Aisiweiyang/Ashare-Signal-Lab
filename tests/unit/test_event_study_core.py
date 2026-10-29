"""事件研究核心：available_at 门控、前向收益、信号聚合、统计（分支覆盖目标 ≥85%）。"""

from __future__ import annotations

import datetime as dt

import pytest
from asg_domain.errors import AvailabilityError
from asg_event_study import (
    PriceBook,
    TextSignal,
    abnormal_returns,
    aggregate_daily_signal,
    assert_no_lookahead,
    build_observations,
    compare_signals_ic,
    equal_weight_index,
    group_stats,
    label_to_signal,
    signal_name,
    spearman_ic,
)

from tests.conftest import make_bars


class TestLabelToSignal:
    def test_fine_grained_product(self):
        assert label_to_signal({"direction": "bullish", "vs_expectation": "above", "certainty": "certain"}) == 1.2
        assert label_to_signal({"direction": "bearish", "vs_expectation": "below", "certainty": "speculative"}) == pytest.approx(-0.54)
        assert label_to_signal({"direction": "neutral", "vs_expectation": "above", "certainty": "certain"}) == 0.0

    def test_direction_3class(self):
        assert label_to_signal({"direction": "bearish"}, kind="direction_3class") == -1.0

    def test_dummy(self):
        assert label_to_signal({}, kind="dummy_majority") == 0.0

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            label_to_signal({}, kind="nope")

    def test_signal_name(self):
        assert "细粒度" in signal_name("fine_grained")
        assert signal_name("weird") == "weird"


class TestPriceBookVisibility:
    def test_visible_bars_gate_by_asof(self, mini_book: PriceBook):
        asof = dt.datetime(2026, 3, 3, 12, 0)  # 第2根bar(3/3)收盘前
        vis = mini_book.bars_visible_to("S1", asof)
        assert [b.trade_date for b in vis] == [dt.date(2026, 3, 2)]  # 3/2收盘可见

    def test_visible_bars_after_close(self, mini_book: PriceBook):
        asof = dt.datetime(2026, 3, 3, 15, 0)
        vis = mini_book.bars_visible_to("S1", asof)
        assert len(vis) == 2

    def test_close_visible_to(self, mini_book: PriceBook):
        assert mini_book.close_visible_to("S1", dt.datetime(2026, 3, 4, 15, 0)) == 11.0

    def test_unknown_security(self, mini_book: PriceBook):
        assert mini_book.series("NOPE") == []
        assert mini_book.close_visible_to("NOPE", dt.datetime(2026, 3, 9, 15, 0)) is None

    def test_duplicate_dates_rejected(self):
        bars = make_bars("S1", dt.date(2026, 3, 2), [10.0, 10.5])
        bars[1] = bars[1]
        dup = [*bars, bars[1]]
        from dataclasses import replace

        dup[-1] = replace(dup[-1], trade_date=dt.date(2026, 3, 2))
        with pytest.raises(ValueError):
            PriceBook(dup)


class TestForwardReturn:
    def test_basic_horizon1(self, mini_book: PriceBook):
        fr = mini_book.forward_return("S1", dt.date(2026, 3, 2), 1)
        assert fr is not None
        assert fr["ret"] == pytest.approx(10.5 / 10.0 - 1.0)

    def test_window_beyond_data_returns_none(self, mini_book: PriceBook):
        assert mini_book.forward_return("S1", dt.date(2026, 3, 10), 5) is None

    def test_missing_entry_day(self, mini_book: PriceBook):
        assert mini_book.forward_return("S1", dt.date(2026, 3, 15), 1) is None

    def test_suspended_entry_requires_tradable(self):
        bars = make_bars("S1", dt.date(2026, 3, 2), [10.0, 10.5, 11.0], suspended_days={dt.date(2026, 3, 3)})
        book = PriceBook(bars)
        # 停牌日不存在可参照收盘，无论开关都不可作为入场日
        assert book.forward_return("S1", dt.date(2026, 3, 3), 1, require_tradable_entry=True) is None
        assert book.forward_return("S1", dt.date(2026, 3, 3), 1) is None
        # 正常日不受影响
        assert book.forward_return("S1", dt.date(2026, 3, 2), 1) is not None

    def test_limit_up_entry_rejected_when_required(self):
        bars = make_bars("S1", dt.date(2026, 3, 2), [10.0, 11.0, 11.5])
        book = PriceBook(bars)
        # 3/3 收盘 11.0 == 涨停价 11.0
        assert book.forward_return("S1", dt.date(2026, 3, 3), 1, require_tradable_entry=True) is None
        assert book.forward_return("S1", dt.date(2026, 3, 3), 1, require_tradable_entry=False) is not None

    def test_suspended_exit_carries(self):
        bars = make_bars("S1", dt.date(2026, 3, 2), [10.0, 10.5, 11.0, 11.8], suspended_days={dt.date(2026, 3, 4)})
        book = PriceBook(bars)
        fr = book.forward_return("S1", dt.date(2026, 3, 3), 1)
        assert fr is not None
        assert fr["exit_date"] == dt.date(2026, 3, 5)  # 3/4 停牌 → 顺延到 3/5
        assert fr["carried_exit"] is True  # 窗口内含停牌，标记衔接口径


class TestAggregateSignal:
    def _sig(self, value: float, avail: dt.datetime | None, sec: str = "S1") -> TextSignal:
        return TextSignal(market_text_id=f"m{value}", security_id=sec, available_at=avail, labels={}, signal=value)

    def test_average_and_filter_by_security(self):
        sigs = [self._sig(1.0, dt.datetime(2026, 3, 2, 15, 0)), self._sig(0.5, dt.datetime(2026, 3, 2, 15, 0)), self._sig(9.9, dt.datetime(2026, 3, 2, 15, 0), sec="S2")]
        got = aggregate_daily_signal(sigs, decision_day=dt.date(2026, 3, 2), decision_time=dt.datetime(2026, 3, 2, 15, 0), security_id="S1")
        assert got == pytest.approx(0.75)

    def test_empty_returns_none(self):
        assert aggregate_daily_signal([], decision_day=dt.date(2026, 3, 2), decision_time=dt.datetime(2026, 3, 2, 15, 0), security_id="S1") is None

    def test_strict_rejects_future_availability(self):
        sigs = [self._sig(1.0, dt.datetime(2026, 3, 3, 15, 0))]
        with pytest.raises(AvailabilityError):
            aggregate_daily_signal(sigs, decision_day=dt.date(2026, 3, 2), decision_time=dt.datetime(2026, 3, 2, 15, 0), security_id="S1")

    def test_strict_rejects_missing(self):
        sigs = [self._sig(1.0, None)]
        with pytest.raises(AvailabilityError):
            aggregate_daily_signal(sigs, decision_day=dt.date(2026, 3, 2), decision_time=dt.datetime(2026, 3, 2, 15, 0), security_id="S1")

    def test_non_strict_ignores(self):
        sigs = [self._sig(1.0, dt.datetime(2026, 3, 9, 15, 0))]
        got = aggregate_daily_signal(sigs, decision_day=dt.date(2026, 3, 2), decision_time=dt.datetime(2026, 3, 2, 15, 0), security_id="S1", strict=False)
        assert got == 1.0  # exploratory 口径


class TestStats:
    def test_equal_weight_index_shape(self, mini_book: PriceBook):
        days = [dt.date(2026, 3, d) for d in range(2, 12)]
        nav = equal_weight_index(mini_book, days)
        assert nav[days[0]] == 1.0
        assert len(nav) == len(days)
        # 收益全为正 → 净值上升
        assert nav[days[-1]] > 1.0

    def test_abnormal_returns_align(self):
        ar = abnormal_returns({"a": 0.05, "b": 0.02}, {"a": 0.01, "c": 0.03})
        assert ar == {"a": pytest.approx(0.04)}

    def test_spearman_perfect(self):
        res = spearman_ic([1, 2, 3, 4], [10, 20, 30, 40])
        assert res["ic"] == pytest.approx(1.0)
        assert res["n"] == 4

    def test_spearman_inverse_and_ci(self):
        res = spearman_ic([1, 2, 3, 4, 5], [5, 4, 3, 2, 1], n_boot=500, seed=1)
        assert res["ic"] == pytest.approx(-1.0)
        assert res["ci95_high"] < 0.05

    def test_spearman_small_sample(self):
        assert spearman_ic([1], [1])["ic"] is None

    def test_spearman_length_mismatch(self):
        with pytest.raises(ValueError):
            spearman_ic([1, 2], [1])

    def test_group_stats(self):
        obs = [
            type("O", (), {"abnormal_ret": 0.01, "forward_ret": 0.02})(),
            type("O", (), {"abnormal_ret": 0.03, "forward_ret": 0.04})(),
            type("O", (), {"abnormal_ret": -0.01, "forward_ret": 0.0})(),
        ]
        stats = group_stats(obs, n_boot=300, seed=1)  # type: ignore[arg-type]
        assert stats["n"] == 3
        assert stats["mean_abnormal"] == pytest.approx(0.01, abs=1e-6)

    def test_group_stats_empty(self):
        assert group_stats([])["n"] == 0

    def test_compare_and_build_observations_flow(self, mini_book: PriceBook):
        days = [dt.date(2026, 3, d) for d in range(2, 12)]
        nav = equal_weight_index(mini_book, days)
        events = [
            {"event_key": "e1", "security_id": "S1", "decision_day": days[1], "signal": 1.2},
            {"event_key": "e2", "security_id": "S1", "decision_day": days[4], "signal": -1.0},
            {"event_key": "e3", "security_id": "S1", "decision_day": days[7], "signal": 0.6},
        ]
        obs = build_observations(price_book=mini_book, benchmark_nav=nav, events=events, horizon=1)
        assert len(obs) == 3
        cmp_res = compare_signals_ic(obs, obs, seed=1)
        assert cmp_res["delta_ic"] == pytest.approx(0.0)

    def test_assert_no_lookahead(self):
        t = dt.datetime(2026, 3, 2, 15, 0)
        assert_no_lookahead(t, t, "x")
        with pytest.raises(AvailabilityError):
            assert_no_lookahead(dt.datetime(2026, 3, 4, 0, 0), t, "y")
