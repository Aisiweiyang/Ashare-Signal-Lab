"""Auditable bilingual financial sentiment baseline, explicitly versioned v2.

No fitted accuracy or probability calibration is claimed. Input clauses,
target scope, negation, expectation evidence, and abstentions remain visible.
The original lexicon_v1 and the registered synthetic experiments are unchanged.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from asg_text_pipeline.bilingual import (
    NORMALIZATION_VERSION,
    detect_text_language,
    normalize_bilingual,
)

MODEL_VERSION = "bilingual_finance_rules_v2.1"
MAX_TEXT_LENGTH = 20000
MAX_CLAUSES = 200


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


@dataclass(frozen=True)
class _Rule:
    key: str
    pattern: re.Pattern
    polarity: float
    event: str = "other"


def _en(pattern: str) -> str:
    return rf"(?<![A-Za-z0-9_])(?:{pattern})(?![A-Za-z0-9_])"


# Specific phrases win over overlapping generic cues. English uses word
# boundaries: e.g. ST and loss must not match "forecast" and "glossary".
_RULES = (
    _Rule("loss_narrowed", _rx(r"亏损(?:大幅)?(?:收窄|减少)|虧損(?:收窄|減少)|" + _en(r"loss(?:es)?\s+(?:(?:has|have|had)\s+)?(?:narrow(?:s|ed|ing)?|shrank|shrunk|decreas(?:e[sd]?|ing)|fell|fallen|declin(?:e[sd]?|ing))")), 1, "earnings"),
    _Rule("loss_widened", _rx(r"亏损(?:扩大|擴大)|虧損擴大|" + _en(r"loss(?:es)?\s+(?:(?:has|have|had)\s+)?(?:widen(?:s|ed|ing)?|expand(?:s|ed|ing)?|increas(?:e[sd]?|ing)|rose|risen)")), -1, "earnings"),
    _Rule("turned_profitable", _rx(r"由亏转盈|由虧轉盈|扭亏为盈|扭虧為盈|" + _en(r"(?:returned|return(?:s|ing)?)\s+to\s+profit|turned\s+profitable")), 1, "earnings"),
    _Rule("turned_loss", _rx(r"由盈转亏|由盈轉虧|转盈为亏|轉盈為虧|" + _en(r"(?:swung|turned)\s+(?:to|into)\s+(?:a\s+)?loss")), -1, "earnings"),
    _Rule("earnings_positive", _rx(r"同比增长|同比增長|大增|扭亏|扭虧|盈利|增长|增長|提高|" + _en(r"(?:profit|revenue|earnings)\s+growth|profitable|profitability|grow(?:s|ing)?|grew|grown|rise[sn]?|rising|rose|increas(?:e[sd]?|ing)|surg(?:e[sd]?|ing)|jump(?:s|ed|ing)?|improv(?:e[sd]?|ing)|growth")), 1, "earnings"),
    _Rule("earnings_negative", _rx(r"同比下降|亏损|虧損|下滑|下降|预减|預減|承压|惡化|恶化|" + _en(r"loss(?:es)?|unprofitable|declin(?:e[sd]?|ing)|decreas(?:e[sd]?|ing)|fall(?:s|en|ing)?|fell|slump(?:s|ed|ing)?|deteriorat(?:e[sd]?|ing)")), -1, "earnings"),
    _Rule("approval", _rx(r"获批|獲批|取得许可|取得許可|" + _en(r"approved|approval|authorized")), 1, "regulatory"),
    _Rule("penalty", _rx(r"处罚|處罰|罚款|罰款|违规|違規|退市|" + _en(r"fined|penalty|penalties|sanctioned|delisted|rejected")), -1, "regulatory"),
    _Rule("orders", _rx(r"中标|中標|签署|簽署|订单增加|訂單增加|" + _en(r"won\s+(?:a\s+)?contract|signed\s+(?:a\s+)?contract|order\s+growth")), 1, "orders"),
    _Rule("buyback", _rx(r"回购|回購|增持|" + _en(r"buybacks?|share\s+repurchases?|repurchas(?:e[sd]?|ing)\s+shares|raised\s+(?:its\s+)?dividend")), 1, "shareholder"),
    _Rule("selling", _rx(r"减持|減持|" + _en(r"insider\s+selling|cut\s+(?:its\s+)?dividend")), -1, "shareholder"),
    _Rule("litigation", _rx(r"诉讼|訴訟|败诉|敗訴|违约|違約|" + _en(r"lawsuit|defaulted|litigation")), -1, "litigation"),
    _Rule("merger", _rx(r"完成收购|完成收購|" + _en(r"completed\s+(?:the\s+)?acquisition")), 1, "ma"),
    _Rule("cancellation", _rx(r"终止|終止|取消|召回|推迟|推遲|" + _en(r"cancelled|canceled|terminated|recalled|delayed")), -1),
    _Rule("positive", _rx(r"利好|突破|" + _en(r"bullish|upgraded|outperformed")), 1),
    _Rule("negative", _rx(r"利空|" + _en(r"bearish|downgraded|underperformed")), -1),
)
_ABOVE = _rx(r"超(?:出)?(?:市场|市場|分析师|分析師)?(?:一致)?预期|超(?:出)?(?:市場)?預期|高于(?:市场)?预期|好于预期|" + _en(r"(?:beat|beats|exceeded|above|better\s+than)\s+(?:market\s+|analyst\s+|consensus\s+)?(?:expectations?|estimates?|forecasts?)"))
_BELOW = _rx(r"低于(?:市场|市場)?(?:一致)?预期|低於(?:市場)?預期|不及预期|不及預期|" + _en(r"(?:missed|misses|below|worse\s+than|fell\s+short\s+of)\s+(?:market\s+|analyst\s+|consensus\s+)?(?:expectations?|estimates?|forecasts?)"))
_INLINE = _rx(r"符合(?:市场)?预期|符合預期|" + _en(r"(?:met|in\s+line\s+with|matched)\s+(?:market\s+|analyst\s+|consensus\s+)?(?:expectations?|estimates?|forecasts?)"))
_SPECULATIVE = _rx(r"预计|預計|可能|有望|传闻|傳聞|尚待|待确认|待確認|(?:拟|擬|计划|計劃|将|將)(?=.{0,6}(?:回购|回購|增持|收购|收購|减持|減持|增长|增長|获批|獲批))|" + _en(r"may|might|could|will|won't|rumou?rs?|reportedly|expects?|expected\s+to|forecast(?:s)?\s+to|plans?\s+to|pending"))
_CONDITIONAL = _rx(r"如果|一旦|若|如获|如獲|" + _en(r"if|subject\s+to|conditional\s+on|provided\s+that"))
_CERTAIN = _rx(r"公告|披露|已|增长|增長|亏损|虧損|下降|" + _en(r"reported|announced|completed|approved|rejected|signed|posted|was|were|is|are|rose|grew|increased|fell|declined|beat|missed|has|have"))
_NEUTRAL = _rx(r"召开|召開|股东大会|股東大會|无重大变化|没有重大变化|" + _en(r"annual\s+meeting|unchanged|no\s+material\s+(?:change|impact)"))
_NEGATION = _rx(r"(?:未|不|无|無|没(?:有)?|沒(?:有)?|尚未|并非|並非|否认|否認)[^,，;；。!?\n]{0,6}$|" + _en(r"not|never|no|without|cannot|can't|won't|shan't|(?:could|would|should|must|did|does|do|is|are|was|were|has|have|had|need)n't|failed\s+to") + r"(?:\s+[A-Za-z]+){0,3}\s*$")
_NOT_ONLY = _rx(r"不仅|不僅|不但|" + _en(r"not\s+only"))
_AMBIGUOUS = _rx(r"不排除|不能排除|并非不|並非不|" + _en(r"not\s+unlikely|cannot\s+rule\s+out|not\s+impossible"))
_CONTRAST = _rx(r"但是|然而|不过|不過|但|" + _en(r"but|however|yet|although"))
_CLAUSE_BREAK = re.compile(r"(?<!\d)[,，](?!\d)|(?<=%)\s*[,，]|但是|然而|不过|不過|但|\b(?:but|however|yet|although)\b", re.IGNORECASE)
_METRIC = _rx(r"每股收益|每股盈余|每股盈餘|毛利率|净利率|淨利率|利润率|利潤率|净利润|淨利潤|利润|利潤|营收|營收|收入|成本|费用|費用|" + _en(r"(?:gross|net|profit)\s+margin|margins?|net\s+profit|profits?|revenue|earnings\s+per\s+share|EPS|costs?|expenses?"))
_EXPECTATION = _rx(r"(?:市场|市場|分析师|分析師)?(?:一致)?(?:预期|預期)|" + _en(r"expectations?|expected|estimates?|consensus|forecasts?"))
_FINANCIAL_CONTEXT = _rx(r"公司|利润|利潤|利率|每股收益|每股盈|由亏转盈|由盈转亏|营收|營收|收入|成本|费用|費用|业绩|業績|回购|回購|股|亏损|虧損|获批|獲批|中标|中標|利好|利空|" + _en(r"profits?|profitable|earnings|revenue|EPS|margins?|costs?|expenses?|loss(?:es)?|shares?|stocks?|buybacks?|dividends?|approval|approved|contracts?|bullish|bearish|acquisition|lawsuit|litigation|fined|penalt(?:y|ies)"))
_VALUE = _rx(r"(?<![A-Za-z0-9_.])(?P<currency>\$|USD\s*|CNY\s*|RMB\s*|人民币\s*)?(?P<value>[+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>percentage\s+points?\b|percent(?:age)?\s+points?\b|basis\s+points?\b|bps?\b|(?:个|個)?百分点|(?:个|個)?百分點|(?:个|個)?基点|(?:个|個)?基點|%|percent\b|billion\b|million\b|bn\b|亿元|萬元|万元|亿|億|万|萬|元|USD\b|CNY\b)?")
_CHANGE = _rx(r"增长|增長|下降|下滑|提高|降低|减少|減少|" + _en(r"grow(?:s|ing|th)?|grew|grown|rise[sn]?|rising|rose|increas(?:e[sd]?|ing)|declin(?:e[sd]?|ing)|decreas(?:e[sd]?|ing)|fall(?:s|en|ing)?|fell"))
_DECLINE = _rx(r"下降|下滑|降低|减少|減少|" + _en(r"declin(?:e[sd]?|ing)|decreas(?:e[sd]?|ing)|fall(?:s|en|ing)?|fell"))
_LEVEL_END = _rx(r"(?:至|为|為|达到|達到|" + _en(r"to|at|was|were|is|are") + r")\s*$")


def _sentence_spans(text: str):
    """Bounded financial-text heuristics, not a full UAX29 implementation.

    Both languages reset target/conditional/numeric scope at sentence ends.
    Decimal points, dotted acronyms, and a small abbreviation list are kept.
    """
    start = 0
    for match in re.finditer(r"[。!?！？;；\n.]", text):
        pos = match.start()
        if match.group() == ".":
            left, right = text[:pos+1], text[pos+1:]
            if pos and right and text[pos-1].isdigit() and right[0].isdigit():
                continue
            if right and not right[0].isspace() and re.match(r"[A-Za-z0-9]", right):
                continue
            if right.strip() and (_rx(r"\b(?:Inc|Ltd|Corp|Co|Mr|Mrs|Dr|vs|e\.g|i\.e)\.$").search(left) or re.search(r"(?:\b[A-Za-z]\.){2,}$", left)):
                continue
        yield start, match.end()
        start = match.end()
    if start < len(text):
        yield start, len(text)


def _speculative(text: str) -> bool:
    for match in _SPECULATIVE.finditer(text):
        if match.group().casefold() == "may":
            before, after = text[:match.start()], text[match.end():]
            # A few explicit date contexts. Case alone cannot distinguish May
            # the month from a modal at the start of an English sentence.
            if _rx(r"\b(?:in|during|for|since|through|of|by|until|on)\s+$|\d\s+$").search(before) or _rx(r"^\s+(?:\d|profits?\b|earnings\b|revenue\b|results\b|sales\b|costs?\b|expenses?\b)").search(after):
                continue
        return True
    return False


def _contains_alias(text: str, alias: str) -> bool:
    if re.search(r"[\u3400-\u9fff]", alias):
        return alias.casefold() in text.casefold()
    return bool(re.search(r"(?<![A-Za-z0-9_])" + re.escape(alias) + r"(?![A-Za-z0-9_])", text, re.IGNORECASE))


def _negated(text: str, start: int) -> bool:
    prefix = _NOT_ONLY.sub("", text[max(0, start - 32):start])
    return bool(_NEGATION.search(prefix))


def _metric_key(name: str) -> str:
    name = name.casefold()
    if name in {"eps", "earnings per share", "每股收益", "每股盈余", "每股盈餘"}:
        return "eps"
    if "margin" in name or "率" in name:
        return "margin"
    if any(w in name for w in ("profit", "利润", "利潤")):
        return "profit"
    if name in {"营收", "營收", "收入", "revenue"}:
        return "revenue"
    return "cost"


def _numeric_comparison(text: str) -> tuple[dict | None, str | None]:
    """Conservative explicit actual-before-expectation comparison in one sentence.

    Multiple metrics, missing units, or growth/level mismatches abstain rather
    than guess. This is not general financial table extraction.
    """
    marker = _EXPECTATION.search(text)
    metrics = list(_METRIC.finditer(text))
    if not marker or not metrics:
        return None, None
    values = list(_VALUE.finditer(text))
    before = [v for v in values if v.end() <= marker.start()]
    after = [v for v in values if v.start() >= marker.end()]
    if not before or not after:
        return None, "numeric_reference_order_unsupported" if len(values) >= 2 else None
    if _negated(text, marker.start()) or _AMBIGUOUS.search(text):
        return None, "numeric_negated_reference"
    if len({_metric_key(m.group()) for m in metrics}) != 1:
        return None, "numeric_multiple_metrics"
    actual, expected = before[-1], after[0]
    if metrics[0].start() > actual.start():
        return None, "numeric_reference_order_unsupported"

    def amount(m):
        unit = (m.group("unit") or "").casefold()
        value = float(m.group("value").replace(",", ""))
        currency = (m.group("currency") or "").strip().casefold()
        if "point" in unit or "百分点" in unit or "百分點" in unit or "基点" in unit or "基點" in unit or unit in {"bp", "bps"}:
            basis = "basis" in unit or "基点" in unit or "基點" in unit or unit in {"bp", "bps"}
            return value/100 if basis else value, "percentage_point"
        if unit in {"%", "percent"}:
            return value, "percent"
        scales = {"billion": 1e9, "bn": 1e9, "million": 1e6, "亿元": 1e8, "亿": 1e8, "億": 1e8, "万元": 1e4, "萬元": 1e4, "万": 1e4, "萬": 1e4, "元": 1}
        if currency in {"$", "usd"} or unit == "usd":
            return value * scales.get(unit, 1), "usd"
        if currency in {"cny", "rmb", "人民币"} or unit in {"亿元", "万元", "萬元", "亿", "億", "万", "萬", "元", "cny"}:
            return value * scales.get(unit, 1), "cny"
        return value, "unspecified"

    a, unit_a = amount(actual)
    e, unit_e = amount(expected)
    if not math.isfinite(a) or not math.isfinite(e):
        return None, "numeric_out_of_range"
    if unit_a != unit_e or unit_a == "unspecified":
        return None, "numeric_units_not_comparable"
    # "fell BY 10%" is a signed change; "fell TO CNY 10m" is a level.
    # An omitted expectation basis may inherit the explicit actual basis,
    # whereas two explicitly different bases are never compared.
    def basis(prefix: str, unit: str) -> tuple[str | None, bool]:
        prefix = prefix[-48:]
        if unit == "percentage_point":
            return "change", bool(_DECLINE.search(prefix))
        if _LEVEL_END.search(prefix):
            return "level", False
        if _CHANGE.search(prefix):
            return "growth" if unit == "percent" else "change", bool(_DECLINE.search(prefix))
        return None, False

    actual_basis, actual_decline = basis(text[metrics[0].end():actual.start()], unit_a)
    expected_basis, expected_decline = basis(text[marker.end():expected.start()], unit_e)
    if actual_basis and expected_basis and actual_basis != expected_basis:
        return None, "numeric_bases_not_comparable"
    actual_basis = actual_basis or expected_basis or "level"
    if actual_decline:
        a = -abs(a)
    if expected_decline:
        e = -abs(e)
    # Explicitly different periods still require human review.
    periods = re.findall(r"20\d{2}(?:年|Q[1-4]|H[12])?", text, re.IGNORECASE)
    if len(set(periods)) > 1:
        return None, "numeric_periods_not_comparable"
    relation = "inline" if math.isclose(a, e, rel_tol=1e-9, abs_tol=1e-9) else "above" if a > e else "below"
    polarity = 0 if relation == "inline" else 1 if relation == "above" else -1
    metric = _metric_key(metrics[0].group())
    if metric == "cost":
        polarity = -polarity
    return {"metric": metric, "basis": actual_basis, "actual": a, "expected": e, "unit": unit_a, "relation": relation, "polarity": polarity}, None


def _analyze_clause(text: str, start: int, end: int) -> dict:
    piece = text[start:end]
    candidates = []
    for rule in _RULES:
        for match in rule.pattern.finditer(piece):
            candidates.append((match.start(), match.end(), rule))
    selected = []
    occupied: list[tuple[int, int]] = []
    for a, b, rule in sorted(candidates, key=lambda x: (-(x[1]-x[0]), x[0], x[2].key)):
        if not any(a < y and x < b for x, y in occupied):
            selected.append((a, b, rule))
            occupied.append((a, b))
    hits = []
    score = 0.0
    cost_only = {_metric_key(m.group()) for m in _METRIC.finditer(piece)} == {"cost"}
    for a, b, rule in sorted(selected, key=lambda x: x[0]):
        negated = _negated(piece, a)
        # Absence of a negative event is not automatically a positive event.
        polarity = -1.0 if negated and rule.polarity > 0 else 0.0 if negated else rule.polarity
        if cost_only and rule.key in {"earnings_positive", "earnings_negative"}:
            polarity = -polarity
        score += polarity
        hits.append({"start": start+a, "end": start+b, "quote": piece[a:b], "rule": rule.key, "polarity": polarity, "negated": negated, "event_type": rule.event})
    flags = []
    expectations = []
    for value, pattern, polarity in (("above", _ABOVE, 1.5), ("below", _BELOW, -1.5), ("inline", _INLINE, 0.0)):
        for match in pattern.finditer(piece):
            if _negated(piece, match.start()):
                flags.append("negated_expectation")
                continue
            expectations.append(value)
            hits.append({"start": start+match.start(), "end": start+match.end(), "quote": match.group(), "rule": "expectation_"+value, "polarity": polarity, "negated": False, "event_type": "earnings"})
    vs = expectations[0] if len(set(expectations)) == 1 else "unknown" if expectations else "no_expectation"
    if len(set(expectations)) > 1:
        flags.append("conflicting_expectations")
    if expectations:
        # Explicit expectation surprise has a separate economic interpretation
        # from a simple "growth" word; do not double count the same phrase.
        score = 0 if vs in {"unknown", "inline"} else 1.5 if vs == "above" else -1.5
        if cost_only:
            score = -score
    if _AMBIGUOUS.search(piece):
        flags.append("ambiguous_negation")
    certainty = "conditional" if _CONDITIONAL.search(piece) else "speculative" if _speculative(piece) else "certain" if _CERTAIN.search(piece) or hits or _NEUTRAL.search(piece) else "unknown"
    direction = "unknown" if flags else "bullish" if score > 0 else "bearish" if score < 0 else "neutral" if hits or _NEUTRAL.search(piece) else "unknown"
    if direction == "unknown":
        score = 0.0
    return {"start": start, "end": end, "text": piece, "language": detect_text_language(piece), "labels": {"direction": direction, "vs_expectation": vs, "certainty": certainty}, "score": score, "evidence": hits, "flags": flags, "comparison": None}


def analyze_bilingual(text: str, *, title: str = "", target: str = "", aliases: list[str] | None = None, other_targets: list[str] | None = None) -> dict:
    """Analyze financial clauses; evidence offsets refer to normalized_text.

    Target names are caller-provided aliases, not verified security identities.
    No English ticker is guessed from arbitrary uppercase words.
    """
    if len(text) + len(title) > MAX_TEXT_LENGTH:
        raise ValueError(f"标题与正文合计不得超过 {MAX_TEXT_LENGTH} 字符")
    normalized = normalize_bilingual("\n".join(x for x in (title, text) if x))
    target = normalize_bilingual(target).strip()
    names = list(dict.fromkeys(normalize_bilingual(n).strip() for n in [target, *(aliases or [])] if n.strip()))
    if aliases and not target:
        raise ValueError("提供别名时必须指定目标公司")
    if any(not n for n in names):
        raise ValueError("目标名称规范化后不能为空")
    others = [normalize_bilingual(n).strip() for n in (other_targets or []) if n.strip()]
    if any(n.casefold() in {a.casefold() for a in names} for n in others):
        raise ValueError("目标与其他公司名称不能重叠")
    clauses = []
    flags = []
    matched_target = False
    for s_start, s_end in _sentence_spans(normalized):
        body = normalized[s_start:s_end]
        conditional = _CONDITIONAL.search(body)
        has_target = bool(names) and any(_contains_alias(body, n) for n in names)
        has_other = any(_contains_alias(body, n) for n in others)
        if names and matched_target and not has_target and not has_other and _FINANCIAL_CONTEXT.search(body):
            flags.append("unattributed_sentence")
        comparison, comparison_flag = _numeric_comparison(body)
        cuts = [s_start]
        for sep in _CLAUSE_BREAK.finditer(body):
            cuts.extend([s_start+sep.start(), s_start+sep.end()])
        cuts.append(s_end)
        active = not names or (has_target and not has_other)
        sentence_clauses = []
        for a, b in zip(cuts[::2], cuts[1::2], strict=True):
            while a < b and normalized[a].isspace():
                a += 1
            while b > a and normalized[b-1].isspace():
                b -= 1
            if a == b:
                continue
            piece = normalized[a:b]
            target_here = any(_contains_alias(piece, n) for n in names)
            other_here = any(_contains_alias(piece, n) for n in others)
            if target_here:
                matched_target = True
                active = True
            elif other_here:
                active = False
            if names and (not active or (target_here and other_here)):
                if target_here and other_here:
                    flags.append("multiple_targets_in_clause")
                continue
            clause = _analyze_clause(normalized, a, b)
            # A leading condition scopes over its consequence after a comma;
            # a separately asserted completed fact terminates that scope.
            if conditional and a >= s_start+conditional.start() and not _rx(r"已(?:经)?|披露|" + _en(r"reported|announced|completed")).search(piece):
                clause["labels"]["certainty"] = "conditional"
            sentence_clauses.append(clause)
        if comparison_flag and sentence_clauses and not has_other:
            flags.append(comparison_flag)
        if comparison and sentence_clauses and not has_other:
            # Preserve the entire sentence as evidence, including both values.
            row = _analyze_clause(normalized, s_start, s_end)
            row["comparison"] = comparison
            row["labels"]["vs_expectation"] = comparison["relation"]
            row["score"] = comparison["polarity"] * 1.5
            row["labels"]["direction"] = "bullish" if row["score"] > 0 else "bearish" if row["score"] < 0 else "neutral"
            if row["labels"]["certainty"] == "unknown":
                row["labels"]["certainty"] = "certain"
            row["evidence"].append({"start": s_start, "end": s_end, "quote": body, "rule": "explicit_numeric_comparison", "polarity": comparison["polarity"], "negated": False, "event_type": "earnings"})
            sentence_clauses = [row]
        clauses.extend(sentence_clauses)
    if len(clauses) > MAX_CLAUSES:
        clauses = clauses[:MAX_CLAUSES]
        flags.append("clause_limit_exceeded")
    if names and not matched_target:
        flags.append("target_not_found")
    if not normalized:
        flags.append("empty_text")
    if normalized and not (_FINANCIAL_CONTEXT.search(normalized) or _ABOVE.search(normalized) or _BELOW.search(normalized) or _INLINE.search(normalized) or _NEUTRAL.search(normalized)):
        flags.append("out_of_financial_domain")
    relevant = [c for c in clauses if c["labels"]["direction"] != "unknown"]
    net = sum(c["score"] for c in relevant)
    polarities = {c["labels"]["direction"] for c in relevant} - {"neutral"}
    if len(polarities) > 1:
        flags.append("mixed_direction")
    flags.extend(f for c in clauses for f in c["flags"])
    expectations = {c["labels"]["vs_expectation"] for c in clauses} - {"no_expectation"}
    certainty_values = {c["labels"]["certainty"] for c in relevant}
    if len(expectations) > 1:
        flags.append("conflicting_expectations")
    if len(certainty_values) > 1:
        flags.append("mixed_certainty")
    direction = "bullish" if net > 0 else "bearish" if net < 0 else "neutral" if relevant and len(polarities) < 2 else "unknown"
    if any(f in flags for f in ("target_not_found", "clause_limit_exceeded", "ambiguous_negation", "multiple_targets_in_clause", "out_of_financial_domain")):
        direction = "unknown"
    if not relevant:
        flags.append("insufficient_financial_evidence")
    vs = next(iter(expectations)) if len(expectations) == 1 else "unknown" if expectations else "no_expectation"
    certainty = next(iter(certainty_values)) if len(certainty_values) == 1 else "unknown"
    evidence = [h for c in clauses for h in c["evidence"]]
    events = {h["event_type"] for h in evidence} - {"other"}
    flags = sorted(set(flags))
    strength = round(net/(1+abs(net)), 4) if direction not in {"unknown", "neutral"} else 0.0
    return {
        "model_version": MODEL_VERSION, "normalization_version": NORMALIZATION_VERSION,
        "language": detect_text_language(normalized), "normalized_text": normalized,
        "coordinate_space": "normalized_text", "target": target or None,
        "labels": {"direction": direction, "vs_expectation": vs, "certainty": certainty, "target_type": "company", "event_type": next(iter(events)) if len(events) == 1 else "other"},
        "reaction": {"directional_strength": strength, "kind": "uncalibrated_rule_score", "predicted_return": None},
        "confidence": None, "probabilities": None,
        "quality_status": "needs_review" if flags or direction == "unknown" else "rule_supported",
        "flags": flags, "clauses": clauses, "evidence": evidence,
    }
