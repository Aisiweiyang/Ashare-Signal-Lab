"""purged K-fold 切分（协议 §6 预留）：块确定性、边界缓冲、簇清除、泄漏断言。"""

from __future__ import annotations

import datetime as dt
from itertools import pairwise

import pytest
from asg_backtest import purge_buffer_days, purged_kfold_folds, split_rows_purged
from asg_domain.errors import AshareSignalError

pytestmark = pytest.mark.leakage


def _days(n: int, *, start: dt.date = dt.date(2026, 3, 2)) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range(n)]


def _rows(days: list[dt.date], *, clusters: dict[dt.date, str] | None = None) -> list[dict]:
    return [
        {"decision_day": d, "cluster_id": (clusters or {}).get(d, f"c{d:%Y%m%d}"), "i": i}
        for i, d in enumerate(days)
    ]


class TestFoldConstruction:
    def test_blocks_partition_all_days_deterministically(self):
        days = _days(30)
        folds = purged_kfold_folds(event_days=days, n_splits=5)
        assert [f.fold_id for f in folds] == [0, 1, 2, 3, 4]
        # 块连续且无缝覆盖
        ordered = sorted(folds, key=lambda f: f.test_start)
        for prev, nxt in pairwise(ordered):
            assert nxt.test_start > prev.test_end
        assert ordered[0].test_start == days[0]
        assert ordered[-1].test_end == days[-1]
        # 同参数完全可复现（禁随机）
        again = purged_kfold_folds(event_days=days, n_splits=5)
        assert again == folds

    def test_insufficient_days_rejected(self):
        with pytest.raises(AshareSignalError, match="不足以"):
            purged_kfold_folds(event_days=_days(3), n_splits=5)


class TestBuffer:
    def test_buffer_formula(self):
        assert purge_buffer_days(embargo_days=2, horizon=1) == 4
        assert purge_buffer_days(embargo_days=2, horizon=2) == 6


class TestPurgedSplit:
    def test_train_excludes_buffered_window_both_sides(self):
        days = _days(30)
        folds = purged_kfold_folds(event_days=days, n_splits=5)
        fold = folds[2]  # 中间块：两侧都应有被缓冲剔除的训练行
        rows = _rows(days)
        train, test = split_rows_purged(rows, fold, buffer_days=3)
        assert test and train
        left_edge = fold.test_start - dt.timedelta(days=3)
        right_edge = fold.test_end + dt.timedelta(days=3)
        assert all(not (left_edge <= r["decision_day"] <= right_edge) for r in train)
        assert {r["decision_day"] for r in test} == set(days) & {
            d for d in days if fold.test_start <= d <= fold.test_end
        }

    def test_cluster_purge_removes_shared_clusters(self):
        days = _days(30)
        folds = purged_kfold_folds(event_days=days, n_splits=5)
        fold = folds[0]
        clusters = dict.fromkeys(days, "shared-cluster")  # 全部同簇：训练必被清空
        rows = _rows(days, clusters=clusters)
        train, test = split_rows_purged(rows, fold, buffer_days=0)
        assert test
        assert train == []  # 同簇清除后训练为空：由调用方如实跳过

    def test_no_cluster_leakage_after_purge(self):
        days = _days(40)
        clusters = {d: f"cluster_{i % 7}" for i, d in enumerate(days)}
        rows = _rows(days, clusters=clusters)
        for fold in purged_kfold_folds(event_days=days, n_splits=5):
            train, test = split_rows_purged(rows, fold, buffer_days=4)
            test_clusters = {r["cluster_id"] for r in test}
            assert not ({r["cluster_id"] for r in train} & test_clusters)

    def test_split_is_non_mutating(self):
        days = _days(20)
        rows = _rows(days)
        snapshot = [dict(r) for r in rows]
        for fold in purged_kfold_folds(event_days=days, n_splits=4):
            split_rows_purged(rows, fold, buffer_days=2)
        assert rows == snapshot
