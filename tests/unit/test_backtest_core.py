"""回测核心：成本、可成交性、组合指标、walk-forward 切分（分支覆盖目标 ≥85%）。"""

from __future__ import annotations

import datetime as dt

import pytest
from asg_backtest import (
    CostModel,
    portfolio_metrics,
    run_backtest,
    sensitivity_grid,
    split_rows_by_fold,
    walk_forward_folds,
)
from asg_domain.constants import DEFAULT_COSTS
from asg_domain.enums import FillStatus
from asg_domain.errors import AshareSignalError
from asg_event_study import Bar, PriceBook

from tests.conftest import make_bars


class TestCostModel:
    def test_defaults_registered(self):
        cm = CostModel()
        assert cm.commission_bps == DEFAULT_COSTS["commission_bps"]
        assert cm.stamp_duty_bps == DEFAULT_COSTS["stamp_duty_bps"]

    def test_buy_sell_asymmetry(self):
        cm = CostModel(min_commission_cny=0.0)
        buy = cm.buy_cost(1_000_000)
        sell = cm.sell_cost(1_000_000)
        assert buy["stamp_duty"] == 0.0
        assert sell["stamp_duty"] > 0
        assert sell["total"] > buy["total"]

    def test_min_commission(self):
        cm = CostModel(min_commission_cny=5.0)
        small = cm.buy_cost(100)
        assert small["commission"] == 5.0

    def test_zero_amount(self):
        cm = CostModel()
        assert cm.buy_cost(0)["total"] == 0.0

    def test_slippage_price_adjustment(self):
        cm = CostModel(slippage_bps=10)
        assert cm.effective_entry_price(10.0) == pytest.approx(10.01)
        assert cm.effective_exit_price(10.0) == pytest.approx(9.99)

    def test_from_config_override(self):
        cm = CostModel.from_config({"slippage_bps": 33.0})
        assert cm.slippage_bps == 33.0

    def test_sensitivity_grid(self):
        grid = sensitivity_grid()
        assert [c.slippage_bps for c in grid] == [5.0, 10.0, 20.0, 50.0]


class TestTradability:
    def test_limit_up_buy_rejected(self):
        # close == limit_up → 涨停不可买
        bars = make_bars("S1", dt.date(2026, 3, 2), [10.0, 11.0])
        book = PriceBook(bars)
        res = run_backtest(price_book=book, signal_rows=[{"security_id": "S1", "decision_day": dt.date(2026, 3, 3), "signal": 1.0}])
        assert any(f.status == FillStatus.REJECTED_LIMIT_UP for f in res.fills)

    def test_limit_down_sell_extends_or_rejects(self):
        # 收盘跌停 → 卖出被拒/顺延（此处只有两根bar，顺延无路 → REJECTED_LIMIT_DOWN）
        bars = make_bars("S1", dt.date(2026, 3, 2), [10.0, 9.0])
        bars[1] = Bar("S1", dt.date(2026, 3, 3), 9.0, 9.2, 8.9, 9.0, 10.0, round(10.0 * 1.1, 2), round(10.0 * 0.9, 2), False)
        book = PriceBook(bars)
        res = run_backtest(price_book=book, signal_rows=[{"security_id": "S1", "decision_day": dt.date(2026, 3, 2), "signal": 1.0}])
        assert res.fills and res.fills[0].status in (FillStatus.REJECTED_LIMIT_DOWN, FillStatus.REJECTED_NO_BAR)

    def test_suspended_entry_rejected(self):
        bars = make_bars("S1", dt.date(2026, 3, 2), [10.0, 10.2], suspended_days={dt.date(2026, 3, 3)})
        book = PriceBook(bars)
        res = run_backtest(price_book=book, signal_rows=[{"security_id": "S1", "decision_day": dt.date(2026, 3, 3), "signal": 1.0}])
        assert any(f.status == FillStatus.REJECTED_SUSPENDED for f in res.fills)

    def test_missing_bar_rejected(self, mini_book: PriceBook):
        res = run_backtest(price_book=mini_book, signal_rows=[{"security_id": "NOPE", "decision_day": dt.date(2026, 3, 3), "signal": 1.0}])
        assert any(f.status == FillStatus.REJECTED_NO_BAR for f in res.fills)


