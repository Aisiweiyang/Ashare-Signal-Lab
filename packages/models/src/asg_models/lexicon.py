"""中文金融情绪词典（内置小型词表，确定性、可审计）。

词表为自建演示用途（覆盖合成模板中的表达），不声称覆盖全市场语汇；
真实研究应使用经人工校验的金融情感词典并记录来源与许可证。
"""

from __future__ import annotations

BULLISH_WORDS: tuple[str, ...] = (
    "超预期", "超出预期", "好于预期", "高于预期", "大增", "大增", "增长", "同比增长", "扭亏", "放量",
    "利好", "中标", "签署", "获奖", "获批", "上调", "增持", "回购", "取得", "完成收购", "许可", "突破",
    "创新高", "净流入", "旺季", "满产", "涨价", "提价", "盈利", "预增", "翻倍",
)
BEARISH_WORDS: tuple[str, ...] = (
    "低于预期", "不及预期", "差于预期", "下滑", "同比下降", "亏损", "预减", "处罚", "罚款", "违规",
    "诉讼", "败诉", "判决", "赔偿", "减持", "取消", "终止", "解除", "警告", "退市", "ST", "跌停",
    "承压", "恶化", "召回", "拖欠", "违约", "生变", "推迟", "分歧", "风险提示", "利空",
)
SPECULATIVE_MARKERS: tuple[str, ...] = (
    "或", "有望", "传闻", "市场消息", "知情人士", "预计", "可能", "或将", "或达", "或生变", "可能推迟",
    "接近监管", "未正式回应", "尚未获得", "存在不确定性", "尚待", "待确认",
)
CONDITIONAL_MARKERS: tuple[str, ...] = (
    "若", "如果", "一旦", "待", "如获", "若属实", "若落地", "若获批", "若维持", "假设",
)
CERTAIN_MARKERS: tuple[str, ...] = (
    "公告", "已", "完成", "决定书", "生效", "判决书", "披露", "实施完毕", "已实施", "快报", "已取得",
    "已签署", "交割",
)
INLINE_MARKERS: tuple[str, ...] = ("符合预期", "符合", "落在预期", "区间内", "一致", "按计划", "与此前", "与原")
ABOVE_MARKERS: tuple[str, ...] = ("超预期", "超出预期", "好于预期", "高于预期", "超出", "超过此前", "高于此前", "优于")
BELOW_MARKERS: tuple[str, ...] = ("低于预期", "不及预期", "差于预期", "低于此前", "低于市场预期", "更乐观", "承压")

_UNKNOWN_HINTS: tuple[str, ...] = ("……", "正文缺失", "残片", "更正公告", "不做评论", "需结合")


def _count_hits(text: str, words: tuple[str, ...]) -> int:
    return sum(text.count(w) for w in words)


def lexicon_scores(text: str) -> dict[str, float]:
    """原始词频得分（供 mock provider 构造伪概率与不确定性）。"""
    bull = _count_hits(text, BULLISH_WORDS)
    bear = _count_hits(text, BEARISH_WORDS)
    total = bull + bear
    return {
        "bullish_hits": float(bull),
        "bearish_hits": float(bear),
        "net": float(bull - bear),
        "total": float(total),
    }


def _softmax2(net: float, total: float) -> dict[str, float]:
    """把词频净分转为三分类伪概率（温度按总命中数缩放）。"""
    import math

    temp = max(0.6, 1.2 - 0.05 * total)
    raw = {"bullish": math.exp(net / temp), "neutral": math.exp(0.0), "bearish": math.exp(-net / temp)}
    s = sum(raw.values())
    return {k: round(v / s, 4) for k, v in raw.items()}


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def classify_with_lexicon(text: str) -> dict:
    """规则词典预测（确定性）。返回结构化标签 + 伪概率 + 不确定性。

    - direction: net>0 → bullish；net<0 → bearish；有 unknown 提示且无情绪词 → unknown；否则 neutral
    - vs_expectation: above/below/inline 标记优先；无参照 → no_expectation
    - certainty: speculative > conditional > certain 依标记优先级；全部缺失时
      若含 unknown 提示 → unknown，否则 certain
    """
    is_unknownish = _has_any(text, _UNKNOWN_HINTS)
    net = lexicon_scores(text)["net"]

    if _has_any(text, ABOVE_MARKERS):
        vs = "above"
    elif _has_any(text, BELOW_MARKERS):
        vs = "below"
    elif _has_any(text, INLINE_MARKERS):
        vs = "inline"
    else:
        vs = "no_expectation"

    if _has_any(text, SPECULATIVE_MARKERS):
        cert = "speculative"
    elif _has_any(text, CONDITIONAL_MARKERS):
        cert = "conditional"
    elif _has_any(text, CERTAIN_MARKERS):
        cert = "certain"
    else:
        cert = "unknown" if is_unknownish else "certain"

    if net > 0:
        direction = "bullish"
    elif net < 0:
        direction = "bearish"
    else:
        direction = "unknown" if is_unknownish else "neutral"

    probs = _softmax2(net, lexicon_scores(text)["total"])
    confidence = max(probs.values())
    uncertainty = round(1.0 - confidence, 4)
    return {
        "labels": {
            "direction": direction,
            "vs_expectation": vs,
            "certainty": cert,
            "target_type": "company",
            "event_type": "other",
        },
        "probabilities": {"direction": probs},
        "confidence": confidence,
        "uncertainty": uncertainty,
    }
