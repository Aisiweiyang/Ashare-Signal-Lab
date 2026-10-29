"""连接器注册表：内置注册 + entry-points 插件发现（借鉴 OpenBB provider 模式，自实现）。

模块化边界（ADR-011）：
- 系统其余部分只依赖 `REGISTRY.create(...)` / `REGISTRY.list_connectors()`，
  不 import 具体连接器实现；新增数据源不改核心代码；
- 第三方包通过 pyproject 声明即可挂接新来源，零参工厂返回 Connector 实例：

    [project.entry-points."asg.connectors"]
    my_source = "my_pkg.connector:MyConnector"

  以 `connector.meta.source_id` 为键注册；
- 插件加载失败只登记原因、跳过该插件，不影响宿主启动（插件隔离）；
- 注册表保存**工厂**而非实例：同一来源可按参数（seed、时间窗）创建多实例；
- CSV 上传属于每次请求的临时实例，不进全局注册表（见 csv_docs.CsvConnector）。
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from asg_domain.errors import ConnectorError, ConnectorUnavailableError

ENTRY_POINT_GROUP = "asg.connectors"

# 零参工厂：返回实现 Connector 协议的实例
ConnectorFactory = Callable[..., Any]


@dataclass(frozen=True)
class ConnectorRecord:
    source_id: str
    factory: ConnectorFactory
    origin: str  # builtin / <插件 distribution 名>
    disabled: bool = False  # 运维开关：注册但不允许创建


class ConnectorRegistry:
    """按 source_id 管理连接器工厂。线程安全性按单进程研究工具假设（不加锁）。"""

    def __init__(self) -> None:
        self._factories: dict[str, ConnectorRecord] = {}

    def register(
        self,
        source_id: str,
        factory: ConnectorFactory,
        *,
        origin: str = "builtin",
        overwrite: bool = False,
    ) -> None:
        if not callable(factory):
            raise ConnectorError(f"连接器工厂必须是零参可调用对象: {source_id!r}")
        if source_id in self._factories and not overwrite:
            raise ConnectorError(f"source_id 已注册（如需覆盖传 overwrite=True）: {source_id!r}")
        self._factories[source_id] = ConnectorRecord(
            source_id=source_id, factory=factory, origin=origin
        )

    def set_disabled(self, source_id: str, *, disabled: bool = True) -> None:
        """运维停用开关：保留注册项但禁止创建实例（合规/故障隔离用）。可反复切换。"""
        rec = self._factories.get(source_id)
        if rec is None:
            raise ConnectorError(f"未知 source_id: {source_id!r}")
        self._factories[source_id] = ConnectorRecord(
            source_id=rec.source_id, factory=rec.factory, origin=rec.origin, disabled=disabled
        )

    def create(self, source_id: str, **params: Any):
        rec = self._factories.get(source_id)
        if rec is None:
            raise ConnectorUnavailableError(
                f"未注册的连接器来源: {source_id!r}；已知: {sorted(self._factories)}"
            )
        if rec.disabled:
            raise ConnectorUnavailableError(f"连接器已被运维停用: {source_id!r}")
        instance = rec.factory(**params)
        meta_id = getattr(getattr(instance, "meta", None), "source_id", source_id)
        if meta_id != source_id:
            raise ConnectorError(
                f"工厂产出的 meta.source_id({meta_id!r}) 与注册键({source_id!r})不一致"
            )
        return instance

    def list_connectors(self) -> list[ConnectorRecord]:
        return sorted(self._factories.values(), key=lambda r: r.source_id)

    def discover_plugins(self, *, group: str = ENTRY_POINT_GROUP) -> list[str]:
        """扫描 entry-points 并注册第三方连接器；返回 (注册成功 + 失败原因) 日志列表。

        失败隔离：单个插件损坏只跳过并记录，绝不中断宿主。
        """
        log: list[str] = []
        try:
            eps = importlib.metadata.entry_points(group=group)
        except TypeError:  # pragma: no cover - 兼容旧 select() API 的运行时
            eps = importlib.metadata.entry_points().get(group, [])  # type: ignore[attr-defined]
        for ep in eps:
            try:
                factory = ep.load()
                instance = factory()
                source_id = getattr(getattr(instance, "meta", None), "source_id", None)
                if not source_id:
                    raise ConnectorError("entry-point 产物缺少 meta.source_id")
                self.register(
                    source_id, factory, origin=ep.dist.name if ep.dist else ep.name, overwrite=True
                )
                log.append(f"ok: {source_id} <- {ep.value}")
            except Exception as e:
                log.append(f"skipped: {ep.name} ({type(e).__name__}: {e})")
        return log


REGISTRY = ConnectorRegistry()


def register_builtin() -> None:
    """注册内置连接器（幂等；模块导入即生效）。"""
    from .akshare_market import AkshareDailyBarsConnector
    from .synthetic import SYNTHETIC_SOURCE_ID, SyntheticConnector

    REGISTRY.register(
        SYNTHETIC_SOURCE_ID,
        SyntheticConnector,
        overwrite=True,  # 幂等：重复调用以最新定义为准
    )
    REGISTRY.register("akshare_hist_daily", AkshareDailyBarsConnector, overwrite=True)


register_builtin()
