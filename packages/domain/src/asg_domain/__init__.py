"""asg_domain：核心领域模型。见 docs/DATA_MODEL 与 docs/ARCHITECTURE。"""

from .calendar import TradingCalendar, default_calendar
from .errors import (
    AshareSignalError,
    AvailabilityError,
    BudgetExceededError,
    CalendarError,
    LabelValidationError,
    QuarantinedOutputError,
    TradingDisabledError,
)
from .timeutils import (
    AFTERNOON_CLOSE,
    availability_from_publish,
    decision_time_for_event_day,
    ensure_naive_cn,
)

__all__ = [
    "AFTERNOON_CLOSE",
    "AshareSignalError",
    "AvailabilityError",
    "BudgetExceededError",
    "CalendarError",
    "LabelValidationError",
    "QuarantinedOutputError",
    "TradingCalendar",
    "TradingDisabledError",
    "availability_from_publish",
    "decision_time_for_event_day",
    "default_calendar",
    "ensure_naive_cn",
]
