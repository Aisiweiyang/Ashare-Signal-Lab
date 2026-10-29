"""CSV 批量导入 connector：用户提供文本的最小合法入口。

CSV 列（UTF-8，首行表头）：source_id?, external_id?, title, content, published_at, symbol?, url?
- published_at 支持 datetime/date/缺失（缺失→precision=unknown，严格模式不可用）；
- symbol 仅为提示，实体链接仍以文本内容为准；
- 行级校验失败 → 该行进 errors，不中断整批（失败隔离）；
- 文件大小上限 5MB（THREAT_MODEL T6）。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from asg_text_pipeline.normalize import parse_publish_time

from asg_connectors.base import ConnectorMeta, RawDoc

MAX_CSV_BYTES = 5 * 1024 * 1024
ALLOWED_COLUMNS = {"source_id", "external_id", "title", "content", "published_at", "symbol", "url"}


@dataclass
class CsvImportResult:
    docs: list[RawDoc]
    errors: list[str]  # "第 N 行: 原因"


def import_csv(content: bytes, *, default_source_id: str = "user_csv") -> CsvImportResult:
    if len(content) > MAX_CSV_BYTES:
        raise ValueError(f"CSV 超过大小上限 {MAX_CSV_BYTES} 字节")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError("CSV 必须是 UTF-8 编码") from e

    reader = csv.DictReader(io.StringIO(text))
    header = set(reader.fieldnames or [])
    unknown_cols = header - ALLOWED_COLUMNS
    if unknown_cols:
        raise ValueError(f"CSV 含未知列: {sorted(unknown_cols)}；允许列: {sorted(ALLOWED_COLUMNS)}")
    if "content" not in header:
        raise ValueError("CSV 缺少必需列: content")

    docs: list[RawDoc] = []
    errors: list[str] = []
    seen_external: set[str] = set()
    for i, row in enumerate(reader, start=2):
        content_text = (row.get("content") or "").strip()
        if not content_text:
            errors.append(f"第 {i} 行: content 为空")
            continue
        external_id = (row.get("external_id") or "").strip() or f"csv-{i}"
        if external_id in seen_external:
            errors.append(f"第 {i} 行: external_id 重复 {external_id!r}")
            continue
        seen_external.add(external_id)
        published_raw = (row.get("published_at") or "").strip() or None
        published_at, precision = parse_publish_time(published_raw)
        if published_raw and published_at is None:
            errors.append(f"第 {i} 行: published_at 无法解析 {published_raw!r}")
            continue
        docs.append(
            RawDoc(
                source_id=(row.get("source_id") or "").strip() or default_source_id,
                external_id=external_id,
                title=(row.get("title") or "").strip()[:200],
                content=content_text,
                published_at=published_at,
                published_precision=precision,
                url=(row.get("url") or "").strip()[:500],
                symbol_hint=(row.get("symbol") or "").strip() or None,
            )
        )
    return CsvImportResult(docs=docs, errors=errors)


class CsvConnector:
    """把已上传的 CSV 字节变为 connector（meta 由调用方传入 source_id）。"""

    def __init__(self, content: bytes, *, source_id: str = "user_csv"):
        self.meta = ConnectorMeta(
            source_id=source_id,
            display_name=f"CSV 导入（{source_id}）",
            allowed_use="用户提供数据，用户须确保拥有合法使用权",
            enabled=True,
        )
        self._result = import_csv(content, default_source_id=source_id)

    @property
    def import_errors(self) -> list[str]:
        return list(self._result.errors)

    def fetch_batch(self, **params) -> list[RawDoc]:
        return list(self._result.docs)
