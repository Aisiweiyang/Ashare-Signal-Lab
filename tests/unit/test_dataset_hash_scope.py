"""dataset_hash 口径收口的单元测试（ADR-013 已知残留 / ADR-014 落地）。

旧口径的输入混入 uuid4 主键（market_text_id/security_id/cluster_id——每次运行
随机），导致「血缘哈希」跨运行不可比（同 seed 两次运行哈希必不同）。收口后：
哈希输入剔除这些主键类字段，只保留内容稳定字段——同 seed 两次运行的
dataset_hash **相同**（跨进程等价性测试做端到端验收，本文件做口径单元验证）。

行为变更声明：新口径哈希与历史 manifest（旧口径）不可比；历史 manifest 只追加
不回写（TEST_REPORT §16 新旧对照）。
"""

from __future__ import annotations

from asg_domain.ids import dataset_hash
from asg_pipeline.experiments import _dataset_hash_input_rows


def _row(**overrides) -> dict:
    base = {
        "market_text_id": "mtx_0123456789ab",
        "security_id": "sec_0123456789ab",
        "cluster_id": "clu_0123456789ab",
        "event_key": "cev_000000",
        "published_at": "2026-01-05 10:00:00",
        "available_at": "2026-01-05 15:00:00",
        "precision": "datetime",
        "direction": "bullish",
        "vs_expectation": "above",
        "certainty": "certain",
        "event_type": "earnings",
    }
    base.update(overrides)
    return base


def test_uuid4_key_fields_excluded_hash_stable():
    """仅 uuid4 主键类字段不同（同一次运行实例差异）→ 哈希相同。"""
    a = dataset_hash(_dataset_hash_input_rows([_row()]))
    b = dataset_hash(
        _dataset_hash_input_rows(
            [_row(market_text_id="mtx_ffffffffffff", security_id="sec_ffffffffffff", cluster_id="clu_ffffffffffff")]
        )
    )
    assert a == b


def test_content_change_changes_hash():
    """内容字段（标签/时间/精度/类型/确定性序号）变化 → 哈希不同。"""
    base = dataset_hash(_dataset_hash_input_rows([_row()]))
    for changed in (
        {"direction": "bearish"},
        {"vs_expectation": "below"},
        {"certainty": "speculative"},
        {"event_type": "orders"},
        {"published_at": "2026-01-06 10:00:00"},
        {"event_key": "cev_000001"},
    ):
        assert base != dataset_hash(_dataset_hash_input_rows([_row(**changed)])), f"字段变化未进入哈希: {changed}"


def test_text_and_row_order_still_matter():
    """text 全文仍不进入哈希（行内容以标签/时间为准），但行序敏感（口径不变）。"""
    rows = [_row(event_key="cev_000000"), _row(event_key="cev_000001", direction="bearish")]
    reversed_rows = list(reversed(rows))
    assert dataset_hash(_dataset_hash_input_rows(rows)) != dataset_hash(_dataset_hash_input_rows(reversed_rows))
