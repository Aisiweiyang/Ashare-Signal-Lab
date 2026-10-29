"""API 集成流程测试：登录 → 播种 → 标注 → 一致性 → 实验 → 导出（验收项 5/7 的 API 层）。"""

from __future__ import annotations

import pytest

from tests.conftest import auth_headers

pytestmark = pytest.mark.integration


def test_full_research_flow_via_api(api_client):
    h = auth_headers(api_client, "admin")

    # 1) 播种演示数据
    r = api_client.post("/api/data/seed-demo?n_events=40&seed=7", headers=h)
    assert r.status_code == 200
    seed_stats = r.json()
    assert seed_stats["n_gold"] > 10

    # 2) 文本可浏览（分页 + 筛选）
    r = api_client.get("/api/texts?page=1&page_size=5", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0 and len(body["items"]) <= 5
    symbol = body["items"][0]["security_symbol"]
    if symbol:
        r2 = api_client.get(f"/api/texts?security_symbol={symbol}", headers=h)
        assert r2.status_code == 200

    # 3) 标注任务与条目
    r = api_client.get("/api/labeling/tasks", headers=h)
    tasks = r.json()["items"]
    assert tasks
    task_id = tasks[0]["task_id"]
    items = api_client.get(f"/api/labeling/tasks/{task_id}/items", headers=h).json()["items"]
    assert items

    # 4) 研究员提交人工标注 + 一致性
    hr = auth_headers(api_client, "researcher")
    r = api_client.post(
        "/api/labeling/annotations",
        headers=hr,
        json={"item_id": items[0]["item_id"], "direction": "neutral", "vs_expectation": "inline", "certainty": "certain", "rationale": "api-flow"},
    )
    assert r.status_code == 200
    r = api_client.post(f"/api/labeling/tasks/{task_id}/agreement", headers=hr)
    assert r.status_code == 200
    assert "direction" in r.json()["kappas"]

    # 5) 金标查询
    r = api_client.get(f"/api/labeling/tasks/{task_id}/gold", headers=hr)
    assert r.status_code == 200
    assert r.json()["n"] > 0

    # 6) 行情数据 + 质量检查
    r = api_client.get("/api/data/securities", headers=h)
    assert r.status_code == 200 and r.json()["items"]
    sec_id = r.json()["items"][0]["security_id"]
    r = api_client.get(f"/api/data/price-bars/{sec_id}", headers=h)
    assert r.status_code == 200 and r.json()["items"]
    r = api_client.get("/api/data/quality", headers=h)
    assert r.status_code == 200

    # 7) 运行实验（mock provider）
    r = api_client.post(
        "/api/experiments/run",
        headers=hr,
        json={"name": "api_flow_exp", "seed": 7, "n_events": 70, "folds": 2, "provider": "mock"},
    )
    assert r.status_code == 200, r.text
    manifest = r.json()
    assert manifest["model_scores"]
    assert "h1_supported" in manifest["ic_gold_fine_vs_dir3"]

    # 8) 导出（金标 JSONL + 实体 contract）
    r = api_client.get(f"/api/export/gold/{task_id}", headers=hr)
    assert r.status_code == 200
    assert "direction" in r.text
    r = api_client.get("/api/export/entities", headers=hr)
    assert r.status_code == 200
    assert r.json()["contract_version"] == "1.0"

    # 9) 审计记录了关键动作（admin）
    r = api_client.get("/api/admin/audit", headers=h)
    actions = {a["action"] for a in r.json()["items"]}
    assert "data.seed_demo" in actions
    assert "experiment.run" in actions
