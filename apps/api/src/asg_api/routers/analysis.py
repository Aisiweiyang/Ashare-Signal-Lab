"""Bilingual analysis is an explicit v2 endpoint, separate from frozen studies."""

from __future__ import annotations

from typing import Annotated

from asg_domain.db import MarketText, Security
from asg_models.bilingual import analyze_bilingual
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from asg_api.deps import get_db, require

router = APIRouter(prefix="/api/analysis", tags=["双语情绪分析"])
Name = Annotated[str, Field(min_length=1, max_length=100)]


class AnalyzeTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=18000)
    title: str = Field(default="", max_length=1000)
    target: str = Field(default="", max_length=100)
    aliases: list[Name] = Field(default_factory=list, max_length=16)
    other_targets: list[Name] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_content(self):
        if not self.text.strip():
            raise ValueError("正文不能为空")
        if self.aliases and not self.target.strip():
            raise ValueError("使用别名时必须指定目标公司")
        return self


class BatchRequest(BaseModel):
    items: list[AnalyzeTextRequest] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_size(self):
        if sum(len(item.text)+len(item.title) for item in self.items) > 64000:
            raise ValueError("批量正文与标题合计不得超过64000字符")
        return self


def _analyze(item: AnalyzeTextRequest) -> dict:
    try:
        return analyze_bilingual(**item.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/text")
def analyze_text(body: AnalyzeTextRequest, user=Depends(require("texts.read"))):
    return _analyze(body)


@router.post("/batch")
def analyze_batch(body: BatchRequest, user=Depends(require("texts.read"))):
    return {"items": [_analyze(item) for item in body.items], "n": len(body.items)}


@router.get("/market-text/{market_text_id}")
def analyze_stored_text(market_text_id: str, user=Depends(require("texts.read")), db: Session = Depends(get_db)):
    row = db.get(MarketText, market_text_id)
    if row is None:
        raise HTTPException(status_code=404, detail="文本不存在")
    sec = db.get(Security, row.security_id) if row.security_id else None
    try:
        result = analyze_bilingual(row.cleaned_text, title=row.title, target=sec.name if sec else "", aliases=[sec.symbol] if sec else [])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {**result, "market_text_id": market_text_id, "security_id": row.security_id, "input_kind": "stored_cleaned_text"}
