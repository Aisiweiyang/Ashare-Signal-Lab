"""asg_event_study：available_at 门控收益、细粒度信号、事件研究与统计推断。"""

from .returns import (
    Bar,
    PriceBook,
    abnormal_returns,
    assert_no_lookahead,
    equal_weight_index,
)
from .signals import TextSignal, aggregate_daily_signal, label_to_signal, signal_name
from .study import (
    EventObservation,
    build_observations,
    compare_signals_ic,
    group_stats,
    ic_decay_curve,
    spearman_ic,
)

__all__ = [
    "Bar",
    "EventObservation",
    "PriceBook",
    "TextSignal",
    "abnormal_returns",
    "aggregate_daily_signal",
    "assert_no_lookahead",
    "build_observations",
    "compare_signals_ic",
    "equal_weight_index",
    "group_stats",
    "ic_decay_curve",
    "label_to_signal",
    "signal_name",
    "spearman_ic",
]
