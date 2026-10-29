"""asg_connectors：数据源适配器（合成/CSV/fixture；可选 akshare 见 docs/OSS_EVALUATION.md）。

连接器发现与创建统一走 `REGISTRY`（见 registry.py）：内置来源模块导入即注册，
第三方包经 entry-points 组 `asg.connectors` 挂接，核心代码无需修改。
"""

from .base import Connector, ConnectorMeta, MarketBar, MarketConnector, RawDoc
from .csv_docs import CsvConnector, CsvImportResult, import_csv
from .market import decision_day_for_publish, generate_price_bars, signal_strength
from .registry import REGISTRY, ConnectorFactory, ConnectorRecord, ConnectorRegistry
from .synthetic import (
    SYNTHETIC_SECURITIES,
    SYNTHETIC_SOURCE_ID,
    LatentLabels,
    SyntheticConnector,
    SyntheticEvent,
    generate_universe,
)

__all__ = [
    "REGISTRY",
    "SYNTHETIC_SECURITIES",
    "SYNTHETIC_SOURCE_ID",
    "Connector",
    "ConnectorFactory",
    "ConnectorMeta",
    "ConnectorRecord",
    "ConnectorRegistry",
    "CsvConnector",
    "CsvImportResult",
    "LatentLabels",
    "MarketBar",
    "MarketConnector",
    "RawDoc",
    "SyntheticConnector",
    "SyntheticEvent",
    "decision_day_for_publish",
    "generate_price_bars",
    "generate_universe",
    "import_csv",
    "signal_strength",
]
