"""转载/重复检测：SimHash（64bit，字符 3-gram）+ 海明距离聚类（ADR-009）。

阶段 12（文献驱动优化，见 TEST_REPORT §15）：simhash 位投票 numpy 向量化
（gram 间独立、FNV 字节序依赖按字节位迭代保留，整数运算逐位等价）；
cluster_duplicates 按位分桶多索引（Manku et al. WWW 2007 的 k+1 分区鸽笼通解）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

_CHINESE_TOKEN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+")

SIMHASH_BITS = 64
DEFAULT_MAX_HAMMING = 6

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3


def _char_ngrams(text: str, n: int = 3) -> list[str]:
    """中文字符与英文/数字 token 的 n-gram 序列。"""
    toks = _CHINESE_TOKEN.findall(text)
    if len(toks) < n:
        return ["".join(toks)] if toks else []
    return ["".join(toks[i : i + n]) for i in range(len(toks) - n + 1)]


def _hash64(s: str) -> int:
    # FNV-1a 64bit：确定性、无依赖、跨平台一致（不用 Python 内置 hash，其有随机化）。
    # 参考实现/单 gram 场景保留；批量路径走 _simhash_from_gram_bytes 的向量化版本。
    h = _FNV_OFFSET
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


def _simhash_from_gram_bytes(grams: list[bytes]) -> int:
    """对已 UTF-8 编码的 gram 序列做 SimHash 位投票。

    与逐 gram 调用 _hash64 的纯 Python 版本逐位等价（整数运算，无浮点）：
    FNV-1a 在 gram 内按字节顺序依赖、gram 间相互独立——按「字节位」迭代
    numpy 数组即可保留顺序语义；uint64 乘法回绕与 Python 的 & 掩码一致。
    """
    ng = len(grams)
    raw = b"".join(grams)
    arr = np.frombuffer(raw, dtype=np.uint8)
    lens = np.fromiter((len(g) for g in grams), dtype=np.int64, count=ng)
    starts = np.zeros(ng, dtype=np.int64)
    if ng > 1:
        starts[1:] = np.cumsum(lens)[:-1]
    h = np.full(ng, _FNV_OFFSET, dtype=np.uint64)
    prime = np.uint64(_FNV_PRIME)
    for p in range(int(lens.max())):
        active = lens > p
        if not active.any():
            break
        byte_at_p = arr[starts[active] + p].astype(np.uint64)
        h[active] = (h[active] ^ byte_at_p) * prime
    # 位投票：某位为 1 的 gram 数过半 → 置位；恰好半数不置位（与旧实现 x>0 一致）
    ones = (((h[:, None] >> np.arange(SIMHASH_BITS, dtype=np.uint64)) & np.uint64(1)) != 0).sum(axis=0)
    set_bits = ones * 2 > ng
    powers = np.left_shift(np.uint64(1), np.arange(SIMHASH_BITS, dtype=np.uint64))
    return int(np.where(set_bits, powers, np.uint64(0)).sum())


def simhash(text: str) -> int:
    grams = _char_ngrams(text)
    if not grams:
        return 0
    return _simhash_from_gram_bytes([g.encode("utf-8") for g in grams])


def simhash_batch(texts: list[str]) -> list[int]:
    """批量 SimHash（阶段 15）：与逐条 simhash() 逐位一致（同一 FNV 位投票语义）。

    全部文本的 gram 拼成一个数组后一次性向量化 FNV 与位投票，摊薄逐文本的
    numpy 调用开销——大规模摄取（cluster_duplicates 路径）的主要收益点。
    空文本（无 token）指纹为 0，与逐条实现一致。
    """
    if not texts:
        return []
    all_grams: list[bytes] = []
    gram_counts: list[int] = []
    for t in texts:
        grams = [g.encode("utf-8") for g in _char_ngrams(t)]
        gram_counts.append(len(grams))
        all_grams.extend(grams)
    n_total = len(all_grams)
    if n_total == 0:
        return [0] * len(texts)

    arr = np.frombuffer(b"".join(all_grams), dtype=np.uint8)
    lens = np.fromiter((len(g) for g in all_grams), dtype=np.int64, count=n_total)
    starts = np.zeros(n_total, dtype=np.int64)
    if n_total > 1:
        np.cumsum(lens[:-1], out=starts[1:])
    h = np.full(n_total, _FNV_OFFSET, dtype=np.uint64)
    prime = np.uint64(_FNV_PRIME)
    for p in range(int(lens.max())):
        active = lens > p
        if not active.any():
            break
        byte_at_p = arr[starts[active] + p].astype(np.uint64)
        h[active] = (h[active] ^ byte_at_p) * prime

    # 位投票：64 个位各做一次 (h>>p)&1，再按文本边界 reduceat 求和。
    # 逐位迭代（而非 (n,64) 大矩阵）控制内存；int32 求和与逐文本小的 gram 数无溢出。
    counts = np.asarray(gram_counts, dtype=np.int64)
    nonempty = counts > 0
    ones_matrix = np.zeros((len(texts), SIMHASH_BITS), dtype=np.int64)
    if nonempty.any():
        # 文本维掩码 → gram 维过滤（非空文本的全部 gram 参与位投票）
        seg_of_gram = np.repeat(np.arange(len(texts), dtype=np.int64), counts)
        keep = nonempty[seg_of_gram]
        hh = h[keep]
        seg_starts = np.zeros(int(nonempty.sum()), dtype=np.int64)
        np.cumsum(counts[nonempty][:-1], out=seg_starts[1:])
        for p in range(SIMHASH_BITS):
            bits = ((hh >> np.uint64(p)) & np.uint64(1)).astype(np.int64)
            ones_matrix[np.flatnonzero(nonempty), p] = np.add.reduceat(bits, seg_starts)
    powers = np.left_shift(np.uint64(1), np.arange(SIMHASH_BITS, dtype=np.uint64))
    set_bits = ones_matrix * 2 > counts[:, None]
    return [int(v) for v in np.where(set_bits, powers, np.uint64(0)).sum(axis=1)]


def hamming(a: int, b: int) -> int:
    # int.bit_count（C 实现）与 bin().count("1") 结果相同；大规模聚类探测
    # （数百万次调用）下差距显著（阶段 16 画像实证）
    return (a ^ b).bit_count()


@dataclass
class DedupItem:
    item_id: str
    text: str
    published_at: datetime | None
    payload: dict | None = None  # 调用方附加信息（如 source_doc_id）


@dataclass
class ClusterResult:
    cluster_of: dict[str, str] = field(default_factory=dict)  # item_id -> cluster_id
    canonical_of: dict[str, str] = field(default_factory=dict)  # cluster_id -> 首发item_id
    n_clusters: int = 0


def cluster_duplicates(
    items: list[DedupItem],
    *,
    max_hamming: int = DEFAULT_MAX_HAMMING,
) -> ClusterResult:
    """贪心单遍聚类：按 published_at 升序处理，与已有簇代表距离 ≤ max_hamming 归簇。

    首发判定：簇内 published_at 最早者（缺失时间者视为最晚，保守不当首发）。

    复杂度（阶段 11，ADR-013）：簇代表按位多索引（鸽笼原理：64bit 分成
    max_hamming+1 个分区时，Hamming ≤ max_hamming 的两个哈希至少在一个分区
    上逐位相同），新文本只与命中桶内的代表做精确海明比较——典型 O(n)，
    最坏仍 O(n·k)（大量代表挤在同一桶），但等价于全扫描的聚类结果
    （对照测试 tests/unit/test_dedup_equivalence.py 锁定）。
    """
    res = ClusterResult()
    if not items:
        return res

    ordered = sorted(items, key=lambda it: (it.published_at is None, it.published_at or datetime.max))
    # 批量文本哈希（阶段 15）：与逐条 simhash() 逐位一致，摊薄逐文本开销
    hashes = simhash_batch([it.text for it in ordered])
    n_parts = max_hamming + 1
    if n_parts > SIMHASH_BITS:
        # 海明阈值 ≥ 位宽：任何两个哈希都可能匹配，退化为顺序扫描语义
        n_parts = SIMHASH_BITS
    widths = [SIMHASH_BITS // n_parts + (1 if i < SIMHASH_BITS % n_parts else 0) for i in range(n_parts)]
    shifts = []
    acc = 0
    for w in widths:
        shifts.append(acc)
        acc += w

    rep: dict[str, int] = {}  # cluster_id -> 代表 simhash（首发文本）
    creation: dict[str, int] = {}  # cluster_id -> 创建顺序（= 簇首发的处理顺序）
    tables: list[dict[int, list[str]]] = [{} for _ in range(n_parts)]

    for it, h in zip(ordered, hashes, strict=True):
        assigned: str | None = None
        best_order: int | None = None
        if max_hamming >= SIMHASH_BITS:
            if rep:
                assigned = next(iter(creation))
                best_order = creation[assigned]
        else:
            seen: set[str] = set()
            for part, shift, width in zip(tables, shifts, widths, strict=True):
                key = (h >> shift) & ((1 << width) - 1)
                for cid in part.get(key, ()):
                    if cid in seen:
                        continue
                    seen.add(cid)
                    # 内联 popcount（阶段 16）：热点路径数百万次比较，省函数调用开销
                    if (h ^ rep[cid]).bit_count() <= max_hamming and (
                        best_order is None or creation[cid] < best_order
                    ):
                        assigned = cid
                        best_order = creation[cid]
        if assigned is None:
            assigned = f"clu_{it.item_id}"
            rep[assigned] = h
            creation[assigned] = len(creation)
            res.canonical_of[assigned] = it.item_id
            for part, shift, width in zip(tables, shifts, widths, strict=True):
                key = (h >> shift) & ((1 << width) - 1)
                part.setdefault(key, []).append(assigned)
        res.cluster_of[it.item_id] = assigned

    res.n_clusters = len(res.canonical_of)
    return res
