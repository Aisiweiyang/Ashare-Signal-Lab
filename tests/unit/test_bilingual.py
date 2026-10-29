"""Behavior contracts for bilingual NLP, including evidence and abstentions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asg_models import BilingualFineGrainedBaseline, analyze_bilingual
from asg_models.bilingual_baseline import tokenize_bilingual
from asg_text_pipeline.bilingual import detect_text_language, normalize_bilingual

CORPUS = json.loads((Path(__file__).resolve().parents[2]/"research/fixtures/bilingual_challenge_v2.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda c: c["id"])
def test_handcrafted_bilingual_challenge(case):
    result = analyze_bilingual(case["text"], **{k: case[k] for k in ("target", "aliases", "other_targets") if k in case})
    for key, expected in case["expected"].items():
        assert result["labels"][key] == expected, result
    # All evidence must be an actual substring of the stated coordinate space.
    for evidence in result["evidence"]:
        assert result["normalized_text"][evidence["start"]:evidence["end"]] == evidence["quote"]
    assert result["confidence"] is None
    assert result["reaction"]["predicted_return"] is None


def test_normalization_preserves_money_signs_and_decodes_entities():
    text = normalize_bilingual("<p>公司&nbsp;EPS −１．２０</p><p>合同１２３４５６７８９０１２３元</p><style>loss</style>")
    assert "EPS -1.20" in text and "1234567890123元" in text
    assert "loss" not in text and "\n" in text and "&nbsp;" not in text


def test_no_substring_double_count_and_negation_does_not_cross_clauses():
    result = analyze_bilingual("公司未获批，利润同比增长30%。")
    assert result["clauses"][0]["labels"]["direction"] == "bearish"
    assert result["clauses"][1]["labels"]["direction"] == "bullish"
    assert len(analyze_bilingual("利润大增。")["evidence"]) == 1
    assert len(analyze_bilingual("利润同比增长30%。")["evidence"]) == 1


def test_numeric_unit_mismatch_abstains_from_comparison():
    result = analyze_bilingual("Revenue grew 30% versus expected $50.")
    assert "numeric_units_not_comparable" in result["flags"]
    assert all(c["comparison"] is None for c in result["clauses"])
    assert result["labels"]["vs_expectation"] != "below"


@pytest.mark.parametrize(("text", "direction"), [
    ("Acme reported costs increased.", "bearish"),
    ("Acme reported expenses declined.", "bullish"),
    ("公司公告成本下降。", "bullish"),
    ("Costs were above expectations.", "bearish"),
    ("Expenses were below expectations.", "bullish"),
    ("The rose is red.", "unknown"),
])
def test_financial_metric_sign_and_out_of_domain(text, direction):
    assert analyze_bilingual(text)["labels"]["direction"] == direction


def test_negated_numeric_reference_is_not_a_surprise_fact():
    result = analyze_bilingual("Revenue grew 30%, not expected at 50%.")
    assert "numeric_negated_reference" in result["flags"]
    assert all(c["comparison"] is None for c in result["clauses"])


def test_numeric_periods_and_metrics_are_not_guessed():
    for text, flag in [
        ("2025 revenue grew 30%, expected 50% in 2026.", "numeric_periods_not_comparable"),
        ("Revenue grew 30% but profit was expected at 50%.", "numeric_multiple_metrics"),
    ]:
        result = analyze_bilingual(text)
        assert flag in result["flags"]
        assert all(c["comparison"] is None for c in result["clauses"])


def test_target_evidence_excludes_other_company():
    result = analyze_bilingual("Beta reported losses. Acme announced buybacks.", target="Acme", other_targets=["Beta"])
    assert result["labels"]["direction"] == "bullish"
    assert all("Beta" not in clause["text"] for clause in result["clauses"])
    with pytest.raises(ValueError):
        analyze_bilingual("Acme reported growth.", target="Acme", other_targets=["Acme"])


def test_limits_language_and_empty_input():
    assert detect_text_language("利润") == "zh"
    assert detect_text_language("profits") == "en"
    assert detect_text_language("利润 EPS") == "mixed"
    assert analyze_bilingual("<script>profits grew</script>")["labels"]["direction"] == "unknown"
    with pytest.raises(ValueError):
        analyze_bilingual("x"*20001)
    result = analyze_bilingual("利润增长。"*201)
    assert "clause_limit_exceeded" in result["flags"]
    assert result["labels"]["direction"] == "unknown"


def test_bilingual_tokens_and_shared_three_head_fit():
    tokens = tokenize_bilingual("公司利润 didn't fall ２０％")
    assert "didn't" in tokens and "fall" in tokens and "20%" in tokens
    assert "公司利润" not in tokens
    rows = []
    for i in range(6):
        rows.extend([
            {"text": f"Acme reported profit growth 利润增长{i}", "direction": "bullish", "vs_expectation": "above", "certainty": "certain"},
            {"text": f"Beta might report losses 可能亏损{i}", "direction": "bearish", "vs_expectation": "below", "certainty": "speculative"},
        ])
    model = BilingualFineGrainedBaseline(seed=7).fit(rows)
    result = model.predict_one("Acme reported profit growth 利润增长")
    assert result["labels"]["direction"] == "bullish"
    assert result["meta"]["model"] == "tfidf_bilingual_v2_1"
    assert model.predict_batch(["Beta might report losses"])[0]["labels"]["direction"] == "bearish"
    texts = ["Acme profit growth", "Beta might report losses", "公司利润增长"]
    assert model.predict_batch(texts) == [model.predict_one(text) for text in texts]


def test_numeric_overflow_is_isolated_and_result_is_json_safe():
    result = analyze_bilingual("Revenue was $" + "9"*400 + " versus expected $50.")
    assert "numeric_out_of_range" in result["flags"]
    json.dumps(result, allow_nan=False)
