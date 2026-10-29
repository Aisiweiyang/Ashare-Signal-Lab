"""演示数据播种：合成宇宙 → 数据库（证券/行情/文本/事件/标注任务/金标）。

模拟标注者说明（DATA_CARD）：金标来自「潜在真值 + 可控错误率的双人独立标注 +
oracle 仲裁」，这是合成演示的捷径；真实路径应经工作台人工标注（API/前端）。
"""

from __future__ import annotations

from asg_connectors.synthetic import SyntheticConnector
from asg_domain.calendar import TradingCalendar
from asg_domain.db import MarketText, TaskItem
from asg_labeling.service import adjudicate, compute_agreement, create_task, submit_annotation
from sqlalchemy import select
from sqlalchemy.orm import Session

from asg_pipeline.ingest import ingest_documents, upsert_market_data


def seed_demo(
    session: Session,
    *,
    seed: int = 20260907,
    n_events: int = 130,
    n_days: int = 180,
    annotator_error_rate: float = 0.08,
    task_name: str = "演示标注任务 v1",
    event_layer_fallback: bool = False,
    allow_unbound: bool = False,
) -> dict:
    """播种演示数据。

    - ADR-014：首次写入（第一次 upsert_market_data）即落库级绑定
      （seed + generator_version + code_commit）；已绑定库的种子/版本不一致
      会抛 BindingError（跨 seed 请用全新库）。allow_unbound=True 仅用于对
      未绑定遗留库的显式豁免采用（写审计留痕）。
    - event_layer_fallback（ADR-015，v4 预注册开启）：摄取聚类接事件层兜底，
      并在返回值中附「兜底前后误聚/漏聚对照」（event_layer_quality）。
    """
    cal = TradingCalendar()
    conn = SyntheticConnector(seed=seed, n_events=n_events, n_days=n_days)

    md_stats = upsert_market_data(
        session,
        securities=conn.securities,
        bars={},
        cal=cal,
        bind_seed=seed,
        bind_generator_version=conn.generator_version,
        bind_allow_adopt=allow_unbound,
    )
    from asg_connectors.market import generate_price_bars

    bars = generate_price_bars(seed=seed, events=conn.events, n_days=n_days, calendar=cal)
    md_stats = upsert_market_data(session, securities=[], bars=bars, cal=cal)

    ingest_stats = ingest_documents(session, conn.fetch_batch(), cal=cal, event_layer_fallback=event_layer_fallback)

    # 潜在标签 → 模拟双人标注（仅簇首发文本参与；_collect_canonical 完成收集）
    seq_by_external = {f"syn-{e.event_seq}": e for e in conn.events}
    canonical_texts, latent_rows = _collect_canonical(session, conn, seq_by_external)

    if not canonical_texts:
        raise RuntimeError("演示数据中无簇首发文本，检查去重聚类")

    task = create_task(
        session,
        name=task_name,
        market_text_ids=[t.market_text_id for t in canonical_texts],
        created_by="system_seed",
        sampling_config={"strategy": "all_canonical", "seed": seed},
    )
    items = session.execute(select(TaskItem).where(TaskItem.task_id == task.task_id)).scalars().all()
    item_by_text = {i.market_text_id: i.item_id for i in items}

    # 模拟标注（批量落库，阶段 11）：commit=False 消除逐条 fsync，
    # 全部标注/仲裁共享一次提交；rng 消费顺序与逐条提交版本一致，
    # 落库行内容逐位一致（等价性测试锁定）。
    latent_by_text: dict[str, dict] = {}
    for t, row in zip(canonical_texts, latent_rows, strict=True):
        latent_by_text[t.market_text_id] = row
    import numpy as np

    rng = np.random.default_rng(seed + 999)
    for annotator in ("annotator_A", "annotator_B"):
        for t in canonical_texts:
            row = latent_by_text[t.market_text_id]
            flipped = _maybe_flip(row, rng, annotator_error_rate)
            submit_annotation(
                session,
                task_id=task.task_id,
                item_id=item_by_text[t.market_text_id],
                annotator=annotator,
                labels=flipped,
                rationale="模拟标注（演示）",
                commit=False,
                # 批量播种（阶段 17）：全新任务必无既有标注，跳过逐条查重
                # SELECT（autoflush 逐条 flush 为播种链路 40% 耗时，画像实证）
                check_existing=False,
            )
    session.flush()

    agreement = compute_agreement(session, task.task_id)

    # oracle 仲裁（取潜在真值）；质量状态一次预取，替代逐条 SELECT
    state_by_text = _task_quality_states(session, task.task_id)
    # 标注行一次预取（阶段 17）：adjudicate 复用，消除循环内逐条 SELECT 的 autoflush
    from asg_domain.db import SentimentAnnotation

    ann_by_item: dict[str, list] = {}
    for a in session.execute(
        select(SentimentAnnotation).where(
            SentimentAnnotation.task_id == task.task_id,
            SentimentAnnotation.is_preview == False,  # noqa: E712
        )
    ).scalars().all():
        ann_by_item.setdefault(a.item_id, []).append(a)
    n_adjudicated = 0
    for t in canonical_texts:
        row = latent_by_text[t.market_text_id]
        if state_by_text.get(t.market_text_id, "missing") == "pending":
            adjudicate(
                session,
                task_id=task.task_id,
                item_id=item_by_text[t.market_text_id],
                adjudicator="oracle_adjudicator",
                labels={
                    "direction": row["direction"],
                    "vs_expectation": row["vs_expectation"],
                    "certainty": row["certainty"],
                    "event_type": row["event_type"],
                },
                rationale="合成仲裁：采用潜在真值（演示捷径）",
                commit=False,
                annotations=ann_by_item.get(item_by_text[t.market_text_id], []),
            )
            n_adjudicated += 1

    from asg_labeling.service import build_gold_set

    session.commit()  # 仲裁批次一次性提交
    gold = build_gold_set(session, task.task_id)

    out = {
        "market_data": md_stats,
        "ingest": ingest_stats,
        "task_id": task.task_id,
        "kappa": agreement.kappas,
        "n_gold": len(gold),
        "n_adjudicated": n_adjudicated,
        "n_canonical": len(canonical_texts),
        "generator_version": getattr(conn, "generator_version", "unknown"),
    }
    if event_layer_fallback:
        # 同一次运行内记录兜底前后的误聚/漏聚（§13.1 真值口径）：量化「接入即改善」
        from asg_pipeline.event_quality import fallback_quality_report

        out["event_layer_quality"] = fallback_quality_report(session, conn)
    return out


