"""SimHash 聚类优化的等价性测试（阶段 11 任务 2 / 阶段 12 simhash 向量化）。

cluster_duplicates 新实现用「按位分桶多索引哈希」（鸽笼原理：64bit 分成
max_hamming+1 个分区，Hamming ≤ max_hamming 的两哈希至少在一个分区上完全
一致）消除 O(n·k) 全簇扫描；simhash 新实现把 FNV 位投票 numpy 向量化。
本文件把优化前实现作为参照（_ref_cluster_duplicates / _ref_simhash），在
随机与构造样本上断言新实现给出**完全一致**（bit-for-bit）的结果（同
cluster_of 划分、同 canonical_of 首发、同簇数、同 64bit 指纹）。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from asg_text_pipeline.dedup import (
    DEFAULT_MAX_HAMMING,
    SIMHASH_BITS,
    DedupItem,
    cluster_duplicates,
    hamming,
    simhash,
)


def _ref_simhash(text: str) -> int:
    """优化前的 simhash（逐字符拷贝自 dedup.py 当时版本，只读锚点）。"""
    v = [0] * SIMHASH_BITS
    grams = _ref_char_ngrams(text)
    if not grams:
        return 0
    for g in grams:
        h = _ref_hash64(g)
        for i in range(SIMHASH_BITS):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i, x in enumerate(v):
        if x > 0:
            out |= 1 << i
    return out


def _ref_hash64(s: str) -> int:
    h = 0xCBF29CE484222325
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def _ref_char_ngrams(text: str, n: int = 3) -> list[str]:
    import re as _re

    token = _re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+")
    toks = token.findall(text)
    if len(toks) < n:
        return ["".join(toks)] if toks else []
    return ["".join(toks[i : i + n]) for i in range(len(toks) - n + 1)]


def _ref_cluster_duplicates(items: list[DedupItem], *, max_hamming: int = DEFAULT_MAX_HAMMING):
    """优化前的实现（逐字符拷贝自 dedup.py 当时版本，只读锚点）。

    贪心单遍：按 published_at 升序处理，与已有簇代表距离 ≤ max_hamming 归簇
    （取创建顺序最早的匹配簇）；首发 = 簇内 published_at 最早者。
    """
    from asg_text_pipeline.dedup import ClusterResult

    res = ClusterResult()
    if not items:
        return res

    ordered = sorted(items, key=lambda it: (it.published_at is None, it.published_at or datetime.max))
    rep: dict[str, int] = {}
    for it in ordered:
        h = simhash(it.text)
        assigned: str | None = None
        for cid, rep_h in rep.items():
            if hamming(h, rep_h) <= max_hamming:
                assigned = cid
                break
        if assigned is None:
            assigned = f"clu_{it.item_id}"
            rep[assigned] = h
            res.canonical_of[assigned] = it.item_id
        res.cluster_of[it.item_id] = assigned

    res.n_clusters = len(res.canonical_of)
    return res


def _make_items(rng: random.Random, n: int, *, repost_rate: float = 0.35, seed_text_len: int = 60) -> list[DedupItem]:
    """构造样本：随机中文式文本 + 一部分近重复「转载」（小改动）。"""
    base = "云舟数控关于年度利润分配方案的公告"
    chars = "云舟数控净利润同比增长个百分点重组预案募集资金股东大会决议公告编号临时披露"
    items: list[DedupItem] = []
    t0 = datetime(2026, 3, 1, 9, 0, 0)
    for i in range(n):
        if items and rng.random() < repost_rate:
            src = rng.choice(items)
            pos = rng.randrange(max(1, len(src.text) - 4))
            text = src.text[:pos] + rng.choice(chars) + src.text[pos + 1 :]
        else:
            text = base + "".join(rng.choice(chars) for _ in range(rng.randrange(seed_text_len // 2, seed_text_len)))
        items.append(
            DedupItem(
                item_id=f"t{i:06d}",
                text=text,
                published_at=t0 + timedelta(minutes=i),
            )
        )
    return items


def _assert_same(got, want) -> None:
    assert got.cluster_of == want.cluster_of, "cluster_of 划分不一致"
    assert got.canonical_of == want.canonical_of, "canonical_of 首发不一致"
    assert got.n_clusters == want.n_clusters, "簇数不一致"


def test_cluster_equivalence_small_constructed():
    rng = random.Random(7)
    items = _make_items(rng, 200)
    _assert_same(cluster_duplicates(items), _ref_cluster_duplicates(items))


def test_cluster_equivalence_random_texts_exact_duplicates():
    """随机文本 + 完全重复文本混合（重复文本 hamming=0 必同簇）。"""
    rng = random.Random(42)
    chars = "根据公司发展战略需要经董事会审议通过现将相关事项公告如下"
    items = []
    t0 = datetime(2026, 5, 2, 10, 0, 0)
    for i in range(150):
        if items and rng.random() < 0.3:
            text = rng.choice(items).text  # 完全重复
        else:
            text = "".join(rng.choice(chars) for _ in range(rng.randrange(20, 80)))
        items.append(DedupItem(item_id=f"x{i:05d}", text=text, published_at=t0 + timedelta(seconds=i)))
    _assert_same(cluster_duplicates(items), _ref_cluster_duplicates(items))


def test_cluster_equivalence_parametric():
    for seed in (0, 1, 2, 3, 4):
        for n, rate in ((80, 0.2), (120, 0.5), (60, 0.0)):
            rng = random.Random(seed)
            items = _make_items(rng, n, repost_rate=rate)
            _assert_same(cluster_duplicates(items), _ref_cluster_duplicates(items))


def test_cluster_equivalence_empty_and_missing_time():
    # 空输入
    assert cluster_duplicates([]).n_clusters == 0
    # 空文本（simhash=0）与缺失时间戳（视为最晚、不当首发）
    items = [
        DedupItem(item_id="a", text="云舟数控公告", published_at=datetime(2026, 1, 1, 8, 0, 0)),
        DedupItem(item_id="b", text="", published_at=datetime(2026, 1, 2, 8, 0, 0)),
        DedupItem(item_id="c", text="", published_at=None),
        DedupItem(item_id="d", text="云舟数控公告", published_at=datetime(2026, 1, 3, 8, 0, 0)),
    ]
    got = cluster_duplicates(items)
    want = _ref_cluster_duplicates(items)
    _assert_same(got, want)
    assert got.canonical_of[got.cluster_of["a"]] == "a"  # 最早者为首发
    assert got.cluster_of["b"] == got.cluster_of["c"]  # 空 hash 同簇


def test_cluster_equivalence_identical_timestamps_stable_ties():
    """published_at 完全相同（排序稳定，按输入顺序贪心）。"""
    rng = random.Random(11)
    t = datetime(2026, 2, 3, 9, 30, 0)
    chars = "本次重组交易对方为独立第三方不构成关联交易尚需股东大会批准"
    items = [
        DedupItem(item_id=f"m{i}", text="".join(rng.choice(chars) for _ in range(40)), published_at=t)
        for i in range(50)
    ]
    # 一半为完全拷贝
    for i in range(25, 50):
        items[i].text = items[i - 25].text
    _assert_same(cluster_duplicates(items), _ref_cluster_duplicates(items))


# ---------------------------------------------------------------------------
# simhash 向量化（阶段 12）：与纯 Python 参照实现逐位一致
# ---------------------------------------------------------------------------


def _random_texts(rng: random.Random, n: int) -> list[str]:
    chars = "云舟数控净利润同比增长个百分点重组预案募集资ABCabc0129，。！；：公告"
    texts = ["" for _ in range(n)]
    for i in range(n):
        kind = i % 5
        if kind == 0:
            texts[i] = ""  # 空
        elif kind == 1:
            texts[i] = rng.choice(chars)  # 单 token（不足 n-gram 分支）
        elif kind == 2:
            texts[i] = "".join(rng.choice(chars) for _ in range(rng.randrange(2, 12)))
        elif kind == 3:
            texts[i] = "".join(rng.choice(chars) for _ in range(rng.randrange(20, 120)))
        else:
            texts[i] = "ReporT 2026" * rng.randrange(1, 8) + "净利润1.23亿"  # ASCII/数字混合
    return texts


def test_simhash_bitwise_equivalent_random():
    rng = random.Random(2026)
    for text in _random_texts(rng, 300):
        assert simhash(text) == _ref_simhash(text), f"指纹不一致: {text!r}"


def test_simhash_bitwise_equivalent_edge_cases():
    cases = [
        "",
        "云舟",
        "云舟数控",
        "云舟数控公告",
        "a",
        "ab",
        "abc",
        "abc def",
        "0123456789" * 5,
        "，。！；：" * 10,  # 仅标点（token 化为空）
        "云舟数控" + "x" * 500,  # 长 ASCII
        "净利润" * 200,  # 长中文
    ]
    for text in cases:
        assert simhash(text) == _ref_simhash(text), f"指纹不一致: {text[:30]!r}"


def test_simhash_tie_vote_not_set():
    """平票（某位恰好半数 gram 为 1）不置位——与参照 x>0 语义一致。

    构造：两个互为按位取反的 gram 指纹各占一半时该位平票；这里用穷举
    小样本统计验证新旧一致即可（random 用例已覆盖大部分路径）。
    """
    for text in ("云舟数", "舟数控", "云舟数控", "控数舟云"):
        assert simhash(text) == _ref_simhash(text)


# ---------------------------------------------------------------------------
# simhash 批量向量化（阶段 15）：与逐条 simhash()（进而纯 Python 参照）逐位一致
# ---------------------------------------------------------------------------


def test_simhash_batch_bitwise_equivalent_random():
    from asg_text_pipeline.dedup import simhash_batch

    rng = random.Random(15)
    texts = _random_texts(rng, 300)
    batched = simhash_batch(texts)
    assert len(batched) == len(texts)
    for got, text in zip(batched, texts, strict=True):
        assert got == simhash(text) == _ref_simhash(text), f"指纹不一致: {text!r}"


def test_simhash_batch_empty_and_mixed():
    from asg_text_pipeline.dedup import simhash_batch

    assert simhash_batch([]) == []
    texts = ["", "", "云舟数控公告", "，。！；：", "a", "ReporT 2026净利润1.23亿"]
    got = simhash_batch(texts)
    assert got[:2] == [0, 0]
    assert got == [simhash(t) for t in texts]


def test_simhash_batch_matches_cluster_duplicates_behavior():
    """cluster_duplicates 批量路径与逐条路径的聚类结果完全一致（等价守护）。"""
    rng = random.Random(99)
    items = _make_items(rng, 200)
    got = cluster_duplicates(items)
    want = _ref_cluster_duplicates(items)
    _assert_same(got, want)


# ---------------------------------------------------------------------------
# 阶段 16：popcount 用 int.bit_count（与 bin().count 参照逐位一致）；批量哈希
# 与 hamming 的组合语义不变
# ---------------------------------------------------------------------------


def test_hamming_matches_bin_count_reference():
    import random as _random

    from asg_text_pipeline.dedup import hamming

    rng = _random.Random(3)
    for _ in range(500):
        a = rng.getrandbits(64)
        b = rng.getrandbits(64)
        assert hamming(a, b) == bin(a ^ b).count("1")
    assert hamming(0, 0) == 0
    assert hamming(0, (1 << 64) - 1) == 64


def test_mock_provider_latency_does_not_change_outputs():
    from asg_models import MockChatProvider
    from asg_models.schema import SYSTEM_PROMPT, build_user_prompt

    text = "云舟数控公告净利润2.53亿元，同比增长12.5%，超出市场一致预期"
    user = build_user_prompt(text, "公告")
    fast = MockChatProvider(seed=7, latency_ms=0.0)
    slow = MockChatProvider(seed=7)
    for _ in range(5):
        out_fast, cost_fast = fast.chat(system=SYSTEM_PROMPT, user=user)
        out_slow, cost_slow = slow.chat(system=SYSTEM_PROMPT, user=user)
        assert out_fast == out_slow and cost_fast == cost_slow
    assert fast.calls == slow.calls == 5
