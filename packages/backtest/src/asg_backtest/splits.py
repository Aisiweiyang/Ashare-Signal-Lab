"""时间切分（RESEARCH_PROTOCOL §4/§6）：禁随机、带 embargo、簇完整性。

两种方案：
- walk-forward（v1 预注册）：训练窗单调前移，杜绝「未来训练」；
- purged K-fold（v2 对照，§6 预留）：时间连续块轮换做测试，训练 = 测试窗外
  事件并做**簇清除**（同 cluster 不跨集）与 **embargo 缓冲**（边界标签窗重叠）。
  对照价值：walk-forward 只用过去训练（更保守），purged K-fold 样本利用更充分；
  两者结论一致 → 时间稳健性证据；不一致 → 提示结论受切分方式影响（如实报告）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from asg_domain.errors import AshareSignalError
from asg_domain.quality import expect_no_duplicate_clusters_across_sets


@dataclass(frozen=True)
class Fold:
    fold_id: int
    train_start: dt.date
    train_end: dt.date
    test_start: dt.date
    test_end: dt.date
    scheme: str = "walk_forward"  # walk_forward | purged_kfold


def walk_forward_folds(
    *,
    event_days: list[dt.date],
    n_folds: int = 3,
    embargo_days: int = 2,
) -> list[Fold]:
    """按时间排序的事件日做 walk-forward 切分。

    每折：train = [start, cut_i]，embargo 隔离期，test = [cut_i + embargo, cut_{i+1})。
    event_days 少于 n_folds*2 时抛错（样本不足以支撑切分，宁可不跑也不造假）。
    """
    days = sorted(set(event_days))
    if len(days) < n_folds * 2:
        raise AshareSignalError(
            f"事件日数量不足以做 {n_folds} 折 walk-forward（仅 {len(days)} 天）"
        )
    folds: list[Fold] = []
    # 均分：前 n_folds 段做训练累计点，最后一段留作最末测试
    step = len(days) // (n_folds + 1)
    for i in range(n_folds):
        cut = days[step * (i + 1) - 1]
        train_start = days[0]
        train_end = cut
        # embargo：从 cut 起跳过 embargo_days 个自然日
        embargo_end = cut + dt.timedelta(days=embargo_days)
        test_candidates = [d for d in days if d > embargo_end]
        if not test_candidates:
            continue
        test_start = test_candidates[0]
        # 测试窗到下一训练切点（最后一折到最后）
        if i < n_folds - 1:
            next_cut = days[step * (i + 2) - 1]
            test_end = next_cut
        else:
            test_end = days[-1]
        if test_start > test_end:
            # embargo 推移吞掉了整个测试窗（数据太短）：跳过该折，不硬造窗口
            continue
        folds.append(Fold(i, train_start, train_end, test_start, test_end))
    return folds


def split_rows_by_fold(
    rows: list[dict],
    fold: Fold,
    *,
    day_key: str = "decision_day",
    cluster_key: str | None = "cluster_id",
) -> tuple[list[dict], list[dict]]:
    """按折切分行；cluster_key 给定时强制同簇不跨集（否则抛 AshareSignalError）。"""
    train = [r for r in rows if fold.train_start <= r[day_key] <= fold.train_end]
    test = [r for r in rows if fold.test_start <= r[day_key] <= fold.test_end]
    if cluster_key:
        res = expect_no_duplicate_clusters_across_sets(train, test)
        if not res["passed"]:
            raise AshareSignalError(f"切分泄漏：同 cluster 跨训练/测试集: {res['violations'][:5]}")
    return train, test


def purged_kfold_folds(
    *,
    event_days: list[dt.date],
    n_splits: int = 5,
    embargo_days: int = 2,
) -> list[Fold]:
    """purged K-fold 的时间块构造（RESEARCH_PROTOCOL §6 预留的对照切分）。

    事件日排序后切成 n_splits 个**时间连续块**，每块轮换做测试；
    train_start/train_end 仅记录全跨度（信息性），实际训练集由
    `split_rows_purged` 在切分时做边界缓冲与簇清除后确定。
    event_days 少于 n_splits 时抛错（宁可不跑也不造假）。
    """
    days = sorted(set(event_days))
    if len(days) < n_splits:
        raise AshareSignalError(
            f"事件日数量不足以做 {n_splits} 折 purged K-fold（仅 {len(days)} 天）"
        )
    # 近似均分的连续块：第 i 块起点 = round(i * n / k)（禁随机，纯确定性）
    folds: list[Fold] = []
    n = len(days)
    for i in range(n_splits):
        lo = round(i * n / n_splits)
        hi = round((i + 1) * n / n_splits)
        if hi <= lo:
            continue
        folds.append(
            Fold(
                fold_id=i,
                train_start=days[0],
                train_end=days[-1],
                test_start=days[lo],
                test_end=days[hi - 1],
                scheme="purged_kfold",
            )
        )
    return folds


def purge_buffer_days(*, embargo_days: int, horizon: int) -> int:
    """测试窗每侧的日历日缓冲：embargo 基线 + 标签窗（T+horizon 交易日 ≈ ≤2× 日历日）。

    双侧缓冲的理由（写入代码以便审计）：
    - 左侧：测试窗前 train 事件的**标签**（T+horizon 收益）不得伸入测试窗；
    - 右侧：测试窗后 train 事件的标签窗与测试窗内事件的标签窗不得重叠
      （López de Prado purged K-fold 的 serial-correlation 缘由）。
    """
    return embargo_days + max(horizon, 1) * 2


def split_rows_purged(
    rows: list[dict],
    fold: Fold,
    *,
    buffer_days: int,
    day_key: str = "decision_day",
    cluster_key: str | None = "cluster_id",
) -> tuple[list[dict], list[dict]]:
    """purged 切分：训练 = 测试窗 ±buffer 之外的事件，并清除与测试集同簇的 train 行。

    簇清除是**主动剔除**（与 walk-forward 的「发现即抛错」不同——本方案天然允许
    训练/测试时间交错，必须先剔除再断言）；清除后若仍发现同簇跨集即为程序缺陷，
    照旧抛 AshareSignalError。train 可能为空（该折无可训练样本），由调用方决定
    跳过并在运行记录中如实计数。
    """
    left = fold.test_start - dt.timedelta(days=buffer_days)
    right = fold.test_end + dt.timedelta(days=buffer_days)
    train = [r for r in rows if not (left <= r[day_key] <= right)]
    test = [r for r in rows if fold.test_start <= r[day_key] <= fold.test_end]
    if cluster_key:
        test_clusters = {r[cluster_key] for r in test if r.get(cluster_key)}
        train = [r for r in train if r.get(cluster_key) not in test_clusters]
        res = expect_no_duplicate_clusters_across_sets(train, test)
        if not res["passed"]:
            raise AshareSignalError(
                f"purged 切分缺陷：清除后仍同 cluster 跨集: {res['violations'][:5]}"
            )
    return train, test


def random_split_dangerous(rows: list[dict], *, seed: int = 0) -> tuple[list[dict], list[dict]]:
    """危险接口：随机切分（仅供泄漏对照实验，结果一律标记 exploratory/禁止结论）。

    保留该函数是因为协议 §7 要求「故意随机切分必须可被检测/对比」；
    调用方有义务在运行记录中标记 allow_future_leak=True。
    """
    import random

    rng = random.Random(seed)
    shuffled = list(rows)
    rng.shuffle(shuffled)
    cut = len(shuffled) // 2
    return shuffled[:cut], shuffled[cut:]
