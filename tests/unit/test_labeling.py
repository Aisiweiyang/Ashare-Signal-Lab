"""标注服务测试：任务、标注、预标注隔离、一致性、仲裁、金标、模拟标注者。"""

from __future__ import annotations

import pytest
from asg_domain.db import MarketText, Security, SourceDocument
from asg_domain.errors import LabelValidationError
from asg_labeling import (
    adjudicate,
    build_gold_set,
    cohens_kappa,
    compute_agreement,
    create_task,
    label_distribution,
    simulate_annotators,
    submit_annotation,
)


@pytest.fixture()
def text_ids(db):
    secs = [Security(symbol=f"00000{i}", exchange="SZSE", name=f"测试公司{i}") for i in range(1)]
    db.add_all(secs)
    db.flush()
    docs = []
    texts = []
    for i in range(4):
        d = SourceDocument(source_id="t", title=f"t{i}", raw_content=f"正文{i}", content_hash=f"h{i}", meta={"external_id": f"e{i}"})
        db.add(d)
        docs.append(d)
        db.flush()
        t = MarketText(source_doc_id=d.source_doc_id, security_id=secs[0].security_id, title=f"t{i}", cleaned_text=f"测试公司{i}公告正文内容足够长{i}", text_hash=f"th{i}")
        db.add(t)
        texts.append(t)
    db.flush()
    return [t.market_text_id for t in texts]


GOOD = {"direction": "bullish", "vs_expectation": "above", "certainty": "certain"}


class TestKappa:
    def test_perfect(self):
        assert cohens_kappa(["a", "b"], ["a", "b"]) == 1.0

    def test_chance_and_negative(self):
        assert cohens_kappa(["a", "a"], ["a", "b"]) < 1.0

    def test_single_category_agree(self):
        assert cohens_kappa(["a", "a"], ["a", "a"]) == 1.0

    def test_length_mismatch(self):
        with pytest.raises(ValueError):
            cohens_kappa(["a"], [])

    def test_empty(self):
        with pytest.raises(ValueError):
            cohens_kappa([], [])


class TestLabelingService:
    def test_task_and_annotation_flow(self, db, text_ids):
        task = create_task(db, name="T1", market_text_ids=text_ids)
        from asg_domain.db import TaskItem
        from sqlalchemy import select

        items = db.execute(select(TaskItem).where(TaskItem.task_id == task.task_id)).scalars().all()
        assert len(items) == 4
        submit_annotation(db, task_id=task.task_id, item_id=items[0].item_id, annotator="A", labels=GOOD)
        ann = submit_annotation(db, task_id=task.task_id, item_id=items[0].item_id, annotator="B", labels=GOOD)
        assert ann.quality_state == "pending"
        # 同一标注者重复提交 → 覆盖
        submit_annotation(db, task_id=task.task_id, item_id=items[0].item_id, annotator="A", labels=GOOD)
        # 一致 → agreed
        run = compute_agreement(db, task.task_id)
        assert run.n_pairs >= 1

    def test_invalid_labels_rejected(self, db, text_ids):
        from asg_domain.db import TaskItem
        from sqlalchemy import select

        task = create_task(db, name="T2", market_text_ids=text_ids)
        item = db.execute(select(TaskItem).where(TaskItem.task_id == task.task_id)).scalars().first()
        with pytest.raises(LabelValidationError):
            submit_annotation(db, task_id=task.task_id, item_id=item.item_id, annotator="A", labels={"direction": "涨", "vs_expectation": "above", "certainty": "certain"})

    def test_wrong_task_item(self, db, text_ids):
        with pytest.raises(LabelValidationError):
            submit_annotation(db, task_id="tsk_missing", item_id="itm_missing", annotator="A", labels=GOOD)

    def test_preview_never_in_gold(self, db, text_ids):
        from asg_domain.db import TaskItem
        from sqlalchemy import select

        task = create_task(db, name="T3", market_text_ids=text_ids)
        items = db.execute(select(TaskItem).where(TaskItem.task_id == task.task_id)).scalars().all()
        # 只有 LLM 预标注 → 金标为空
        submit_annotation(db, task_id=task.task_id, item_id=items[0].item_id, annotator="llm_bot", labels=GOOD, is_preview=True)
        assert build_gold_set(db, task.task_id) == []
        # 双人一致后进入金标
        submit_annotation(db, task_id=task.task_id, item_id=items[0].item_id, annotator="A", labels=GOOD)
        submit_annotation(db, task_id=task.task_id, item_id=items[0].item_id, annotator="B", labels=GOOD)
        gold = build_gold_set(db, task.task_id)
        assert len(gold) == 1 and gold[0]["source"] == "agreed"
        # 预标注没有污染金标（一致值来自人工）
        assert "llm_bot" not in gold[0]["annotator_pseudonyms"]

    def test_adjudication_flow(self, db, text_ids):
        from asg_domain.db import TaskItem
        from sqlalchemy import select

        task = create_task(db, name="T4", market_text_ids=text_ids)
        items = db.execute(select(TaskItem).where(TaskItem.task_id == task.task_id)).scalars().all()
        submit_annotation(db, task_id=task.task_id, item_id=items[0].item_id, annotator="A", labels=GOOD)
        submit_annotation(db, task_id=task.task_id, item_id=items[0].item_id, annotator="B", labels={"direction": "bearish", "vs_expectation": "below", "certainty": "certain"})
        compute_agreement(db, task.task_id)
        # 分歧未仲裁 → 不进金标
        assert build_gold_set(db, task.task_id) == []
        dec = adjudicate(db, task_id=task.task_id, item_id=items[0].item_id, adjudicator="ADJ", labels={"direction": "bullish", "vs_expectation": "above", "certainty": "certain"})
        assert dec.decision_id.startswith("adj_")
        gold = build_gold_set(db, task.task_id)
        assert gold[0]["source"] == "adjudicated"
        # 仲裁后不允许再改标注
        with pytest.raises(LabelValidationError):
            submit_annotation(db, task_id=task.task_id, item_id=items[0].item_id, annotator="A", labels=GOOD)

    def test_adjudicate_requires_two_annotations(self, db, text_ids):
        from asg_domain.db import TaskItem
        from sqlalchemy import select

        task = create_task(db, name="T5", market_text_ids=text_ids)
        item = db.execute(select(TaskItem).where(TaskItem.task_id == task.task_id)).scalars().first()
        submit_annotation(db, task_id=task.task_id, item_id=item.item_id, annotator="A", labels=GOOD)
        with pytest.raises(LabelValidationError):
            adjudicate(db, task_id=task.task_id, item_id=item.item_id, adjudicator="ADJ", labels=GOOD)

    def test_agreement_requires_pairs(self, db, text_ids):
        from asg_domain.db import TaskItem
        from sqlalchemy import select

        task = create_task(db, name="T6", market_text_ids=text_ids)
        item = db.execute(select(TaskItem).where(TaskItem.task_id == task.task_id)).scalars().first()
        submit_annotation(db, task_id=task.task_id, item_id=item.item_id, annotator="A", labels=GOOD)
        with pytest.raises(LabelValidationError):
            compute_agreement(db, task.task_id)


