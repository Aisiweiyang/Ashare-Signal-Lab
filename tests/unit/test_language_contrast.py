"""Aligned translations, changed meaning, and conservative scope contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asg_models import analyze_bilingual
from asg_models.bilingual_baseline import tokenize_bilingual

CORPUS = json.loads((Path(__file__).resolve().parents[2]/"research/fixtures/language_contrast_v21.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("pair", CORPUS["pairs"], ids=lambda p: p["id"])
def test_same_meaning_translations_satisfy_contract(pair):
    results = [analyze_bilingual(**pair[lang]) for lang in ("zh", "en")]
    for result in results:
        for key, expected in pair["expected"].items():
            assert result["labels"][key] == expected, result
        assert set(pair.get("flags", [])) <= set(result["flags"])
        comparisons = [c["comparison"] for c in result["clauses"] if c["comparison"]]
        if "comparison" in pair:
            if pair["comparison"] is None:
                assert not comparisons
            else:
                assert len(comparisons) == 1
                for key, expected in pair["comparison"].items():
                    assert comparisons[0][key] == pytest.approx(expected) if isinstance(expected, (float, int)) else comparisons[0][key] == expected
        for evidence in result["evidence"]:
            assert result["normalized_text"][evidence["start"]:evidence["end"]] == evidence["quote"]
    for key in ("direction", "vs_expectation", "certainty"):
        assert results[0]["labels"][key] == results[1]["labels"][key]


@pytest.mark.parametrize(("positive", "negative"), [
    ("公司利润增长。", "公司利润没有增长。"),
    ("Profits increased.", "Profits didn't increase."),
    ("The drug has been approved.", "The drug hasn't been approved."),
    ("公司亏损减少。", "公司亏损扩大。"),
    ("Company losses fell.", "Company profits fell."),
    ("Company losses decreased.", "Company losses increased."),
])
def test_changed_meaning_must_change_direction(positive, negative):
    assert analyze_bilingual(positive)["labels"]["direction"] == "bullish"
    assert analyze_bilingual(negative)["labels"]["direction"] == "bearish"


@pytest.mark.parametrize("text", [
    "公司利润下降至100万元，预期80万元。",
    "Company profit fell to CNY 1 million versus expected CNY 0.8 million.",
])
def test_decline_to_a_level_is_not_a_negative_amount(text):
    result = analyze_bilingual(text)
    comparison = result["clauses"][0]["comparison"]
    assert comparison["basis"] == "level"
    assert comparison["actual"] == 1000000
    assert comparison["expected"] == 800000
    assert result["labels"]["vs_expectation"] == "above"


@pytest.mark.parametrize("text", [
    "公司毛利率提高至30%，预期提高5%。",
    "Company gross margin rose to 30% versus expected increase of 5%.",
])
def test_same_percent_symbol_does_not_justify_comparing_level_and_growth(text):
    result = analyze_bilingual(text)
    assert "numeric_bases_not_comparable" in result["flags"]
    assert all(c["comparison"] is None for c in result["clauses"])


@pytest.mark.parametrize("text", [
    "In May, the company reported profits grew.",
    "The company reported May profits grew.",
    "On May 2 the company reported profits grew.",
])
def test_month_name_does_not_make_a_fact_speculative(text):
    assert analyze_bilingual(text)["labels"]["certainty"] == "certain"
    assert analyze_bilingual("The company may report profit growth.")["labels"]["certainty"] == "speculative"


def test_dotted_acronyms_decimals_and_company_abbreviations_keep_evidence():
    result = analyze_bilingual("Acme Inc. reported U.S. revenue grew. Acme EPS was $1.20 versus expected $1.50.", target="Acme")
    assert len(result["clauses"]) == 2
    assert "U.S." in result["clauses"][0]["text"]
    assert result["clauses"][1]["comparison"]["actual"] == 1.2


def test_thousands_and_currency_are_preserved_in_trainable_features():
    tokens = tokenize_bilingual("Revenue was $1,000.50; profit didn't fall 20%.")
    assert "1000.50" in tokens and "$" in tokens
    assert "didn't" in tokens and "20%" in tokens
    assert "000.50" not in tokens
