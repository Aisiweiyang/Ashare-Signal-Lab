"""标注路由：任务管理、标注提交（LLM 预标注隔离）、一致性、仲裁、金标查询。"""

from __future__ import annotations

from asg_domain.db import (
    AdjudicationDecision,
    AnnotationTask,
    SentimentAnnotation,
    TaskItem,
)
from asg_labeling.service import (
    adjudicate,
    build_gold_set,
    compute_agreement,
    create_task,
    submit_annotation,
)
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from asg_api.deps import get_db, require, write_audit

router = APIRouter(prefix="/api/labeling", tags=["标注"])


class CreateTaskRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    market_text_ids: list[str] = Field(min_length=1, max_length=2000)


class AnnotationRequest(BaseModel):
    item_id: str
    direction: str
    vs_expectation: str
    certainty: str
    target_type: str = "company"
    event_type: str = "other"
    evidence_spans: list[dict] = []
    rationale: str = Field(default="", max_length=500)
    is_preview: bool = False


class AdjudicateRequest(BaseModel):
    item_id: str
    direction: str
    vs_expectation: str
    certainty: str
    rationale: str = Field(default="", max_length=500)


@router.get("/tasks")
def list_tasks(user=Depends(require("texts.read")), db: Session = Depends(get_db)):
    tasks = db.execute(select(AnnotationTask).order_by(AnnotationTask.created_at.desc())).scalars().all()
    out = []
    for t in tasks:
        n_items = len(db.execute(select(TaskItem).where(TaskItem.task_id == t.task_id)).scalars().all())
        out.append({"task_id": t.task_id, "name": t.name, "status": t.status, "guide_version": t.guide_version, "n_items": n_items, "created_by": t.created_by})
    return {"items": out}


@router.post("/tasks")
def create_task_api(body: CreateTaskRequest, user=Depends(require("labeling.agreement")), db: Session = Depends(get_db)):
    task = create_task(db, name=body.name, market_text_ids=body.market_text_ids, created_by=user.username)
    write_audit(db, actor=user.username, action="task.create", target=task.task_id)
    return {"task_id": task.task_id, "n_items": len(body.market_text_ids)}


@router.get("/tasks/{task_id}/items")
def list_task_items(task_id: str, user=Depends(require("texts.read")), db: Session = Depends(get_db)):
    items = db.execute(select(TaskItem).where(TaskItem.task_id == task_id).order_by(TaskItem.order_no)).scalars().all()
    out = []
    for it in items:
        anns = db.execute(select(SentimentAnnotation).where(SentimentAnnotation.item_id == it.item_id)).scalars().all()
        mine = next((a for a in anns if a.annotator_pseudonym == user.username), None)
        # LLM 预标注：仅作为预览展示，永不计入金标（is_preview 隔离）
        preview = next((a for a in anns if a.is_preview), None)
        out.append(
            {
                "item_id": it.item_id,
                "market_text_id": it.market_text_id,
                "order_no": it.order_no,
                "my_annotation": None
                if mine is None
                else {k: getattr(mine, k) for k in ("direction", "vs_expectation", "certainty")},
                "preview_labels": None
                if preview is None
                else {k: getattr(preview, k) for k in ("direction", "vs_expectation", "certainty")},
                "quality_state": (mine.quality_state if mine else (preview.quality_state if preview else "未标注")),
            }
        )
    return {"items": out}


@router.post("/annotations")
def submit_annotation_api(body: AnnotationRequest, user=Depends(require("labeling.annotate")), db: Session = Depends(get_db)):
    try:
        ann = submit_annotation(
            db,
            task_id=_task_of_item(db, body.item_id),
            item_id=body.item_id,
            annotator=user.username,
            labels={
                "direction": body.direction,
                "vs_expectation": body.vs_expectation,
                "certainty": body.certainty,
                "target_type": body.target_type,
                "event_type": body.event_type,
            },
            evidence_spans=body.evidence_spans,
            rationale=body.rationale,
            is_preview=body.is_preview,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)[:200]) from e
    write_audit(db, actor=user.username, action="annotation.submit", target=body.item_id, detail={"is_preview": body.is_preview})
    return {"annotation_id": ann.annotation_id, "quality_state": ann.quality_state}


@router.post("/tasks/{task_id}/agreement")
def compute_agreement_api(task_id: str, user=Depends(require("labeling.agreement")), db: Session = Depends(get_db)):
    try:
        run = compute_agreement(db, task_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)[:200]) from e
    write_audit(db, actor=user.username, action="agreement.compute", target=task_id, detail=run.kappas)
    return {"run_id": run.run_id, "kappas": run.kappas, "n_pairs": run.n_pairs}


@router.post("/adjudications")
def adjudicate_api(body: AdjudicateRequest, user=Depends(require("labeling.adjudicate")), db: Session = Depends(get_db)):
    try:
        dec = adjudicate(
            db,
            task_id=_task_of_item(db, body.item_id),
            item_id=body.item_id,
            adjudicator=user.username,
            labels={"direction": body.direction, "vs_expectation": body.vs_expectation, "certainty": body.certainty},
            rationale=body.rationale,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)[:200]) from e
    write_audit(db, actor=user.username, action="adjudication.create", target=body.item_id)
    return {"decision_id": dec.decision_id}


@router.get("/tasks/{task_id}/gold")
def gold_set_api(task_id: str, user=Depends(require("labeling.agreement")), db: Session = Depends(get_db)):
    gold = build_gold_set(db, task_id)
    return {"n": len(gold), "items": gold}


@router.get("/tasks/{task_id}/disagreements")
def disagreements_api(task_id: str, user=Depends(require("labeling.adjudicate")), db: Session = Depends(get_db)):
    """仲裁工作队列：两人标注存在分歧且未仲裁的 items。"""
    items = db.execute(select(TaskItem).where(TaskItem.task_id == task_id)).scalars().all()
    out = []
    for it in items:
        anns = [a for a in db.execute(select(SentimentAnnotation).where(SentimentAnnotation.item_id == it.item_id)).scalars().all() if not a.is_preview]
        dec = db.execute(select(AdjudicationDecision).where(AdjudicationDecision.item_id == it.item_id)).scalar_one_or_none()
        if len(anns) >= 2 and dec is None:
            a, b = sorted(anns, key=lambda x: x.annotator_pseudonym)[:2]
            if (a.direction, a.vs_expectation, a.certainty) != (b.direction, b.vs_expectation, b.certainty):
                out.append(
                    {
                        "item_id": it.item_id,
                        "market_text_id": it.market_text_id,
                        "ann_a": {"by": a.annotator_pseudonym, "direction": a.direction, "vs_expectation": a.vs_expectation, "certainty": a.certainty},
                        "ann_b": {"by": b.annotator_pseudonym, "direction": b.direction, "vs_expectation": b.vs_expectation, "certainty": b.certainty},
                    }
                )
    return {"items": out}


def _task_of_item(db: Session, item_id: str) -> str:
    item = db.get(TaskItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="任务项不存在")
    return item.task_id
