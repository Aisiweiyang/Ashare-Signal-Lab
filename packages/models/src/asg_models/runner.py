"""预测 runner：批量推理编排（缓存、预算、隔离、落库、血缘）。"""

from __future__ import annotations

from datetime import datetime

from asg_domain.db import (
    ModelPrediction,
    ModelVersion,
    PredictionRun,
    PromptVersion,
)
from asg_domain.enums import RunState
from asg_domain.errors import BudgetExceededError, QuarantinedOutputError
from asg_domain.ids import canonical_env, config_hash, new_id
from sqlalchemy import select
from sqlalchemy.orm import Session

from asg_models.schema import SYSTEM_PROMPT, build_user_prompt, parse_model_output


class TextItem:
    """最小预测输入。"""

    __slots__ = ("market_text_id", "text", "title")

    def __init__(self, market_text_id: str, title: str, text: str) -> None:
        self.market_text_id = market_text_id
        self.title = title
        self.text = text


def run_prediction(
    session: Session,
    *,
    items: list[TextItem],
    chat_provider,  # ChatProvider
    model_kind: str,
    model_name: str,
    model_params: dict | None = None,
    seed: int = 0,
    input_dataset_hash: str = "",
    notes: str = "",
    use_cache: bool = True,
) -> PredictionRun:
    """执行一次预测批次并持久化。

    - 缓存键 = (provider标识, prompt版本hash, 文本hash)：重复文本直接复用（省预算）；
    - schema 校验失败 → schema_valid=False 落隔离区（quarantine_reason 记原因）；
    - BudgetExceededError → run 置 failed，已完成部分保留（可审计）；
    - 返回 PredictionRun（含 state/degraded 信息）。
    """
    import hashlib

    params = model_params or {}
    prompt = PromptVersion(
        name="fine_grained_v1",
        template=SYSTEM_PROMPT,
        schema_hash=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:64],
        notes="预注册提示词 v1（RESEARCH_PROTOCOL）",
    )
    session.add(prompt)

    model_version = ModelVersion(
        provider_kind=model_kind,
        model_name=model_name,
        model_params=params,
        model_card={
            "language": "zh",
            "scope": "文本情绪细粒度标注（研究用途）",
            "limitations": "mock=词典规则；llm=受知识截止与提示影响；均不得用于投资建议",
        },
    )
    session.add(model_version)
    session.flush()

    run = PredictionRun(
        run_id=new_id("prd"),
        model_version_id=model_version.model_version_id,
        prompt_id=prompt.prompt_id,
        input_dataset_hash=input_dataset_hash,
        config_hash=config_hash({"model": model_name, "kind": model_kind, "seed": seed, "params": params}),
        seed=seed,
        state=RunState.RUNNING.value,
        env=canonical_env(),
        notes=notes,
    )
    session.add(run)
    session.flush()

    cache: dict[str, dict] = {}
    if use_cache:
        prior_rows = session.execute(
            select(ModelPrediction).where(ModelPrediction.schema_valid == True)  # noqa: E712
        ).scalars().all()
        for row in prior_rows:
            # 缓存键独立存储在 labels JSON 内（见下），避免解析 raw_output
            cached_key = (row.labels or {}).get("_cache_key")
            if cached_key:
                cache[cached_key] = {
                    "labels": {k: v for k, v in (row.labels or {}).items() if not k.startswith("_")},
                    "latency_ms": row.latency_ms,
                }

    degraded_reasons: list[str] = []
    quarantined = 0
    total_cost = 0.0
    for item in items:
        cache_key = hashlib.sha256(f"{model_kind}|{model_name}|{item.text}".encode()).hexdigest()[:32]
        if use_cache and cache_key in cache:
            hit = cache[cache_key]
            session.add(
                ModelPrediction(
                    run_id=run.run_id,
                    market_text_id=item.market_text_id,
                    labels={**hit["labels"], "_cache_key": cache_key},
                    probabilities={},
                    confidence=0.0,
                    uncertainty=0.0,
                    raw_output="(cache)",
                    schema_valid=True,
                    latency_ms=0.0,
                )
            )
            continue
        user = build_user_prompt(item.text, item.title)
        started = datetime.now()
        raw = ""
        try:
            raw, cost = chat_provider.chat(system=SYSTEM_PROMPT, user=user)
            total_cost += cost
            latency = (datetime.now() - started).total_seconds() * 1000
            parsed = parse_model_output(raw)
            session.add(
                ModelPrediction(
                    run_id=run.run_id,
                    market_text_id=item.market_text_id,
                    labels={**parsed.to_labels(), "_cache_key": cache_key},
                    probabilities={},
                    confidence=0.0,
                    uncertainty=0.0,
                    raw_output=raw,
                    schema_valid=True,
                    latency_ms=latency,
                )
            )
        except QuarantinedOutputError as e:
            quarantined += 1
            session.add(
                ModelPrediction(
                    run_id=run.run_id,
                    market_text_id=item.market_text_id,
                    labels={},
                    raw_output=raw,
                    schema_valid=False,
                    quarantine_reason=str(e)[:200],
                )
            )
        except BudgetExceededError:
            degraded_reasons.append("budget_exceeded")
            run.state = RunState.FAILED.value
            run.finished_at = datetime.now()
            run.cost_cny = total_cost
            session.commit()
            return run

    run.state = RunState.DEGRADED.value if degraded_reasons else RunState.SUCCEEDED.value
    run.notes = (notes + " | " if notes else "") + ";".join(degraded_reasons)
    run.cost_cny = total_cost
    run.finished_at = datetime.now()
    session.commit()
    return run


def predictions_of_run(session: Session, run_id: str, *, include_quarantine: bool = False) -> list[ModelPrediction]:
    stmt = select(ModelPrediction).where(ModelPrediction.run_id == run_id)
    if not include_quarantine:
        stmt = stmt.where(ModelPrediction.schema_valid == True)  # noqa: E712
    return list(session.execute(stmt).scalars().all())


def prediction_metrics_summary(session: Session, run_id: str) -> dict:
    rows = predictions_of_run(session, run_id)
    quar = session.execute(
        select(ModelPrediction).where(ModelPrediction.run_id == run_id, ModelPrediction.schema_valid == False)  # noqa: E712
    ).scalars().all()
    from collections import Counter

    dist = Counter()
    for r in rows:
        dist[r.labels.get("direction", "?")] += 1
    return {
        "n_valid": len(rows),
        "n_quarantined": len(quar),
        "direction_distribution": dict(dist),
    }
