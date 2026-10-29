"""MinHash+LSH 近重复聚类原型（exploratory，ADR-009 备选路线；阶段 14）。

与 SimHash（`dedup.py`）的对照依据：TEST_REPORT §15.1 调研（Shrivastva,
*In Defense of MinHash Over SimHash*, ICML 2014；ChenghaoMou/text-dedup 的
方法族参照）。两者在这里的差异：

- SimHash：字符 3-gram 投票成一个 64bit 指纹，海明距离 ≤ 6 判同——确定性，
  但「同模板仅数字不同」与「前缀改写转载」落在同一判定区间的两侧没有天然分界。
- MinHash：对 3-gram **集合**的 Jaccard 相似度做无偏估计（`jaccard_estimate`），
  显式阈值判同——「数字替换」与「前缀改写」落在 Jaccard 轴的不同位置，
  可以分开设门（对照量化见 `scripts/analyze_minhash_scale.py`）。

实现（全部自实现，不引第三方依赖；调研登记见 docs/OSS_EVALUATION.md）：

- 置换哈希：`h_i(x) = (a_i·x + b_i) mod (2^31-1)`，a/b 由固定 seed 的
  numpy RNG 生成——**固定 seed 跨进程确定**（与项目一等约束一致）。
  选 2^31-1（Mersenne 素数）使 a·x+b 在 uint64 内不回绕，逐位可复现。
- LSH 分带：`num_perm` 个置换值切成 `bands` 带 × `rows` 行，带键相同才互为
  候选；候选再做估计 Jaccard 精确门控。S-curve：阈值 s* = (1/bands)^(1/rows)，
  默认 128 置换 / 16 带 × 8 行 → s* ≈ 0.707，配 0.80 判同阈值时
  真相似对召回 ≈ 0.95（s=0.9 → ≈0.9999）。
- 贪心聚类语义与 `cluster_duplicates` 一致：按 `published_at` 升序处理，
  命中多簇时取**创建顺序最早**者，簇首发为簇内最早者。

**opt-in 原型**：默认 ingest 不经过本模块；这是与 SimHash 的研究对照，
不改变任何默认路径指标。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .dedup import ClusterResult, DedupItem, _char_ngrams, _hash64

_MERSENNE_31 = (1 << 31) - 1  # 2147483647：a·x+b < 2^62，uint64 内精确无回绕

DEFAULT_THRESHOLD = 0.80


@dataclass(frozen=True)
class MinHashConfig:
    num_perm: int = 128
    bands: int = 16
    rows: int = 8
    threshold: float = DEFAULT_THRESHOLD

    def __post_init__(self) -> None:
        if self.num_perm < 1 or self.bands < 1 or self.rows < 1:
            raise ValueError("num_perm/bands/rows 必须为正整数")
        if self.num_perm < self.bands * self.rows:
            raise ValueError("num_perm 必须 ≥ bands*rows（分带不重叠）")
        if not 0.0 < self.threshold <= 1.0:
            raise ValueError("threshold 必须在 (0, 1]")


def _gram_hashes(text: str) -> np.ndarray:
    grams = _char_ngrams(text)
    if not grams:
        return np.empty(0, dtype=np.uint64)
    return np.array([_hash64(g) for g in grams], dtype=np.uint64)


def _perm_params(seed: int, num_perm: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    a = rng.integers(1, _MERSENNE_31, size=num_perm, dtype=np.uint64)
    b = rng.integers(0, _MERSENNE_31, size=num_perm, dtype=np.uint64)
    return a, b


def minhash_signature(text: str, *, seed: int = 0, num_perm: int = 128) -> np.ndarray:
    """字符 3-gram 集合的 MinHash 签名（uint64 数组，固定 seed 跨进程确定）。"""
    a, b = _perm_params(seed, num_perm)
    gh = _gram_hashes(text)
    if gh.size == 0:
        # 空文本：所有置换取 0（彼此相似度为 1——与 SimHash 的空文本处理同语义）
        return np.zeros(num_perm, dtype=np.uint64)
    x = gh % _MERSENNE_31
    values = (x[:, None] * a[None, :] + b[None, :]) % _MERSENNE_31  # (n_grams, num_perm)
    return values.min(axis=0)


def jaccard_estimate(sig_a: np.ndarray, sig_b: np.ndarray) -> float:
    """签名一致位比例 = Jaccard 的无偏估计。"""
    if sig_a.shape != sig_b.shape:
        raise ValueError("签名长度不一致")
    if sig_a.size == 0:
        return 1.0
    return float(np.mean(sig_a == sig_b))


def exact_jaccard(text_a: str, text_b: str) -> float:
    """精确集合 Jaccard（字符 3-gram），供对照与测试。"""
    sa = set(_char_ngrams(text_a))
    sb = set(_char_ngrams(text_b))
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def _band_keys(sig: np.ndarray, cfg: MinHashConfig) -> list[tuple[int, ...]]:
    arr = sig[: cfg.bands * cfg.rows].reshape(cfg.bands, cfg.rows)
    return [tuple(int(v) for v in row) for row in arr]


def _canonical_key(item: DedupItem) -> tuple:
    # 与 cluster_duplicates / refine_clusters 一致的首发规则
    return (item.published_at is None, item.published_at or datetime.max, item.item_id)


def cluster_minhash(
    items: list[DedupItem],
    *,
    seed: int = 0,
    config: MinHashConfig | None = None,
) -> ClusterResult:
    """MinHash+LSH 贪心聚类（语义与 `cluster_duplicates` 一致，exploratory）。

    按 published_at 升序处理；候选 = 任一带键相同的既有簇；候选中估计
    Jaccard ≥ threshold 者按创建顺序取最早。返回 ClusterResult 同构结果。
    """
    cfg = config or MinHashConfig()
    res = ClusterResult()
    if not items:
        return res

    ordered = sorted(items, key=lambda it: (it.published_at is None, it.published_at or datetime.max))
    sigs = {it.item_id: minhash_signature(it.text, seed=seed, num_perm=cfg.num_perm) for it in ordered}
    buckets: list[dict[tuple[int, ...], list[str]]] = [{} for _ in range(cfg.bands)]
    creation: dict[str, int] = {}

    for it in ordered:
        sig = sigs[it.item_id]
        keys = _band_keys(sig, cfg)
        assigned: str | None = None
        best_order: int | None = None
        seen: set[str] = set()
        for band, key in enumerate(keys):
            for cid in buckets[band].get(key, ()):
                if cid in seen:
                    continue
                seen.add(cid)
                rep_sig = sigs[res.canonical_of[cid]]
                if jaccard_estimate(sig, rep_sig) >= cfg.threshold and (
                    best_order is None or creation[cid] < best_order
                ):
                    assigned = cid
                    best_order = creation[cid]
        if assigned is None:
            assigned = f"clu_{it.item_id}"
            creation[assigned] = len(creation)
            res.canonical_of[assigned] = it.item_id
        for band, key in enumerate(keys):
            buckets[band].setdefault(key, []).append(assigned)
        res.cluster_of[it.item_id] = assigned

    res.n_clusters = len(res.canonical_of)
    return res
