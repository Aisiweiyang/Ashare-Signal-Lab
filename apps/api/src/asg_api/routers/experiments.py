"""实验与回测路由：运行（researcher+）、列表、manifest 详情。"""

from __future__ import annotations

import json
from pathlib import Path

from asg_domain.db import BacktestRun, ExperimentRun, MetricResult
from asg_pipeline import ExperimentConfig, run_full_experiment
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from asg_api.deps import get_db, require, write_audit
from asg_api.settings import get_settings

router = APIRouter(prefix="/api/experiments", tags=["实验"])


class RunExperimentRequest(BaseModel):
    name: str = Field(default="exp_from_workbench", max_length=100)
    seed: int = 20260907
    n_events: int = Field(default=130, ge=30, le=500)
    n_days: int = Field(default=180, ge=60, le=400)
    provider: str = Field(default="mock", pattern="^(mock|llm)$")
    folds: int = Field(default=3, ge=2, le=6)
    slippage_bps: float = Field(default=10.0, ge=0, le=200)
    split_scheme: str = Field(default="walk_forward", pattern="^(walk_forward|purged_kfold)$")
    kfold_splits: int = Field(default=5, ge=2, le=10)
    event_type_ablation: bool = False


@router.get("")
def list_experiments(user=Depends(require("experiments.read")), db: Session = Depends(get_db)):
    runs = db.execute(select(ExperimentRun).order_by(ExperimentRun.started_at.desc()).limit(50)).scalars().all()
    bts = db.execute(select(BacktestRun).order_by(BacktestRun.started_at.desc()).limit(50)).scalars().all()
    return {
        "experiments": [
            {"run_id": r.run_id, "name": r.name, "state": r.state, "seed": r.seed, "config_hash": r.config_hash[:12], "started_at": r.started_at.isoformat()}
            for r in runs
        ],
        "backtests": [
            {"run_id": b.run_id, "signal_id": b.signal_id, "state": b.state, "seed": b.seed, "started_at": b.started_at.isoformat()}
            for b in bts
        ],
    }


@router.post("/run")
def run_experiment_api(body: RunExperimentRequest, user=Depends(require("experiments.run")), db: Session = Depends(get_db)):
    settings = get_settings()
    if settings.allow_real_trading:
        # 双保险：配置层也不允许（硬编码默认 False）
        raise HTTPException(status_code=403, detail="真实交易被系统策略禁止")
    cfg = ExperimentConfig(
        name=body.name,
        seed=body.seed,
        n_events=body.n_events,
        n_days=body.n_days,
        provider=body.provider,
        folds=body.folds,
        costs={"slippage_bps": body.slippage_bps},
        split_scheme=body.split_scheme,
        kfold_splits=body.kfold_splits,
        event_type_ablation=body.event_type_ablation,
    )
    try:
        manifest = run_full_experiment(db, cfg)
    except Exception as e:
        write_audit(db, actor=user.username, action="experiment.run", target=body.name, result="failed", detail={"error": str(e)[:200]})
        raise HTTPException(status_code=400, detail=f"实验失败: {str(e)[:200]}") from e
    write_audit(db, actor=user.username, action="experiment.run", target=manifest["run_id"])
    return manifest


@router.get("/{run_id}")
def experiment_detail(run_id: str, user=Depends(require("experiments.read")), db: Session = Depends(get_db)):
    exp = db.get(ExperimentRun, run_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="实验不存在")
    detail = {
        "run_id": exp.run_id,
        "name": exp.name,
        "state": exp.state,
        "config": exp.config,
        "seed": exp.seed,
        "dataset_hash": exp.dataset_hash,
        "code_commit": exp.code_commit,
        "metrics": exp.metrics,
        "started_at": exp.started_at.isoformat(),
        "finished_at": exp.finished_at.isoformat() if exp.finished_at else None,
        "manifest_uri": exp.manifest_uri,
    }
    if exp.manifest_uri and Path(exp.manifest_uri).exists():
        with open(exp.manifest_uri, encoding="utf-8") as f:
            detail["manifest"] = json.load(f)
    return detail


@router.get("/backtests/{run_id}/metrics")
def backtest_metrics(run_id: str, user=Depends(require("experiments.read")), db: Session = Depends(get_db)):
    bt = db.get(BacktestRun, run_id)
    if bt is None:
        raise HTTPException(status_code=404, detail="回测不存在")
    metrics = db.execute(select(MetricResult).where(MetricResult.backtest_run_id == run_id)).scalars().all()
    return {
        "run_id": run_id,
        "state": bt.state,
        "metrics": [{"name": m.name, "value": m.value} for m in metrics],
        "notes": bt.notes,
    }
