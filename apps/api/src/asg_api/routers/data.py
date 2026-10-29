"""数据路由：证券列表、行情、CSV 导入、演示播种、数据质量。"""

from __future__ import annotations

from asg_connectors.csv_docs import CsvConnector
from asg_connectors.market import generate_price_bars
from asg_connectors.synthetic import SyntheticConnector
from asg_domain.calendar import TradingCalendar
from asg_domain.db import (
    Issuer,
    PriceBar,
    Security,
    TradingCalendarDay,
)
from asg_domain.quality import (
    expect_available_at_covers_published,
    expect_price_continuity,
    run_all,
)
from asg_pipeline import ingest_documents, seed_demo, upsert_market_data
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from asg_api.deps import get_db, require, write_audit

router = APIRouter(prefix="/api/data", tags=["数据"])


@router.get("/securities")
def list_securities(user=Depends(require("texts.read")), db: Session = Depends(get_db)):
    rows = db.execute(select(Security, Issuer).join(Issuer, Security.issuer_id == Issuer.issuer_id, isouter=True)).all()
    return {
        "items": [
            {
                "security_id": s.security_id,
                "symbol": s.symbol,
                "exchange": s.exchange,
                "name": s.name,
                "industry": i.industry if i else "",
                "status": s.status,
            }
            for s, i in rows
        ]
    }


@router.get("/price-bars/{security_id}")
def price_bars(security_id: str, limit: int = 400, user=Depends(require("texts.read")), db: Session = Depends(get_db)):
    rows = db.execute(select(PriceBar).where(PriceBar.security_id == security_id).order_by(PriceBar.trade_date).limit(min(limit, 1000))).scalars().all()
    return {
        "items": [
            {
                "date": b.trade_date.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "suspended": b.suspended,
                "limit_up": b.limit_up,
                "limit_down": b.limit_down,
            }
            for b in rows
        ]
    }


@router.post("/import/csv")
async def import_csv(file: UploadFile, user=Depends(require("data.import")), db: Session = Depends(get_db)):
    content = await file.read()
    try:
        conn = CsvConnector(content, source_id=f"csv:{file.filename or 'upload'}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)[:200]) from e
    stats = ingest_documents(db, conn.fetch_batch())
    write_audit(db, actor=user.username, action="data.import_csv", target=str(file.filename), detail=stats)
    return {"stats": stats, "row_errors": conn.import_errors[:20]}


@router.post("/seed-demo")
def seed_demo_api(seed: int = 20260907, n_events: int = 130, user=Depends(require("data.import")), db: Session = Depends(get_db)):
    stats = seed_demo(db, seed=seed, n_events=n_events)
    write_audit(db, actor=user.username, action="data.seed_demo", target=f"seed={seed}", detail={"n_events": n_events})
    return stats


@router.post("/generate-bars")
def generate_bars_api(seed: int = 20260907, n_days: int = 180, user=Depends(require("data.import")), db: Session = Depends(get_db)):
    """为库内已有证券补行情（fixture 合成）。"""
    from asg_connectors.synthetic import GENERATOR_VERSION

    conn = SyntheticConnector(seed=seed)
    cal = TradingCalendar()
    bars = generate_price_bars(seed=seed, events=conn.events, n_days=n_days, calendar=cal)
    # ADR-014：行情写入路径同样落库级绑定（未绑定遗留库会被拒绝，绑定不一致视为改绑）
    stats = upsert_market_data(
        db,
        securities=[],
        bars=bars,
        cal=cal,
        bind_seed=seed,
        bind_generator_version=GENERATOR_VERSION,
    )
    write_audit(db, actor=user.username, action="data.generate_bars", detail=stats)
    return stats


@router.get("/binding")
def binding_status(user=Depends(require("texts.read")), db: Session = Depends(get_db)):
    """当前库的数据版本绑定状态（ADR-014；工作台展示用）。"""
    from asg_domain.db import DatabaseBinding

    row = db.execute(select(DatabaseBinding).order_by(DatabaseBinding.id)).scalars().first()
    if row is None:
        return {"bound": False}
    return {
        "bound": True,
        "seed": row.seed,
        "generator_version": row.generator_version,
        "code_commit": row.code_commit,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/quality")
def data_quality(user=Depends(require("texts.read")), db: Session = Depends(get_db)):
    """运行数据质量契约并返回断言结果（工作台数据质量页数据源）。"""
    from asg_domain.db import MarketText

    days = [d.day for d in db.execute(select(TradingCalendarDay)).scalars().all()]
    bars_by_sec: dict[str, list[dict]] = {}
    for b in db.execute(select(PriceBar)).scalars().all():
        bars_by_sec.setdefault(b.security_id, []).append({"trade_date": b.trade_date})
    texts = db.execute(select(MarketText)).scalars().all()
    text_rows = [
        {"id": t.market_text_id, "published_at": t.published_at, "available_at": t.available_at}
        for t in texts
    ]
    checks = []
    if days:
        checks.append(lambda rows: expect_price_continuity(bars_by_sec, days))
    checks.append(expect_available_at_covers_published)
    result = run_all(text_rows, checks)
    result["n_texts"] = len(texts)
    result["n_bars"] = sum(len(v) for v in bars_by_sec.values())
    return result
