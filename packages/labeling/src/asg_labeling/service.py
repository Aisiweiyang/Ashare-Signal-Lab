"""标注服务：任务创建、双人标注落库、一致性计算、仲裁与金标构建。

规则（LABELING_GUIDE / RESEARCH_PROTOCOL §4）：
- is_preview=True 的 LLM 预标注永不进入金标；
- 金标 = 双人一致（AGREED）或仲裁后（ADJUDICATED）；
- 同一 (item, annotator) 只能有一条标注（DB 唯一约束）；
- 标注者对外只暴露匿名化 pseudonym。
"""

from __future__ import annotations

from datetime import datetime

from asg_domain.db import (
    AdjudicationDecision,
    AgreementRun,
    AnnotationTask,
    SentimentAnnotation,
    TaskItem,
)
from asg_domain.errors import LabelValidationError
from asg_domain.ids import config_hash
from sqlalchemy import select
from sqlalchemy.orm import Session

from asg_labeling.agreement import agreement_by_dimension, disagreement_mask

DIMS = ("direction", "vs_expectation", "certainty")


def create_task(
    session: Session,
    *,
    name: str,
    market_text_ids: list[str],
    created_by: str = "",
    guide_version: str = "1.0",
    sampling_config: dict | None = None,
) -> AnnotationTask:
    if not market_text_ids:
        raise LabelValidationError("标注任务不能为空")
    task = AnnotationTask(
        name=name,
        guide_version=guide_version,
        sampling_config=sampling_config or {},
        created_by=created_by,
    )
    session.add(task)
    session.flush()
    for i, mtid in enumerate(market_text_ids):
        session.add(TaskItem(task_id=task.task_id, market_text_id=mtid, order_no=i))
    session.commit()
    return task


def submit_annotation(
    session: Session,
    *,
    task_id: str,
    item_id: str,
    annotator: str,
    labels: dict,
    evidence_spans: list[dict] | None = None,
    rationale: str = "",
    is_preview: bool = False,
    commit: bool = True,
    check_existing: bool = True,
) -> SentimentAnnotation:
    """落一条标注。LLM 预标注必须 is_preview=True。

    commit=False 时不提交事务（批量播种用；调用方负责最终 commit），
    API 单条路径保持默认逐条提交。
    check_existing=False（阶段 17，仅供批量播种路径）：跳过 (item, annotator)
    查重 SELECT——该查询会触发 autoflush 导致逐条 INSERT（画像实证为播种
    链路 40% 耗时）；调用方必须保证全新任务无既有标注。标签值域校验保留。
    """
    from asg_domain.quality import expect_labels_in_domain

    item = session.get(TaskItem, item_id)
    if item is None or item.task_id != task_id:
        raise LabelValidationError(f"item {item_id} 不属于任务 {task_id}")
    row = {
        "id": item_id,
        "direction": labels.get("direction"),
        "vs_expectation": labels.get("vs_expectation"),
        "certainty": labels.get("certainty"),
    }
    res = expect_labels_in_domain([row])
    if not res["passed"]:
        raise LabelValidationError(f"标签值域非法: {res['violations'][:3]}")

    existing = None
    if check_existing:
        existing = session.execute(
            select(SentimentAnnotation).where(
                SentimentAnnotation.item_id == item_id,
                SentimentAnnotation.annotator_pseudonym == annotator,
            )
        ).scalar_one_or_none()
    if existing is not None:
        # 覆盖自己的 pending 标注（工作台“重新提交”）；已仲裁的项不允许再改
        if existing.quality_state in ("adjudicated",):
            raise LabelValidationError("该项已仲裁，不得再修改标注")
        existing.direction = labels["direction"]
        existing.vs_expectation = labels["vs_expectation"]
        existing.certainty = labels["certainty"]
        existing.rationale = rationale
        existing.evidence_spans = evidence_spans or []
        existing.is_preview = is_preview
        existing.submitted_at = datetime.now()
        if commit:
            session.commit()
        return existing

    ann = SentimentAnnotation(
        task_id=task_id,
        item_id=item_id,
        market_text_id=item.market_text_id,
        annotator_pseudonym=annotator,
        guide_version=_task_guide_version(session, task_id),
        direction=labels["direction"],
        vs_expectation=labels["vs_expectation"],
        certainty=labels["certainty"],
        target_type=labels.get("target_type", "company"),
        event_type=labels.get("event_type", "other"),
        evidence_spans=evidence_spans or [],
        rationale=rationale,
        is_preview=is_preview,
    )
    session.add(ann)
    if commit:
        session.commit()
    return ann


