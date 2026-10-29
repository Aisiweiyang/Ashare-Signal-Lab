"""事件层聚类兜底原型（exploratory，ADR-015；文献依据见 TEST_REPORT §15）。

动机（ADR-009/012 已知限制）：SimHash（Hamming ≤ 6）在扩容下有两类残余错误——
误聚（同模板+同公司、仅数字不同的例行公告被并为一簇）与漏聚（前缀改写的转载
未与根事件同簇，v2 样板句后 700 事件仍有 10.4%）。TDT 经典管线（Allan & Papka,
UMass；single-pass/增量凝聚聚类,TF-IDF+余弦+时间窗）提示用「事件层」二级聚类
兜底。本模块实现两步互补的原型：

1. 拆分（治误聚）：同一 SimHash 簇内若文本的「数字签名」（全部数字 token 的
   排序多元组）不同 → 判为不同事件，按签名拆分；合成宇宙的误聚全部来自
   「同模板、仅数字不同」，真实场景对应同公司结构雷同的例行公告。用多元组
   而非逐位骨架的原因：转载前缀（「【转载】」等）无数字但会平移全部字符，
   逐位骨架会把转载与根拆开（首轮实测漏聚恶化到 100% 的教训，ADR-015）。
2. 合并（治漏聚）：不同簇的簇首若满足「同一证券 + 发布时间差 ≤ 窗口 + 数字
   签名相同 + 字符 n-gram TF-IDF 余弦 ≥ 阈值」→ 判为同一事件的改写转载，合并。
   合并要求签名相同，恰好保证第 1 步拆开的「不同数字事件」不会被并回去。

**opt-in 原型**：默认 ingest 不经过本模块（研究指标与 §13/§14 完全一致）；
量化评估见 scripts/analyze_event_layer.py 与 research/runs/event_layer_*.json。
接默认管线前需在真实授权语料上验证（ADR-015）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .dedup import ClusterResult, DedupItem

_DIGITS = re.compile(r"\d+(?:\.\d+)?")

# 与 asg_models.baselines 的 char 通道一致的字符 n-gram 配置（复用现有依赖 sklearn）
_TFIDF_KWARGS = {"analyzer": "char_wb", "ngram_range": (2, 4), "min_df": 1, "sublinear_tf": True}


@dataclass
class EventLayerConfig:
    max_merge_window_days: int = 3
    merge_cosine_threshold: float = 0.90
    split_by_digit_skeleton: bool = True


@dataclass
class _Group:
    """拆分后的候选事件组：成员 item_id 集合 + 组首（首发）。"""

    members: list[str] = field(default_factory=list)
    canonical: str = ""
    published_at: datetime | None = None
    skeleton: tuple[str, ...] = ()


def number_signature(text: str) -> tuple[str, ...]:
    """数字签名：全部数字 token（含小数）的排序多元组。

    对「前缀插入/文字改写」不变式（转载识别友好），对「同模板不同数字」
    可区分（误聚拆分依据）。空签名（无数字文本）彼此相同——它们不参与
    有信息量的拆分，合并侧另有余弦+时间窗+证券三重门控。
    """
    return tuple(sorted(_DIGITS.findall(text)))


def _canonical_key(item: DedupItem) -> tuple:
    # 与 cluster_duplicates 一致的首发规则：时间最早者（缺失视为最晚）；并列取 item_id
    return (item.published_at is None, item.published_at or datetime.max, item.item_id)


def refine_clusters(
    items: list[DedupItem],
    clustering: ClusterResult,
    *,
    security_of: dict[str, str] | None = None,
    config: EventLayerConfig | None = None,
) -> ClusterResult:
    """在 SimHash 聚类结果之上做事件层拆分/合并（exploratory）。

    items：与聚类输入相同的条目（text/published_at 必须一致）；
    security_of：item_id → security_id（可缺省；缺证券信息的条目不参与合并）。
    返回与 ClusterResult 同构的结果；确定性：全部分组/合并按内容键排序。
    """
    cfg = config or EventLayerConfig()
    by_id = {it.item_id: it for it in items}

    # ---- 第 1 步：同簇内按数字骨架拆分（治误聚） ----
    members_by_cluster: dict[str, list[str]] = {}
    for item_id, cid in clustering.cluster_of.items():
        members_by_cluster.setdefault(cid, []).append(item_id)

    groups: list[_Group] = []
    for members in members_by_cluster.values():
        by_skeleton: dict[tuple[str, ...], list[str]] = {}
        for mid in members:
            it = by_id[mid]
            sk = number_signature(it.text) if cfg.split_by_digit_skeleton else ()
            by_skeleton.setdefault(sk, []).append(mid)
        for sk, group_members in by_skeleton.items():
            first = min(group_members, key=lambda m: _canonical_key(by_id[m]))
            groups.append(
                _Group(
                    members=sorted(group_members),
                    canonical=first,
                    published_at=by_id[first].published_at,
                    skeleton=sk,
                )
            )

    # ---- 第 2 步：跨组合并（治漏聚；TDT 余弦+时间窗管线） ----
    parent = list(range(len(groups)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    if security_of is not None and cfg.max_merge_window_days > 0:
        candidates = [
            gi
            for gi, g in enumerate(groups)
            if security_of.get(g.canonical) and g.published_at is not None
        ]
        # 按同一 (security, 骨架) 分桶后做窗口内余弦（桶小，两两比较开销可忽略）
        buckets: dict[tuple[str, str], list[int]] = {}
        for gi in candidates:
            g = groups[gi]
            buckets.setdefault((security_of[g.canonical], g.skeleton), []).append(gi)
        window = cfg.max_merge_window_days
        for (_sec, _sk), gids in buckets.items():
            gids = sorted(gids, key=lambda gi: _canonical_key(by_id[groups[gi].canonical]))
            texts = [by_id[groups[gi].canonical].text for gi in gids]
            sims = _pairwise_cosine(texts)
            for i in range(len(gids)):
                for j in range(i + 1, len(gids)):
                    gi, gj = gids[i], gids[j]
                    dt_days = abs(
                        (groups[gi].published_at - groups[gj].published_at).total_seconds()
                    ) / 86400.0
                    if dt_days <= window and sims[i][j] >= cfg.merge_cosine_threshold:
                        ri, rj = find(gi), find(gj)
                        if ri != rj:
                            parent[max(ri, rj)] = min(ri, rj)

    # ---- 重建结果：组首 = 并集内最早者；cluster id = clu_<组首>（确定性） ----
    res = ClusterResult()
    unions: dict[int, list[int]] = {}
    for gi in range(len(groups)):
        unions.setdefault(find(gi), []).append(gi)
    for root_gids in unions.values():
        first = min(
            (groups[gi].canonical for gi in root_gids),
            key=lambda m: _canonical_key(by_id[m]),
        )
        cid = f"clu_{first}"
        res.canonical_of[cid] = first
        for gi in root_gids:
            for mid in groups[gi].members:
                res.cluster_of[mid] = cid
    res.n_clusters = len(res.canonical_of)
    return res


def _pairwise_cosine(texts: list[str]) -> list[list[float]]:
    """字符 n-gram TF-IDF 余弦矩阵（桶内规模小；sklearn 为既有依赖）。"""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import linear_kernel

    if len(texts) < 2:
        return [[1.0] * len(texts) for _ in texts]
    vec = TfidfVectorizer(**_TFIDF_KWARGS)
    m = vec.fit_transform(texts)
    return linear_kernel(m).tolist()
