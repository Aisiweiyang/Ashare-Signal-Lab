"""领域枚举。所有标签与状态值域的唯一事实源。"""

from __future__ import annotations

from enum import StrEnum


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class VsExpectation(StrEnum):
    ABOVE = "above"
    INLINE = "inline"
    BELOW = "below"
    NO_EXPECTATION = "no_expectation"
    UNKNOWN = "unknown"


class Certainty(StrEnum):
    CERTAIN = "certain"
    SPECULATIVE = "speculative"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


class TargetType(StrEnum):
    COMPANY = "company"
    SECURITY = "security"
    INDUSTRY = "industry"
    INDEX_MACRO = "index_macro"
    OTHER = "other"


class EventType(StrEnum):
    EARNINGS = "earnings"
    ORDERS = "orders"
    MA = "ma"  # 并购
    REGULATORY = "regulatory"
    SHAREHOLDER = "shareholder"
    PRODUCT = "product"
    LITIGATION = "litigation"
    MACRO_POLICY = "macro_policy"
    OTHER = "other"


class QualityState(StrEnum):
    """标注质量状态流转：pending → agreed/adjudicated → gold；分歧未仲裁不可为 gold。"""

    PENDING = "pending"
    AGREED = "agreed"
    ADJUDICATED = "adjudicated"
    DISCARDED = "discarded"


class UserRole(StrEnum):
    ADMIN = "admin"
    RESEARCHER = "researcher"
    ADJUDICATOR = "adjudicator"
    ANNOTATOR = "annotator"
    VIEWER = "viewer"


class RunState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEGRADED = "degraded"  # 降级运行（如 LLM 不可用走 mock）


class SignalKind(StrEnum):
    DIRECTION_3CLASS = "direction_3class"
    FINE_GRAINED = "fine_grained"
    DUMMY_MAJORITY = "dummy_majority"
    LEXICON = "lexicon"


class FillStatus(StrEnum):
    FILLED = "filled"
    REJECTED_LIMIT_UP = "rejected_limit_up"  # 涨停不可买
    REJECTED_LIMIT_DOWN = "rejected_limit_down"  # 跌停不可卖
    REJECTED_SUSPENDED = "rejected_suspended"
    REJECTED_NO_BAR = "rejected_no_bar"
    REJECTED_T_PLUS_1 = "rejected_t_plus_1"


LABEL_DIMENSIONS: tuple[str, ...] = ("direction", "vs_expectation", "certainty")