def _task_guide_version(session: Session, task_id: str) -> str:
    task = session.get(AnnotationTask, task_id)
    return task.guide_version if task else "1.0"


def compute_agreement(session: Session, task_id: str) -> AgreementRun:
    """按 item 配对双人标注计算 Kappa；并据此更新 quality_state（一致→agreed）。"""
    items = session.execute(select(TaskItem).where(TaskItem.task_id == task_id)).scalars().all()
    item_ids = [it.item_id for it in items]
    anns = session.execute(
        select(SentimentAnnotation).where(
            SentimentAnnotation.task_id == task_id,
            SentimentAnnotation.is_preview == False,  # noqa: E712 — 预标注绝不参与
        )
    ).scalars().all()
    by_item: dict[str, dict[str, SentimentAnnotation]] = {}
    for a in anns:
        by_item.setdefault(a.item_id, {})[a.annotator_pseudonym] = a

    pairs: list[tuple[dict, dict]] = []
    pair_items: list[str] = []
    for iid in item_ids:
        d = by_item.get(iid, {})
        if len(d) >= 2:
            anns_sorted = sorted(d.values(), key=lambda x: x.annotator_pseudonym)
            la = {k: getattr(anns_sorted[0], k) for k in DIMS}
            lb = {k: getattr(anns_sorted[1], k) for k in DIMS}
            pairs.append((la, lb))
            pair_items.append(iid)

    if not pairs:
        raise LabelValidationError("任务尚无成对人工标注，无法计算一致性")

    kappas = agreement_by_dimension(pairs)
    run = AgreementRun(task_id=task_id, kappas=kappas, n_items=len(item_ids), n_pairs=len(pairs), config_hash=config_hash({"task": task_id}))
    session.add(run)

    # 一致 → agreed；分歧 → 留待仲裁
    mask = disagreement_mask(pairs)
    for iid, disagree in zip(pair_items, mask, strict=True):
        for a in by_item[iid].values():
            a.quality_state = "pending" if disagree else "agreed"
    session.commit()
    return run


def adjudicate(
    session: Session,
    *,
    task_id: str,
    item_id: str,
    adjudicator: str,
    labels: dict,
    rationale: str = "",
    commit: bool = True,
    annotations: list[SentimentAnnotation] | None = None,
) -> AdjudicationDecision:
    """仲裁：写入决策并更新两条标注的 quality_state=adjudicated。

    commit=False 时不提交事务（批量播种用；调用方负责最终 commit）。
    annotations（阶段 17，仅供批量播种路径）：调用方预取的该 item 全部
    人工标注——跳过逐条 SELECT（autoflush 逐条 flush 的同源问题）；
    数量 <2 的校验对预取列表同样生效。
    """
    item = session.get(TaskItem, item_id)
    if item is None or item.task_id != task_id:
        raise LabelValidationError(f"item {item_id} 不属于任务 {task_id}")
    if annotations is None:
        anns = session.execute(
            select(SentimentAnnotation).where(
                SentimentAnnotation.item_id == item_id, SentimentAnnotation.is_preview == False  # noqa: E712
            )
        ).scalars().all()
    else:
        anns = [a for a in annotations if a.item_id == item_id and not a.is_preview]
    if len(anns) < 2:
        raise LabelValidationError("仲裁要求两条独立人工标注")
    dec = AdjudicationDecision(
        item_id=item_id,
        task_id=task_id,
        adjudicator=adjudicator,
        direction=labels["direction"],
        vs_expectation=labels["vs_expectation"],
        certainty=labels["certainty"],
        rationale=rationale,
    )
    session.add(dec)
    for a in anns:
        a.quality_state = "adjudicated"
    if commit:
        session.commit()
    return dec


