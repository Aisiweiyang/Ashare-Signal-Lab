"""Integration coverage for the scaled synthetic gold-label pipeline."""

from __future__ import annotations

import pytest
from asg_connectors.synthetic import SyntheticConnector
from asg_domain.db import AnnotationTask, MarketText, SourceDocument
from asg_domain.enums import Certainty, Direction, VsExpectation
from asg_labeling.service import build_gold_set
from asg_pipeline import ExperimentConfig, run_full_experiment, seed_demo
from sqlalchemy import select

pytestmark = pytest.mark.integration

SEED = 20260912
N_EVENTS = 700
N_DAYS = 180


def test_seed_demo_scales_gold_through_full_labeling_chain(db):
    stats = seed_demo(db, seed=SEED, n_events=N_EVENTS, n_days=N_DAYS)

    task = db.get(AnnotationTask, stats["task_id"])
    assert task is not None
    gold = build_gold_set(db, task.task_id)
    assert stats["n_gold"] == len(gold)
    assert len(gold) >= 500

    valid_values = {
        "direction": {item.value for item in Direction},
        "vs_expectation": {item.value for item in VsExpectation},
        "certainty": {item.value for item in Certainty},
    }
    for row in gold:
        assert row["source"] in {"agreed", "adjudicated"}
        for dimension, values in valid_values.items():
            assert row[dimension] in values

    assert set(stats["kappa"]) == set(valid_values)
    assert all(value < 1.0 for value in stats["kappa"].values())

    # 转载排除：ingest 落库的 SourceDocument.meta 不含 is_repost（只有 external_id/
    # symbol_hint），必须用同 seed 生成器重建真值映射来核验；键缺失即失败，
    # 禁止用 .get(..., False) 之类默认值掩盖缺键。
    conn = SyntheticConnector(seed=SEED, n_events=N_EVENTS, n_days=N_DAYS)
    repost_by_external = {f"syn-{ev.event_seq}": ev.is_repost for ev in conn.events}
    assert repost_by_external  # 真值映射非空

    text_ids = [row["market_text_id"] for row in gold]
    gold_texts = db.execute(
        select(MarketText, SourceDocument)
        .join(SourceDocument, SourceDocument.source_doc_id == MarketText.source_doc_id)
        .where(MarketText.market_text_id.in_(text_ids))
    ).all()
    assert len(gold_texts) == len(gold)
    for _, source in gold_texts:
        ext = (source.meta or {}).get("external_id")
        assert ext in repost_by_external, f"金标文本的 external_id 不在真值映射中: {ext!r}"
        assert repost_by_external[ext] is False, f"金标包含转载文本: {ext!r}"


def test_scaled_full_experiment_meets_protocol_sample_floor(db, tmp_path):
    cfg = ExperimentConfig(
        name="it_gold_scaling",
        seed=SEED,
        n_events=N_EVENTS,
        n_days=N_DAYS,
        alpha=0.012,
        provider="mock",
        folds=3,
        embargo_days=2,
        horizons=(1, 2),
        event_type_ablation=True,
        split_scheme="walk_forward",
    )

    manifest = run_full_experiment(db, cfg, runs_dir=tmp_path)

    assert "h1_verdict" in manifest
    assert manifest["n_oos_events"] > 200
    assert manifest["timings_seconds"]
    assert manifest["ic_by_event_type_gold_exploratory"]
    assert (tmp_path / f"{manifest['run_id']}.json").exists()
