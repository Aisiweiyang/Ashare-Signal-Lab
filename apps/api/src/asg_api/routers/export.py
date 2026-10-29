"""导出路由：金标 JSONL（匿名化）、实体引用 contract。"""

from __future__ import annotations

from asg_domain.contracts import export_entities
from asg_domain.db import MarketEvent, MarketText, Security, SourceDocument
from asg_labeling.service import build_gold_set
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from asg_api.deps import get_db, require, write_audit

router = APIRouter(prefix="/api/export", tags=["导出"])


@router.get("/gold/{task_id}", response_class=PlainTextResponse)
def export_gold(task_id: str, user=Depends(require("export.data")), db: Session = Depends(get_db)):
    """金标 JSONL：仅匿名化字段（RESEARCH_PROTOCOL §3 / LABELING_GUIDE §5）。"""
    gold = build_gold_set(db, task_id)
    lines = []
    for g in gold:
        lines.append(
            json_dumps(
                {
                    "market_text_id": g["market_text_id"],
                    "item_id": g["item_id"],
                    "labels": {k: g[k] for k in ("direction", "vs_expectation", "certainty")},
                    "target_type": g["target_type"],
                    "event_type": g["event_type"],
                    "source": g["source"],
                    "annotator_pseudonyms": g["annotator_pseudonyms"],
                    "guide_version": g["guide_version"],
                }
            )
        )
    write_audit(db, actor=user.username, action="export.gold", target=task_id, detail={"n": len(gold)})
    return "\n".join(lines)


@router.get("/entities")
def export_entities_api(user=Depends(require("export.data")), db: Session = Depends(get_db)):
    """跨项目 contract：entity_id/security_id/event_id/source_id 引用集合。"""
    rows = (
        db.execute(select(MarketEvent, MarketText, Security, SourceDocument).join(MarketText, MarketEvent.market_text_id == MarketText.market_text_id).join(Security, MarketText.security_id == Security.security_id).join(SourceDocument, MarketText.source_doc_id == SourceDocument.source_doc_id).limit(5000))
        .all()
    )
    payload = export_entities(
        [
            {
                "entity_id": s.issuer_id or f"ent_unknown_{s.security_id}",
                "security_id": s.security_id,
                "event_id": ev.event_id,
                "source_id": d.source_id,
            }
            for ev, t, s, d in rows
        ]
    )
    write_audit(db, actor=user.username, action="export.entities", detail={"n": len(payload["entities"])})
    return payload


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
