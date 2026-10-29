"""中文文本规范化与脱敏。纯函数，无副作用。"""

from __future__ import annotations

import re
from datetime import datetime

# 常见 HTML 标签
_HTML_TAG = re.compile(r"<[^>]{1,200}>")
# 全角数字/字母 → 半角由 translate 表处理
_FULL2HALF = {ord(f): ord(t) for f, t in zip(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    strict=True,
)}
_FULL2HALF[0xFF05] = 0x25  # ％ → %
_WHITESPACE = re.compile(r"[ \t\r\f\v\u3000]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
# 中国大陆手机号（1 开头 11 位）
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 身份证（18 位，含校验位 X）
_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
# 银行卡式长数字串（12-19 位，保守脱敏）
_LONG_NUM = re.compile(r"(?<!\d)\d{12,19}(?!\d)")
_URL = re.compile(r"https?://\S+")


def strip_html(text: str) -> str:
    return _HTML_TAG.sub(" ", text)


def normalize(text: str) -> str:
    """清洗管线：去 HTML → 全角转半角 → 压空白 → 统一换行 → strip。"""
    if not text:
        return ""
    t = strip_html(text)
    t = t.translate(_FULL2HALF)
    t = _WHITESPACE.sub(" ", t)
    t = _MULTI_NEWLINE.sub("\n\n", t)
    return t.strip()


def anonymize(text: str) -> str:
    """PII 脱敏：手机号、身份证、长数字串、URL 域名主体。"""
    t = _PHONE.sub("[手机号]", text)
    t = _IDCARD.sub("[证件号]", t)
    t = _LONG_NUM.sub("[数字串]", t)
    t = _URL.sub("[链接]", t)
    return t


def clean_for_analysis(text: str, *, drop_pii: bool = True) -> str:
    """入库标准清洗：normalize + 可选脱敏。"""
    t = normalize(text)
    if drop_pii:
        t = anonymize(t)
    return t


def parse_publish_time(raw: str | datetime | None) -> tuple[datetime | None, str]:
    """解析发布时间，返回 (datetime, precision)。precision ∈ {datetime, date, unknown}。

    日期精度（无时刻）按 DATA_GOVERNANCE 保守处理：调用方应把
    available_at 推到该日之后（本函数只负责解析与标注精度）。
    """
    if raw is None:
        return None, "unknown"
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None), "datetime"
    s = str(raw).strip()
    if not s:
        return None, "unknown"
    for fmt, prec in (
        ("%Y-%m-%d %H:%M:%S", "datetime"),
        ("%Y-%m-%d %H:%M", "datetime"),
        ("%Y/%m/%d %H:%M", "datetime"),
        ("%Y-%m-%dT%H:%M:%S", "datetime"),
        ("%Y年%m月%d日 %H:%M", "datetime"),
        ("%Y-%m-%d", "date"),
        ("%Y/%m/%d", "date"),
        ("%Y年%m月%d日", "date"),
    ):
        try:
            return datetime.strptime(s, fmt), prec
        except ValueError:
            continue
    return None, "unknown"
