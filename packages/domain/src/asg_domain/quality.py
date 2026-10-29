"""轻量数据质量契约（借鉴 Great Expectations 思想，自实现；ADR-005 相关取舍见 docs）。

每个 contract 是纯函数：接收行列表，返回断言结果列表；
workers 与 tests 共用同一实现，保证「流水线检查」与「测试断言」不漂移。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from .errors import AvailabilityError

AssertionResult = dict[str, Any]  # {"name", "passed", "violations", "detail"}


def _assert(name: str, violations: list[str], detail: str = "") -> AssertionResult:
    return {"name": name, "passed": not violations, "violations": violations[:20], "n_violations": len(violations), "detail": detail}


def expect_no_future_rows(rows: list[dict[str, Any]], now: datetime, ts_key: str = "available_at") -> AssertionResult:
    bad = [str(r.get("id", i)) for i, r in enumerate(rows) if isinstance(r.get(ts_key), datetime) and r[ts_key] > now]
    return _assert("no_future_rows", bad)


def expect_unique(rows: list[dict[str, Any]], key: str) -> AssertionResult:
    seen: dict[Any, int] = {}
    for i, r in enumerate(rows):
        seen.setdefault(r.get(key), i)
    bad = [f"{key}={k} 重复" for k, first in seen.items() if sum(1 for r in rows if r.get(key) == k) > 1]
    return _assert(f"unique_{key}", bad)


def expect_not_null(rows: list[dict[str, Any]], key: str) -> AssertionResult:
    bad = [str(r.get("id", i)) for i, r in enumerate(rows) if r.get(key) is None]
    return _assert(f"not_null_{key}", bad)


def expect_labels_in_domain(rows: list[dict[str, Any]]) -> AssertionResult:
    from .enums import Certainty, Direction, VsExpectation

    d_ok = {d.value for d in Direction}
    v_ok = {v.value for v in VsExpectation}
    c_ok = {c.value for c in Certainty}
    bad: list[str] = []
    for i, r in enumerate(rows):
        rid = str(r.get("id", i))
        if r.get("direction") not in d_ok:
            bad.append(f"{rid}:direction={r.get('direction')!r}")
        if r.get("vs_expectation") not in v_ok:
            bad.append(f"{rid}:vs_expectation={r.get('vs_expectation')!r}")
        if r.get("certainty") not in c_ok:
            bad.append(f"{rid}:certainty={r.get('certainty')!r}")
    return _assert("labels_in_domain", bad)


def expect_price_continuity(
    bars_by_security: dict[str, list[dict[str, Any]]],
    trading_days: list[date],
) -> AssertionResult:
    """交易日无缺口：每个活跃证券在每个交易日都有 bar（停牌行以 suspended=True 存在）。"""
    bad: list[str] = []
    day_set = set(trading_days)
    for sec_id, bars in bars_by_security.items():
        have = {b["trade_date"] for b in bars}
        missing = day_set - have
        if missing:
            bad.append(f"{sec_id} 缺 {len(missing)} 个交易日（如 {sorted(missing)[0]}）")
    return _assert("price_continuity", bad)


def expect_available_at_covers_published(rows: list[dict[str, Any]]) -> AssertionResult:
    """available_at 必须存在且 >= published_at。"""
    bad: list[str] = []
    for i, r in enumerate(rows):
        rid = str(r.get("id", i))
        pub, avail = r.get("published_at"), r.get("available_at")
        if not isinstance(avail, datetime):
            bad.append(f"{rid}: available_at 缺失")
        elif isinstance(pub, datetime) and avail < pub:
            bad.append(f"{rid}: available_at < published_at")
    return _assert("available_at_covers_published", bad)


def expect_no_duplicate_clusters_across_sets(train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]]) -> AssertionResult:
    """切分防泄漏：同一 cluster 不得同时出现在训练与测试。"""
    train_clusters = {r.get("cluster_id") for r in train_rows if r.get("cluster_id")}
    bad = sorted({r.get("cluster_id") for r in test_rows if r.get("cluster_id") in train_clusters})
    return _assert("no_cluster_leak_across_splits", bad)


def require_available(at: datetime | None, decision_time: datetime, what: str) -> None:
    """严格可用性门控：不满足直接抛 AvailabilityError（调用方不得吞掉）。"""
    if at is None:
        raise AvailabilityError(f"{what} 的 available_at 缺失，严格模式下拒绝使用")
    if at > decision_time:
        raise AvailabilityError(f"{what} 在决策时点 {decision_time} 不可得（available_at={at}）")


def run_all(rows: list[dict[str, Any]], checks: list[Callable[[list[dict[str, Any]]], AssertionResult]]) -> dict[str, Any]:
    results = [c(rows) for c in checks]
    return {
        "passed": all(r["passed"] for r in results),
        "assertions": results,
        "n_assertions": len(results),
    }