class TestBacktestEngine:
    def test_positive_trend_profit_before_costs(self, mini_book: PriceBook):
        rows = [{"security_id": "S1", "decision_day": dt.date(2026, 3, 2), "signal": 1.2, "available_at_used": dt.datetime(2026, 3, 1, 15, 0)}]
        res = run_backtest(price_book=mini_book, signal_rows=rows)
        assert res.metrics["n_fills"] == 1
        assert res.metrics["total_return"] > 0
        assert res.metrics["costs_total"] > 0

    def test_small_signal_filtered(self, mini_book: PriceBook):
        rows = [{"security_id": "S1", "decision_day": dt.date(2026, 3, 2), "signal": 0.01}]
        res = run_backtest(price_book=mini_book, signal_rows=rows, min_abs_signal=0.05)
        assert res.metrics["n_fills"] == 0
        assert res.metrics["n_days"] == 0

    def test_long_only_skips_negative(self, mini_book: PriceBook):
        rows = [{"security_id": "S1", "decision_day": dt.date(2026, 3, 2), "signal": -1.0}]
        res = run_backtest(price_book=mini_book, signal_rows=rows, long_only=True)
        assert res.order_intents == []

    def test_short_mirror_when_allowed(self, mini_book: PriceBook):
        rows = [{"security_id": "S1", "decision_day": dt.date(2026, 3, 2), "signal": -1.2}]
        long_res = run_backtest(price_book=mini_book, signal_rows=[{"security_id": "S1", "decision_day": dt.date(2026, 3, 2), "signal": 1.2}], long_only=False)
        short_res = run_backtest(price_book=mini_book, signal_rows=rows, long_only=False)
        assert long_res.metrics["total_return"] > 0
        assert short_res.metrics["total_return"] < 0

    def test_top_k_and_intents_persisted(self):
        # 两只证券同日信号，top_k=1 只成交一只
        bars = make_bars("A1", dt.date(2026, 3, 2), [10.0, 10.5, 11.0]) + make_bars("B2", dt.date(2026, 3, 2), [20.0, 20.1, 20.2])
        book = PriceBook(bars)
        rows = [
            {"security_id": "A1", "decision_day": dt.date(2026, 3, 2), "signal": 1.2},
            {"security_id": "B2", "decision_day": dt.date(2026, 3, 2), "signal": 1.0},
        ]
        res = run_backtest(price_book=book, signal_rows=rows, top_k=1)
        assert len(res.order_intents) == 1
        assert res.order_intents[0].security_id == "A1"

    def test_cost_sensitivity_changes_result(self, mini_book: PriceBook):
        rows = [{"security_id": "S1", "decision_day": dt.date(2026, 3, 2), "signal": 1.2}]
        cheap = run_backtest(price_book=mini_book, signal_rows=rows, cost_model=CostModel(slippage_bps=1))
        pricey = run_backtest(price_book=mini_book, signal_rows=rows, cost_model=CostModel(slippage_bps=100))
        assert cheap.metrics["total_return"] > pricey.metrics["total_return"]

    def test_nav_curve_dates(self, mini_book: PriceBook):
        rows = [{"security_id": "S1", "decision_day": dt.date(2026, 3, 3), "signal": 1.2}]
        res = run_backtest(price_book=mini_book, signal_rows=rows)
        assert res.nav_curve and res.nav_curve[0][0] > dt.date(2026, 3, 3)


class TestPortfolioMetrics:
    def test_empty(self):
        m = portfolio_metrics([], 1_000_000.0, 1_000_000.0, 0, 0.0, [])
        assert m["n_days"] == 0 and m["sharpe"] is None

    def test_sharpe_and_mdd(self):
        rets = [0.01, -0.005, 0.008, 0.012, -0.002]
        nav = 1_000_000.0
        for r in rets:
            nav *= 1 + r
        m = portfolio_metrics(rets, 1_000_000.0, nav, 5, 100.0, [])
        assert m["sharpe"] is not None
        assert 0 < m["max_drawdown"] < 0.01
        assert m["win_rate_days"] == 0.6
        assert m["n_fills"] == 5

    def test_zero_variance_sharpe_none(self):
        m = portfolio_metrics([0.0, 0.0], 100.0, 100.0, 0, 0.0, [])
        assert m["sharpe"] is None


class TestWalkForward:
    def _days(self, n: int, start: dt.date | None = None) -> list[dt.date]:
        start = start or dt.date(2026, 1, 5)
        return [start + dt.timedelta(days=i) for i in range(n)]

    def test_folds_ordered_and_embargo(self):
        days = self._days(60)
        folds = walk_forward_folds(event_days=days, n_folds=3, embargo_days=2)
        assert len(folds) >= 2
        for f in folds:
            assert f.train_end < f.test_start  # embargo 保证间隔
            assert f.test_start <= f.test_end
        # 时间上不回看：train_end 早于 test_end
        assert folds[0].test_end <= folds[-1].test_end

    def test_insufficient_days_raises(self):
        with pytest.raises(AshareSignalError):
            walk_forward_folds(event_days=self._days(4), n_folds=3)

    def test_split_rows_and_cluster_leak(self):
        days = self._days(30)
        folds = walk_forward_folds(event_days=days, n_folds=2, embargo_days=1)
        rows = [
            {"decision_day": days[3], "cluster_id": "c1"},
            {"decision_day": days[4], "cluster_id": "c1"},  # 同簇不同日
            {"decision_day": days[20], "cluster_id": "c2"},
        ]
        f = folds[-1]
        train, test = split_rows_by_fold(rows, f)
        assert all(r["cluster_id"] != "c2" or r in test for r in train)
        # 强制把同簇放进两侧 → 泄漏被拒
        bad_rows = [
            {"decision_day": f.train_start, "cluster_id": "cx"},
            {"decision_day": f.test_start, "cluster_id": "cx"},
        ]
        with pytest.raises(AshareSignalError, match="泄漏"):
            split_rows_by_fold(bad_rows, f)

    def test_random_split_dangerous_is_shuffled(self):
        rows = [{"i": i} for i in range(20)]
        a, b = random_split(rows, seed=3)
        assert len(a) == 10 and len(b) == 10
        assert {r["i"] for r in a + b} == set(range(20))


def random_split(rows, *, seed: int):
    from asg_backtest import random_split_dangerous

    return random_split_dangerous(rows, seed=seed)
