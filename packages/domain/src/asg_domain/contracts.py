"""跨系统导入导出 contract（首版预留，不做企业穿透/UBO）。

其他项目（如未来的股权穿透系统）只能通过这四个稳定 ID 与本项目交换数据，
交换格式见 `export_entities` / `import_entities`。
"""

from __future__ import annotations

from typing import Any, TypedDict


class EntityRef(TypedDict):
    """对外 contract：实体引用。"""

    entity_id: str  # Issuer.issuer_id（ent_ 前缀）
    security_id: str  # Security.security_id（sec_ 前缀）
    event_id: str  # MarketEvent.event_id（evt_ 前缀）
    source_id: str  # SourceDocument.source_id（来源标识）


CONTRACT_VERSION = "1.0"


def export_entities(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """导出实体引用集合。要求每行含全部四个 contract 字段，否则 ValueError。"""
    required = ("entity_id", "security_id", "event_id", "source_id")
    out: list[EntityRef] = []
    for i, row in enumerate(rows):
        missing = [k for k in required if not row.get(k)]
        if missing:
            raise ValueError(f"第 {i} 行缺少 contract 字段: {missing}")
        out.append({k: str(row[k]) for k in required})
    return {"contract_version": CONTRACT_VERSION, "entities": out}


def import_entities(payload: dict[str, Any]) -> list[EntityRef]:
    """导入实体引用集合：校验 contract 版本与字段完整性。"""
    version = payload.get("contract_version")
    if version != CONTRACT_VERSION:
        raise ValueError(f"contract 版本不兼容: {version!r}（期望 {CONTRACT_VERSION!r}）")
    rows = payload.get("entities")
    if not isinstance(rows, list):
        raise ValueError("entities 必须是列表")
    return export_entities(rows)["entities"]