def _collect_canonical(session: Session, conn: SyntheticConnector, seq_by_external: dict) -> tuple[list, list]:
    """簇首发文本 + 对应潜在标签行。"""
    from asg_domain.db import TextCluster
    from sqlalchemy import select

    clusters = session.execute(select(TextCluster)).scalars().all()
    canonical_ids = {c.canonical_doc_id for c in clusters if c.canonical_doc_id}
    texts = session.execute(select(MarketText)).scalars().all()
    by_id = {t.market_text_id: t for t in texts}
    from asg_domain.db import SourceDocument

    src_docs = {s.source_doc_id: s for s in session.execute(select(SourceDocument)).scalars().all()}
    out_texts: list = []
    out_rows: list[dict] = []
    for cid in canonical_ids:
        t = by_id.get(cid)
        if t is None:
            continue
        sd = src_docs.get(t.source_doc_id)
        if sd is None:
            continue
        ext = (sd.meta or {}).get("external_id", "")
        ev = seq_by_external.get(ext)
        if ev is None or ev.is_repost:
            continue
        if ev.latent.direction.value == "unknown":
            continue  # 噪声文本不进金标
        out_texts.append(t)
        out_rows.append(
            {
                "direction": ev.latent.direction.value,
                "vs_expectation": ev.latent.vs_expectation.value,
                "certainty": ev.latent.certainty.value,
                "event_type": ev.latent.event_type.value,
            }
        )
    # 确定性排序（ADR-013）：canonical_ids 是 set，其迭代顺序受进程哈希随机化影响；
    # 模拟标注按此顺序消费共享 rng，顺序不定会导致同 seed 跨进程金标内容漂移。
    # external_id（syn-<seq>，由生成器决定）是内容稳定键。
    def _ext_id(t) -> str:
        sd = src_docs.get(t.source_doc_id)
        return (sd.meta or {}).get("external_id", "") if sd else ""

    pairs = sorted(zip(out_texts, out_rows, strict=True), key=lambda pair: _ext_id(pair[0]))
    out_texts = [t for t, _ in pairs]
    out_rows = [r for _, r in pairs]
    return out_texts, out_rows


def _maybe_flip(row: dict, rng, error_rate: float) -> dict:
    dir_cycle = {"bullish": "neutral", "neutral": "bearish", "bearish": "bullish"}
    vs_cycle = {"above": "inline", "inline": "below", "below": "no_expectation", "no_expectation": "above"}
    cert_cycle = {"certain": "speculative", "speculative": "conditional", "conditional": "certain"}
    out = dict(row)
    if rng.random() < error_rate and out["direction"] in dir_cycle:
        out["direction"] = dir_cycle[out["direction"]]
    if rng.random() < error_rate and out["vs_expectation"] in vs_cycle:
        out["vs_expectation"] = vs_cycle[out["vs_expectation"]]
    if rng.random() < error_rate and out["certainty"] in cert_cycle:
        out["certainty"] = cert_cycle[out["certainty"]]
    return out


def _task_quality_states(session: Session, task_id: str) -> dict[str, str]:
    """一次查询取任务全部非预览标注的 (market_text_id → quality_state)。

    替代逐条 SELECT 的 _item_quality_state；同一条目两条标注的
    quality_state 由 compute_agreement 统一设置，取首条与逐条查询等价。
    """
    from asg_domain.db import SentimentAnnotation
    from sqlalchemy import select

    rows = session.execute(
        select(SentimentAnnotation.market_text_id, SentimentAnnotation.quality_state).where(
            SentimentAnnotation.task_id == task_id,
            SentimentAnnotation.is_preview == False,  # noqa: E712
        )
    ).all()
    out: dict[str, str] = {}
    for market_text_id, state in rows:
        out.setdefault(market_text_id, state)
    return out
