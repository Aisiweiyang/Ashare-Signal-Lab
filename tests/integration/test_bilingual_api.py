from __future__ import annotations

import pytest

from tests.conftest import auth_headers

pytestmark = pytest.mark.integration


def test_bilingual_api_auth_single_batch_and_validation(api_client):
    assert api_client.post("/api/analysis/text", json={"text": "Acme grew."}).status_code == 401
    headers = auth_headers(api_client, "viewer")
    result = api_client.post("/api/analysis/text", headers=headers, json={"text": "Acme reported EPS of $1.20 versus expected $1.50.", "target": "Acme"})
    assert result.status_code == 200, result.text
    assert result.json()["labels"]["vs_expectation"] == "below"
    assert result.json()["confidence"] is None
    batch = api_client.post("/api/analysis/batch", headers=headers, json={"items": [{"text": "未获批新药。"}, {"text": "Acme may be profitable."}]})
    assert batch.status_code == 200 and batch.json()["n"] == 2
    assert batch.json()["items"][0]["labels"]["direction"] == "bearish"
    units = api_client.post("/api/analysis/batch", headers=headers, json={"items": [
        {"text": "公司毛利率提高50个基点，预期提高0.4个百分点。"},
        {"text": "Company gross margin rose 50 basis points versus expected 0.4 percentage points."},
        {"text": "The company drug hasn't been approved."},
    ]})
    assert units.status_code == 200
    first, second, negative = units.json()["items"]
    assert first["clauses"][0]["comparison"] == second["clauses"][0]["comparison"]
    assert first["clauses"][0]["comparison"]["actual"] == 0.5
    assert negative["labels"]["direction"] == "bearish"
    assert negative["model_version"] == "bilingual_finance_rules_v2.1"
    for body in [{"text": " "}, {"text": "x"*18001}, {"text": "growth", "aliases": ["Acme"]}, {"text": "Acme grew.", "target": "Acme", "other_targets": ["Acme"]}]:
        assert api_client.post("/api/analysis/text", headers=headers, json=body).status_code == 422
    assert api_client.post("/api/analysis/batch", headers=headers, json={"items": [{"text": "x"}]*33}).status_code == 422
    assert api_client.post("/api/analysis/batch", headers=headers, json={"items": [{"text": "x"*17000}]*4}).status_code == 422


def test_stored_text_analysis_does_not_mutate_research_text(api_client):
    headers = auth_headers(api_client, "admin")
    assert api_client.post("/api/data/seed-demo?seed=7&n_events=30", headers=headers).status_code == 200
    item = api_client.get("/api/texts?page_size=1", headers=headers).json()["items"][0]
    original = api_client.get(f"/api/texts/{item['market_text_id']}", headers=headers).json()
    result = api_client.get(f"/api/analysis/market-text/{item['market_text_id']}", headers=headers)
    assert result.status_code == 200
    assert result.json()["market_text_id"] == item["market_text_id"]
    assert result.json()["security_id"]
    assert api_client.get(f"/api/texts/{item['market_text_id']}", headers=headers).json() == original
    assert api_client.get("/api/analysis/market-text/missing", headers=headers).status_code == 404
    # CSV sources can contain documents longer than the interactive limit.
    # The stored-text route must report an actionable 422, not a server error.
    from asg_domain.db import MarketText

    with api_client.app.state.db_session_factory() as db:
        row = db.get(MarketText, item["market_text_id"])
        row.cleaned_text = "公司利润增长"*4000
        db.commit()
    too_long = api_client.get(f"/api/analysis/market-text/{item['market_text_id']}", headers=headers)
    assert too_long.status_code == 422 and "20000" in too_long.json()["detail"]