class TestSimulatedAnnotators:
    def test_produces_realistic_kappa(self):
        rows = [
            {"direction": "bullish", "vs_expectation": "above", "certainty": "certain"},
            {"direction": "bearish", "vs_expectation": "below", "certainty": "speculative"},
        ] * 10
        pairs = simulate_annotators(rows, seed=1, error_rate=0.08)
        from asg_labeling import agreement_by_dimension

        kappas = agreement_by_dimension(pairs)
        # 错误率 8% → kappa 高但小于 1
        assert 0.5 < kappas["direction"] <= 1.0

    def test_zero_error_perfect_agreement(self):
        rows = [{"direction": "bullish", "vs_expectation": "above", "certainty": "certain"}] * 5
        pairs = simulate_annotators(rows, seed=2, error_rate=0.0)
        assert all(a == b for a, b in pairs)

    def test_label_distribution(self):
        d = label_distribution(["a", "a", "b"])
        assert d == {"a": 0.6667, "b": 0.3333}


# ---------------------------------------------------------------------------
# 阶段 17：批量播种参数（API 默认路径不变）
# ---------------------------------------------------------------------------


def _mk_task(db) -> tuple[str, list[str]]:
    from asg_domain.db import MarketText, TaskItem
    from asg_labeling.service import create_task
    from sqlalchemy import select

    ids = [t.market_text_id for t in db.execute(select(MarketText)).scalars().all()[:3]]
    task = create_task(db, name="批量任务", market_text_ids=ids, created_by="t")
    item_ids = [
        i.item_id for i in db.execute(select(TaskItem).where(TaskItem.task_id == task.task_id)).scalars().all()
    ]
    return task.task_id, item_ids


def test_submit_annotation_batch_flag_skips_lookup_but_validates(seeded_db):
    from asg_domain.errors import LabelValidationError
    from asg_labeling.service import submit_annotation

    task_id, ids = _mk_task(seeded_db)
    labels = {"direction": "bullish", "vs_expectation": "above", "certainty": "certain"}
    ann = submit_annotation(
        seeded_db, task_id=task_id, item_id=ids[0], annotator="a1", labels=labels,
        check_existing=False, commit=False,
    )
    seeded_db.flush()
    assert ann.direction == "bullish" and ann.guide_version
    # 非法值域仍然被拦截（批量路径不绕过校验）
    import pytest

    with pytest.raises(LabelValidationError):
        submit_annotation(
            seeded_db, task_id=task_id, item_id=ids[1], annotator="a1",
            labels={"direction": "nope", "vs_expectation": "above", "certainty": "certain"},
            check_existing=False, commit=False,
        )


def test_adjudicate_with_preloaded_annotations(seeded_db):
    from asg_domain.db import SentimentAnnotation
    from asg_labeling.service import submit_annotation
    from sqlalchemy import select

    task_id, ids = _mk_task(seeded_db)
    labels = {"direction": "bearish", "vs_expectation": "below", "certainty": "speculative"}
    for who in ("a1", "a2"):
        submit_annotation(seeded_db, task_id=task_id, item_id=ids[0], annotator=who, labels=labels, commit=False)
    seeded_db.flush()
    anns = seeded_db.execute(
        select(SentimentAnnotation).where(SentimentAnnotation.item_id == ids[0])
    ).scalars().all()
    from asg_labeling.service import adjudicate

    dec = adjudicate(
        seeded_db, task_id=task_id, item_id=ids[0], adjudicator="oracle",
        labels=labels, annotations=anns, commit=False,
    )
    assert dec.item_id == ids[0]
    for a in anns:
        assert a.quality_state == "adjudicated"
