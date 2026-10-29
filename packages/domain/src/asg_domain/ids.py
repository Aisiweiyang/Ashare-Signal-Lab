"""ID 生成、内容哈希、配置哈希与数据集哈希。

所有 ID 形如 `<前缀>_<12位hex>`，可读且跨数据库移植；
所有哈希统一 sha256 hex，作为血缘字段的唯一口径。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def short_token(n: int = 16) -> str:
    return secrets.token_hex(n // 2)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_hash(text: str) -> str:
    return content_hash(text.encode("utf-8"))


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_json(obj: Any) -> str:
    """确定性 JSON 序列化（排序键、固定分隔符、无空白），用于配置哈希。"""

    def _default(o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, (set, frozenset)):
            return sorted(o)
        raise TypeError(f"不可序列化类型: {type(o)!r}")

    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_default)


def config_hash(cfg: Any) -> str:
    return text_hash(stable_json(cfg))


def dataset_hash(rows: list[dict[str, Any]]) -> str:
    """行序敏感的数据集哈希：每行 stable_json 后再整体哈希。"""
    joined = "\n".join(stable_json(r) for r in rows)
    return text_hash(joined)


def canonical_env() -> dict[str, str]:
    """运行环境指纹（写入 run manifest；不包含任何密钥值）。"""
    import platform
    import sys

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
