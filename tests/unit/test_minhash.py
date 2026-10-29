"""MinHash+LSH 近重复聚类原型的单元测试（exploratory，ADR-009 备选路线；阶段 14）。

对照基准是既有 SimHash 管线：gram 口径一致（字符 3-gram，同一 `_char_ngrams`）。
原型为 opt-in 研究对照，默认管线不经过本模块。

LSH 召回率由 S-curve 决定（128 置换 / 16 带 × 8 行，阈值 0.80）：
P(候选) = 1-(1-s^8)^16 —— s=0.9 时 ≈0.9999，s=0.8 时 ≈0.947。
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
from asg_text_pipeline.dedup import DedupItem
from asg_text_pipeline.minhash import (
    MinHashConfig,
    exact_jaccard,
    jaccard_estimate,
    minhash_signature,
)

_T0 = dt.datetime(2026, 3, 2, 9, 0, 0)

_BASE = (
    "云舟数控（601907）公告2026年度净利润2.53亿元，同比增长12.5%，"
    "超出市场一致预期。公司同时披露第一季订单总额8.7亿元，产能利用率维持高位。"
    "董事会审议通过利润分配预案，拟每10股派发现金红利1.2元。"
)


def _item(iid: str, text: str, hours: float = 0.0) -> DedupItem:
    return DedupItem(item_id=iid, text=text, published_at=_T0 + dt.timedelta(hours=hours))


def test_identical_text_signature_stable_and_similarity_one():
    a = minhash_signature(_BASE, seed=7)
    b = minhash_signature(_BASE, seed=7)
    assert np.array_equal(a, b)
    assert jaccard_estimate(a, b) == 1.0
    assert exact_jaccard(_BASE, _BASE) == 1.0


def test_estimate_close_to_exact_jaccard():
    """估计 Jaccard 与精确集合 Jaccard 的偏差应在置换数允许范围内（±0.1）。"""
    repost = "【转载】" + _BASE
    est = jaccard_estimate(minhash_signature(_BASE, seed=7), minhash_signature(repost, seed=7))
    exact = exact_jaccard(_BASE, repost)
    assert exact > 0.85  # 前缀插入对长文本的 3-gram 集合扰动很小
    assert abs(est - exact) < 0.10


def test_prefix_invariant_stays_above_threshold():
    """转载前缀不把相似度拉到阈值以下（漏聚防线与 SimHash 同款语义）。"""
    from asg_text_pipeline.minhash import DEFAULT_THRESHOLD

    repost = "【转载】【快讯】" + _BASE
    assert jaccard_estimate(minhash_signature(_BASE, seed=7), minhash_signature(repost, seed=7)) >= DEFAULT_THRESHOLD


def test_same_template_different_numbers_separated():
    """同模板、仅数字不同：Jaccard 应明显低于转载改写的情形（误聚拆分能力）。"""
    other = _BASE.replace("2.53亿元", "1.87亿元").replace("12.5%", "9.3%").replace("8.7亿元", "6.2亿元")
    assert exact_jaccard(_BASE, other) < 0.80
    assert jaccard_estimate(minhash_signature(_BASE, seed=7), minhash_signature(other, seed=7)) < 0.80


def test_unrelated_texts_far_apart():
    other = "岚台生物发布新药临床试验批准通知书，公司在研管线推进顺利，本期研发费用投入增加。"
    assert jaccard_estimate(minhash_signature(_BASE, seed=7), minhash_signature(other, seed=7)) < 0.30


def test_seed_changes_signature_but_not_similarity_ordering():
    sig_a1 = minhash_signature(_BASE, seed=1)
    sig_a2 = minhash_signature(_BASE, seed=2)
    assert not np.array_equal(sig_a1, sig_a2)  # 不同 seed 置换函数不同
    repost = "【转载】" + _BASE
    unrelated = "岚台生物发布新药临床试验批准通知书。"
    near_1 = jaccard_estimate(sig_a1, minhash_signature(repost, seed=1))
    far_1 = jaccard_estimate(sig_a1, minhash_signature(unrelated, seed=1))
    near_2 = jaccard_estimate(sig_a2, minhash_signature(repost, seed=2))
    far_2 = jaccard_estimate(sig_a2, minhash_signature(unrelated, seed=2))
    assert near_1 > far_1 and near_2 > far_2  # 相似性排序对 seed 稳健


def test_cluster_deterministic_and_groups_reposts():
    from asg_text_pipeline.minhash import cluster_minhash

    items = [
        _item("t1", _BASE),
        _item("t2", "【转载】" + _BASE, hours=2.0),
        _item("t3", _BASE.replace("2.53亿元", "1.87亿元").replace("12.5%", "9.3%").replace("8.7亿元", "6.2亿元"), hours=3.0),
        _item("t4", "岚台生物发布新药临床试验批准通知书，公司在研管线推进顺利。", hours=4.0),
    ]
    r1 = cluster_minhash(items, seed=7)
    r2 = cluster_minhash(items, seed=7)
    assert r1.cluster_of == r2.cluster_of and r1.canonical_of == r2.canonical_of
    assert r1.n_clusters == 3  # t1+t2 同簇（转载），t3 数字不同另立，t4 独立
    assert r1.cluster_of["t1"] == r1.cluster_of["t2"]
    assert r1.cluster_of["t3"] != r1.cluster_of["t1"]
    assert r1.canonical_of[r1.cluster_of["t1"]] == "t1"  # 首发为最早者


def test_cluster_empty_and_single():
    from asg_text_pipeline.minhash import cluster_minhash

    empty = cluster_minhash([], seed=7)
    assert empty.n_clusters == 0
    one = cluster_minhash([_item("only", _BASE)], seed=7)
    assert one.n_clusters == 1 and one.cluster_of["only"].startswith("clu_")


def test_config_validation():
    with pytest.raises(ValueError):
        MinHashConfig(num_perm=10, bands=16, rows=8)  # num_perm < bands*rows
    with pytest.raises(ValueError):
        MinHashConfig(num_perm=128, bands=0, rows=8)
