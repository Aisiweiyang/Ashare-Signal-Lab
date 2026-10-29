"""asg_backtest：可成交性、成本、组合回测与时间切分（walk-forward / purged K-fold）。"""

from .costs import CostModel, sensitivity_grid
from .engine import (
    BacktestResult,
    FillRow,
    OrderIntentRow,
    portfolio_metrics,
    run_backtest,
)
from .splits import (
    Fold,
    purge_buffer_days,
    purged_kfold_folds,
    random_split_dangerous,
    split_rows_by_fold,
    split_rows_purged,
    walk_forward_folds,
)

__all__ = [
    "BacktestResult",
    "CostModel",
    "FillRow",
    "Fold",
    "OrderIntentRow",
    "portfolio_metrics",
    "purge_buffer_days",
    "purged_kfold_folds",
    "random_split_dangerous",
    "run_backtest",
    "sensitivity_grid",
    "split_rows_by_fold",
    "split_rows_purged",
    "walk_forward_folds",
]
