"""事件层兜底质量对照（ADR-015；真值口径与 analyze_simhash_scale.py 一致）。

合成宇宙自带根事件真值（转载链 repost_of_seq 可回溯），据此对任意聚类结果
量化两类残余错误：
- 误聚实例 = Σ簇(不同根事件数 − 1)；
- 漏聚 = 转载文本与其根事件首发文本不同簇。

供两处使用：
1. `fallback_quality_report`——v4 预注册运行内（seed_demo 开启兜底时）在**同一次
   运行**里对同一批文本重算 SimHash 基线与 SimHash+兜底两档指标，量化「接入即改善」；
2. `scripts/analyze_event_layer.py`——独立三档量化报告（基线/仅拆分/拆分+合并）。

全部为合成宇宙数字；根事件判定只依赖生成器真值，不依赖聚类结果。
"""

from __future__ import annotations

from asg_connectors.synthetic import SyntheticConnector
from asg_text_pipeline.dedup import DedupItem, cluster_duplicates
from asg_text_pipeline.event_layer import EventLayerConfig, refine_clusters
from sqlalchemy import select
from sqlalchemy.orm import Session


def root_seq(seq: int, by_seq: dict) -> int:
    """沿转载链回溯根事件序号（防环）。"""
    seen: set[int] = set()
    ev = by_seq[seq]
    while ev.is_repost and ev.repost_of_seq is not None and seq not in seen:
        seen.add(seq)
        seq = ev.repost_of_seq
        ev = by_seq[seq]
    return seq


def cluster_truth_metrics(
    cluster_of: dict[str, str],
    root_of_text: dict[str, int],
    texts: list,
    repost_flags: dict[str, bool],
) -> dict:
    """给定 cluster_of（item_id → 簇 id）与真值映射，计算误聚/漏聚（§13.1 口径）。"""
    roots_by_cluster: dict[str, set[int]] = {}
    for t in texts:
        cid = cluster_of.get(t.market_text_id, "")
        if cid:
            roots_by_cluster.setdefault(cid, set()).add(root_of_text[t.market_text_id])
    n_false_clusters = sum(1 for roots in roots_by_cluster.values() if len(roots) > 1)
    n_false_instances = sum(len(roots) - 1 for roots in roots_by_cluster.values() if len(roots) > 1)
    root_text_cluster: dict[int, str] = {}
    for t in texts:
        if not repost_flags[t.market_text_id]:
            root_text_cluster.setdefault(root_of_text[t.market_text_id], cluster_of.get(t.market_text_id, ""))
    n_missed = sum(
        1
        for t in texts
        if repost_flags[t.market_text_id]
        and cluster_of.get(t.market_text_id, "") != root_text_cluster.get(root_of_text[t.market_text_id], "")
    )
    n_repost = sum(1 for t in texts if repost_flags[t.market_text_id])
    return {
        "n_clusters": len(roots_by_cluster),
        "false_merge_clusters": n_false_clusters,
        "false_merge_instances": n_false_instances,
        "missed_reposts": n_missed,
        "n_repost_true": n_repost,
        "missed_repost_rate": round(n_missed / max(n_repost, 1), 4),
    }


def _truth_maps_from_db(session: Session, conn: SyntheticConnector):
    """从库内文本 + 生成器真值构建 (texts, items, root_of_text, repost_flags, security_of)。"""
    from asg_domain.db import MarketText, SourceDocument

    by_seq = {e.event_seq: e for e in conn.events}
    texts = session.execute(select(MarketText)).scalars().all()
    src_docs = {s.source_doc_id: s for s in session.execute(select(SourceDocument)).scalars().all()}
    root_of_text: dict[str, int] = {}
    repost_flags: dict[str, bool] = {}
    items: list[DedupItem] = []
    security_of: dict[str, str] = {}
    for t in texts:
        ext = (src_docs[t.source_doc_id].meta or {}).get("external_id", "")
        seq = int(ext.removeprefix("syn-"))
        root_of_text[t.market_text_id] = root_seq(seq, by_seq)
        repost_flags[t.market_text_id] = bool(by_seq[seq].is_repost)
        items.append(DedupItem(item_id=t.market_text_id, text=t.cleaned_text, published_at=t.published_at))
        security_of[t.market_text_id] = t.security_id or ""
    return texts, items, root_of_text, repost_flags, security_of


def fallback_quality_report(session: Session, conn: SyntheticConnector) -> dict:
    """同一次运行内的兜底前后对照：SimHash 基线 vs SimHash+事件层兜底。

    对当前库内的同一批文本重算两档聚类（refine 确定性，与 ingest 时落库的
    簇结构一致），真值来自生成器——即 manifest 里「接入即改善」的证据。
    """
    texts, items, root_of_text, repost_flags, security_of = _truth_maps_from_db(session, conn)
    base = cluster_duplicates(items)
    full = refine_clusters(items, base, security_of=security_of, config=EventLayerConfig())
    return {
        "n_texts": len(texts),
        "baseline_simhash": cluster_truth_metrics(base.cluster_of, root_of_text, texts, repost_flags),
        "with_fallback": cluster_truth_metrics(full.cluster_of, root_of_text, texts, repost_flags),
        "config": {
            "max_merge_window_days": EventLayerConfig().max_merge_window_days,
            "merge_cosine_threshold": EventLayerConfig().merge_cosine_threshold,
            "split_by_digit_skeleton": True,
        },
    }
