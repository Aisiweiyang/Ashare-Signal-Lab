"""实体链接：证券代码识别 + 简称别名表匹配，输出带跨度的链接结果。"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A 股代码：6 位数字，可选交易所后缀
_CODE = re.compile(r"(?<!\d)(\d{6})\.(SZ|SS|SH)(?!\d)|(?<!\d)(\d{6})(?!\d)")

# 交易所推断：6 开头=沪，0/3 开头=深，4/8 开头=北
_EXCHANGE_BY_PREFIX = {"6": "SSE", "0": "SZSE", "3": "SZSE", "4": "BSE", "8": "BSE"}


@dataclass(frozen=True)
class EntityMention:
    span_start: int
    span_end: int
    surface: str
    security_id: str | None = None
    issuer_id: str | None = None
    symbol: str | None = None
    exchange: str | None = None
    method: str = "regex_code"
    confidence: float = 1.0


def exchange_for_symbol(symbol: str) -> str | None:
    return _EXCHANGE_BY_PREFIX.get(symbol[:1])


def find_code_mentions(text: str, *, resolve=None) -> list[EntityMention]:
    """识别文本中的证券代码。resolve: symbol -> (security_id, issuer_id) | None。"""
    out: list[EntityMention] = []
    for m in _CODE.finditer(text):
        sym = m.group(1) or m.group(3)
        suffix = m.group(2)
        if suffix in ("SZ", "SS", "SH"):
            exchange = "SZSE" if suffix == "SZ" else "SSE"
        else:
            exchange = exchange_for_symbol(sym)
            # 无后缀且首位无法推断为 A 股板块的 6 位串（如日期、邮编）不当作代码
            if exchange is None:
                continue
        sec_id = iss_id = None
        if resolve is not None:
            hit = resolve(sym)
            if hit:
                sec_id, iss_id = hit
            else:
                continue  # 无法解析到库内证券则不产生链接（防误链）
        out.append(
            EntityMention(
                span_start=m.start(),
                span_end=m.end(),
                surface=m.group(0),
                security_id=sec_id,
                issuer_id=iss_id,
                symbol=sym,
                exchange=exchange,
                method="regex_code",
                confidence=1.0 if suffix else 0.9,
            )
        )
    return out


def build_alias_index(aliases: list[tuple[str, str, str]]) -> dict[str, tuple[str, str]]:
    """别名索引：[(alias, security_id, issuer_id)] → {alias: (sec, iss)}。

    长别名优先匹配（避免「银行」类短简称误命中——调用方应避免过短别名）。
    """
    idx: dict[str, tuple[str, str]] = {}
    for alias, sec, iss in aliases:
        if len(alias) < 2:
            raise ValueError(f"别名过短（≥2 字符要求）: {alias!r}")  # 单字别名误链风险高，直接拒绝
        prev = idx.get(alias)
        if prev is not None and prev != (sec, iss):
            raise ValueError(f"别名冲突: {alias!r} 同时映射到 {prev} 与 {(sec, iss)}")
        idx[alias] = (sec, iss)
    return idx


def find_alias_mentions(
    text: str,
    alias_index: dict[str, tuple[str, str]],
    *,
    method: str = "alias_table",
) -> list[EntityMention]:
    out: list[EntityMention] = []
    if not text:
        return out
    # 按别名长度降序，同一跨度只保留最长命中
    spans_found: list[tuple[int, int]] = []
    for alias in sorted(alias_index, key=len, reverse=True):
        start = 0
        while True:
            i = text.find(alias, start)
            if i < 0:
                break
            j = i + len(alias)
            if not any(s < j and i < e for s, e in spans_found):
                sec, iss = alias_index[alias]
                out.append(
                    EntityMention(
                        span_start=i,
                        span_end=j,
                        surface=alias,
                        security_id=sec,
                        issuer_id=iss,
                        method=method,
                        confidence=0.95,
                    )
                )
                spans_found.append((i, j))
            start = j
    out.sort(key=lambda m: m.span_start)
    return out


def link_entities(
    text: str,
    *,
    alias_index: dict[str, tuple[str, str]] | None = None,
    resolve_code=None,
) -> list[EntityMention]:
    """综合链接：代码 + 别名，按跨度去重（重叠时保留 confidence 高者）。"""
    mentions = find_code_mentions(text, resolve=resolve_code)
    if alias_index:
        mentions += find_alias_mentions(text, alias_index)
    mentions.sort(key=lambda m: (m.span_start, -m.span_end))
    kept: list[EntityMention] = []
    last_end = -1
    for m in mentions:
        if m.span_start >= last_end:
            kept.append(m)
            last_end = m.span_end
    return kept


def primary_security(mentions: list[EntityMention]) -> str | None:
    """主证券：首个出现的链接（标题/首段主语优先由调用方保证）。"""
    for m in mentions:
        if m.security_id:
            return m.security_id
    return None
