"""文本浏览路由：分页、筛选（证券/时间/严格可用）。"""

from __future__ import annotations

from asg_domain.db import MarketText, Security, SourceDocument
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from asg_api.deps import get_db, require

router = APIRouter(prefix="/api/texts", tags=["文本"])


class TextOut(BaseModel):
    market_text_id: str
    title: str
    snippet: str
    security_symbol: str | None
    published_at: str | None
    available_at: str | None
    strict_ok: bool
    cluster_id: str | None
    quality_flags: dict


@router.get("", response_model=dict)
def list_texts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    security_symbol: str | None = None,
    strict_only: bool = False,
    user=Depends(require("texts.read")),
    db: Session = Depends(get_db),
):
    stmt = select(MarketText).order_by(MarketText.published_at.desc().nullslast())
    if strict_only:
        stmt = stmt.where(MarketText.strict_ok == True)  # noqa: E712
    if security_symbol:
        sec = db.execute(select(Security).where(Security.symbol == security_symbol)).scalar_one_or_none()
        if sec is None:
            raise HTTPException(status_code=404, detail="证券不存在")
        stmt = stmt.where(MarketText.security_id == sec.security_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    secs = {s.security_id: s for s in db.execute(select(Security)).scalars().all()}
    docs = {d.source_doc_id: d for d in db.execute(select(SourceDocument)).scalars().all()}
    items = [
        TextOut(
            market_text_id=t.market_text_id,
            title=t.title or (docs.get(t.source_doc_id).title if docs.get(t.source_doc_id) else ""),
            snippet=(t.cleaned_text[:120] + "…") if len(t.cleaned_text) > 120 else t.cleaned_text,
            security_symbol=secs[t.security_id].symbol if t.security_id in secs else None,
            published_at=t.published_at.isoformat() if t.published_at else None,
            available_at=t.available_at.isoformat() if t.available_at else None,
            strict_ok=t.strict_ok,
            cluster_id=t.cluster_id,
            quality_flags=t.quality_flags or {},
        )
        for t in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": [i.model_dump() for i in items]}


@router.get("/{market_text_id}")
def get_text(market_text_id: str, user=Depends(require("texts.read")), db: Session = Depends(get_db)):
    t = db.get(MarketText, market_text_id)
    if t is None:
        raise HTTPException(status_code=404, detail="文本不存在")
    return {
        "market_text_id": t.market_text_id,
        "title": t.title,
        "cleaned_text": t.cleaned_text,
        "security_id": t.security_id,
        "published_at": t.published_at.isoformat() if t.published_at else None,
        "available_at": t.available_at.isoformat() if t.available_at else None,
        "strict_ok": t.strict_ok,
        "cluster_id": t.cluster_id,
        "quality_flags": t.quality_flags or {},
    }
