"""Connector contract（借鉴 OpenBB provider 模式，自实现）。

每个 connector 必须：
- 声明 source_id、允许用途、频率限制、启用开关；
- 产出统一的 RawDoc（含来源、原始时间、内容），不做清洗（清洗属 text_pipeline）；
- 幂等：同样输入产出同样输出（同 content_hash）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol


@dataclass
class RawDoc:
    """connector → 系统的最小文档单元。published_precision 由 connector 如实声明。"""

    source_id: str
    external_id: str  # 来源侧唯一 ID（用于幂等去重）
    title: str
    content: str
    published_at: datetime | None
    published_precision: str  # datetime / date / unknown
    url: str = ""
    symbol_hint: str | None = None  # 来源侧证券代码提示（仍需实体链接确认）
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorMeta:
    source_id: str
    display_name: str
    allowed_use: str  # 允许用途声明（合规）
    rate_limit_per_minute: int = 60
    enabled: bool = True


class Connector(Protocol):
    meta: ConnectorMeta

    def fetch_batch(self, **params: Any) -> list[RawDoc]:
        """返回一批 RawDoc。真实 connector 在此实现限速/退避/缓存；
        本项目默认只提供合成/CSV/fixture 等无外部访问实现。"""
        ...


@dataclass
class MarketBar:
    """行情 connector → 系统的最小行情单元（字段与 PriceBar 对齐）。

    limit_up/limit_down 与 suspended 由 connector 如实给出；
    connector 不得为了「数据完整」虚构停牌或涨跌停价。
    """

    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    volume: float
    amount: float
    suspended: bool
    limit_up: float
    limit_down: float


class MarketConnector(Protocol):
    """行情数据连接器契约（与文本 Connector 并列，互不依赖）。

    实现必须：幂等（同参数同结果）、声明 allowed_use、默认可停用；
    可选依赖缺失时抛 ConnectorUnavailableError，不得静默替换数据源。
    """

    meta: ConnectorMeta

    def fetch_price_bars(self, symbol: str, *, start: date, end: date) -> list[MarketBar]:
        """返回 [start, end] 闭区间内的日行情（按 trade_date 升序）。"""
        ...