def build_gold_set(session: Session, task_id: str) -> list[dict]:
    """金标集：agreed（取一致值）+ adjudicated（取仲裁值）。预标注永不出现在这里。"""
    items = session.execute(select(TaskItem).where(TaskItem.task_id == task_id)).scalars().all()
    anns = session.execute(
        select(SentimentAnnotation).where(
            SentimentAnnotation.task_id == task_id,
            SentimentAnnotation.is_preview == False,  # noqa: E712
        )
    ).scalars().all()
    decs = session.execute(select(AdjudicationDecision).where(AdjudicationDecision.task_id == task_id)).scalars().all()
    dec_by_item = {d.item_id: d for d in decs}
    ann_by_item: dict[str, list[SentimentAnnotation]] = {}
    for a in anns:
        ann_by_item.setdefault(a.item_id, []).append(a)

    gold: list[dict] = []
    for it in items:
        ann_list = sorted(ann_by_item.get(it.item_id, []), key=lambda x: x.annotator_pseudonym)
        if not ann_list:
            continue
        dec = dec_by_item.get(it.item_id)
        if dec is not None:
            labels = {k: getattr(dec, k) for k in DIMS}
            source = "adjudicated"
            annotators = [a.annotator_pseudonym for a in ann_list] + [dec.adjudicator]
        elif len(ann_list) >= 2 and all(a.direction == ann_list[0].direction and a.vs_expectation == ann_list[0].vs_expectation and a.certainty == ann_list[0].certainty for a in ann_list[:2]):
            labels = {k: getattr(ann_list[0], k) for k in DIMS}
            source = "agreed"
            annotators = [a.annotator_pseudonym for a in ann_list[:2]]
        else:
            continue  # 分歧未仲裁：不进金标
        first = ann_list[0]
        gold.append(
            {
                "item_id": it.item_id,
                "market_text_id": it.market_text_id,
                **labels,
                "target_type": first.target_type,
                "event_type": first.event_type,
                "source": source,
                "annotator_pseudonyms": annotators,  # 已匿名
                "guide_version": first.guide_version,
            }
        )
    return gold


def simulate_annotators(
    latent_rows: list[dict],
    *,
    seed: int,
    error_rate: float = 0.08,
    annotators: tuple[str, str] = ("annotator_A", "annotator_B"),
) -> list[tuple[dict, dict]]:
    """合成数据的模拟标注者：以 latent 为真值，按 error_rate 独立翻错（非金标，用于演示闭环）。

    翻错规则：direction 在 {bullish,bearish,neutral} 内轮转；vs/certainty 在非 unknown 值域内轮转。
    两人独立采样 → 天然产生分歧与 <1 的 Kappa（诚实、可配置）。
    """
    import numpy as np
    from asg_domain.enums import Certainty, Direction, VsExpectation

    rng = np.random.default_rng(seed)
    dir_cycle = {Direction.BULLISH: Direction.NEUTRAL, Direction.NEUTRAL: Direction.BEARISH, Direction.BEARISH: Direction.BULLISH}
    vs_cycle = {
        VsExpectation.ABOVE: VsExpectation.INLINE,
        VsExpectation.INLINE: VsExpectation.BELOW,
        VsExpectation.BELOW: VsExpectation.NO_EXPECTATION,
        VsExpectation.NO_EXPECTATION: VsExpectation.ABOVE,
    }
    cert_cycle = {
        Certainty.CERTAIN: Certainty.SPECULATIVE,
        Certainty.SPECULATIVE: Certainty.CONDITIONAL,
        Certainty.CONDITIONAL: Certainty.CERTAIN,
    }

    def _flip(row: dict) -> dict:
        out = dict(row)
        if rng.random() < error_rate and out["direction"] in dir_cycle:
            out["direction"] = dir_cycle[Direction(out["direction"])].value
        if rng.random() < error_rate and out["vs_expectation"] in vs_cycle:
            out["vs_expectation"] = vs_cycle[VsExpectation(out["vs_expectation"])].value
        if rng.random() < error_rate and out["certainty"] in cert_cycle:
            out["certainty"] = cert_cycle[Certainty(out["certainty"])].value
        return out

    result = []
    for row in latent_rows:
        a = _flip(row)
        b = _flip(row)
        result.append(
            (
                {"direction": a["direction"], "vs_expectation": a["vs_expectation"], "certainty": a["certainty"]},
                {"direction": b["direction"], "vs_expectation": b["vs_expectation"], "certainty": b["certainty"]},
            )
        )
    return result
