"""事件层聚类兜底原型的单元测试（exploratory，ADR-015）。

构造案例覆盖两条核心规则：
- 拆分：同簇内数字签名不同（同模板、仅数字不同）→ 拆开，且不同事件不会被合并回去；
- 合并：签名相同 + 同证券 + 时间窗内 + 高余弦（改写转载被 SimHash 漏聚的情形）→ 并回。
"""

from __future__ import annotations

import datetime as dt

from asg_text_pipeline.dedup import ClusterResult, DedupItem, cluster_duplicates
from asg_text_pipeline.event_layer import number_signature, refine_clusters

_T0 = dt.datetime(2026, 3, 2, 9, 0, 0)


def _item(iid: str, text: str, hours: float = 0.0) -> DedupItem:
    return DedupItem(item_id=iid, text=text, published_at=_T0 + dt.timedelta(hours=hours))


def _clustering(pairs: list[tuple[str, str]]) -> ClusterResult:
    res = ClusterResult()
    for item_id, cid in pairs:
        res.cluster_of[item_id] = cid
        if cid.startswith("clu_") and res.canonical_of.get(cid) is None:
            res.canonical_of[cid] = cid.removeprefix("clu_")
    res.n_clusters = len(res.canonical_of)
    return res


def test_number_signature_prefix_invariant_and_discriminating():
    base = "云舟数控（601907）公告净利润2.53亿元，同比增长12.5%"
    repost = "【转载】" + base
    assert number_signature(repost) == number_signature(base)  # 前缀插入不变式
    other = "云舟数控（601907）公告净利润1.87亿元，同比增长9.3%"
    assert number_signature(other) != number_signature(base)  # 不同数字可区分


def test_split_separates_same_template_different_numbers():
    # 仅一个数字不同（模拟例行公告的份额/日期变化）：SimHash 因指纹投票对小扰动
    # 不敏感而误聚为一簇；数字签名应把两者拆开
    t1 = "云舟数控（601907）公告本期债券付息登记日为2026年3月18日，凡在登记日当日收市后持有本期债券的投资者均享有本次付息权益，票面利率为3.15%，期限为五年，付息资金将于次一交易日划付至持有人账户，请投资者关注相关安排。"
    t2 = "云舟数控（601907）公告本期债券付息登记日为2026年3月18日，凡在登记日当日收市后持有本期债券的投资者均享有本次付息权益，票面利率为3.16%，期限为五年，付息资金将于次一交易日划付至持有人账户，请投资者关注相关安排。"
    items = [_item("a", t1), _item("b", t2, hours=2.0)]
    base = cluster_duplicates(items)  # Hamming ≤ 6 → 误聚为一簇
    assert base.n_clusters == 1, "构造前提：两条被 SimHash 误聚"

    ref = refine_clusters(items, base)
    assert ref.n_clusters == 2
    assert ref.cluster_of["a"] != ref.cluster_of["b"]
    assert ref.canonical_of[ref.cluster_of["a"]] == "a"  # 时间最早者为组首


def test_merge_recovers_rewritten_repost():
    body = "沛然电子（300880）的经营牌照续期审查进展顺利，有望按期获得续期，最终结果以监管公告为准，公司日常生产经营活动一切正常。"
    root = _item("root", body, hours=0.0)
    repost = _item("rp", "【转载】" + body, hours=5.0)
    # 构造「SimHash 漏聚」前提：两文本被放进不同簇（真实场景为改写致 Hamming>6）
    base = _clustering([("root", "clu_root"), ("rp", "clu_rp")])
    security_of = {"root": "sec_A", "rp": "sec_A"}

    ref = refine_clusters([root, repost], base, security_of=security_of)
    assert ref.n_clusters == 1
    assert ref.cluster_of["root"] == ref.cluster_of["rp"]
    assert ref.canonical_of[ref.cluster_of["root"]] == "root"  # 根事件时间为最早


def test_merge_gates_block_wrong_merges():
    body = "沛然电子（300880）的经营牌照续期审查进展顺利，有望按期获得续期，最终结果以监管公告为准，公司日常生产经营活动一切正常。"
    other_body = "岚台生物（300323）研发的新药完成三期临床，数据独立委员会建议继续推进试验，公司将在年报中披露进展。"
    root = _item("root", body, hours=0.0)
    later_other = _item("other", other_body, hours=5.0)
    far = _item("far", other_body, hours=72.0)

    # 不同证券 → 不合并
    base_two = _clustering([("root", "clu_root"), ("other", "clu_other")])
    ref = refine_clusters(
        [root, later_other], base_two, security_of={"root": "sec_A", "other": "sec_B"}
    )
    assert ref.n_clusters == 2

    # 同证券但超出时间窗 → 不合并（base 须与条目一致）
    base_far = _clustering([("root", "clu_root"), ("far", "clu_far")])
    ref = refine_clusters([root, far], base_far, security_of={"root": "sec_A", "far": "sec_A"})
    assert ref.n_clusters == 2


def test_split_then_no_remerge_different_numbers():
    """拆分后的「同模板不同数字」对不会被合并步骤并回去（签名门控）。"""
    t1 = "云舟数控公告净利润2.53亿元，同比增长12.5%，经营现金流稳健"
    t2 = "云舟数控公告净利润1.87亿元，同比增长9.3%，经营现金流稳健"
    items = [_item("a", t1), _item("b", t2, hours=2.0)]
    base = cluster_duplicates(items)
    ref = refine_clusters(items, base, security_of={"a": "sec_A", "b": "sec_A"})
    assert ref.n_clusters == 2


def test_refine_without_security_is_deterministic():
    items = [
        _item("a", "云舟数控公告净利润2.53亿元，同比增长12.5%，经营现金流稳健"),
        _item("b", "云舟数控公告净利润1.87亿元，同比增长9.3%，经营现金流稳健", hours=2.0),
    ]
    base = cluster_duplicates(items)
    r1 = refine_clusters(items, base)
    r2 = refine_clusters(items, base)
    assert r1.cluster_of == r2.cluster_of
    assert r1.canonical_of == r2.canonical_of
