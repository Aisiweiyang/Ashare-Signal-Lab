"""统计热路径优化的逐位等价测试（阶段 11，任务 1）。

参照实现（本文件内的 _ref_* 函数）是优化前的原始实现（TEST_REPORT §13 时代码），
逐字符拷贝自 packages/event_study/src/asg_event_study/study.py 的当时版本。
当前实现必须在多种规模/种子/并列值形态下给出**逐位一致**（bit-for-bit，
不是 approx）的结果——bootstrap 的随机数消费顺序也必须一致。

背景：compare_signals_ic 旧实现每次迭代调用 _spearman_point（内部固定跑
n_boot=10 次无用 bootstrap），2000 次外层循环即 ~4 万次浪费迭代，是 v3
manifest 中 190s「样本外评估」阶段的主要热点之一。
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from asg_event_study.study import _rankdata, compare_signals_ic, spearman_ic

# ---------------------------------------------------------------------------
# 参照实现：优化前的原始代码（只读，禁止修改——它是等价性断言的锚点）
# ---------------------------------------------------------------------------


def _ref_spearman_ic(signals: list[float], rets: list[float], *, n_boot: int = 2000, seed: int = 0) -> dict:
    if len(signals) != len(rets):
        raise ValueError("signals 与 rets 长度不一致")
    n = len(signals)
    if n < 3:
        return {"n": n, "ic": None}
    s = np.asarray(signals, dtype=float)
    r = np.asarray(rets, dtype=float)
    rs = _rankdata(s)
    rr = _rankdata(r)
    rs_c = rs - rs.mean()
    rr_c = rr - rr.mean()
    denom = math.sqrt(float((rs_c**2).sum()) * float((rr_c**2).sum()))
    ic = float((rs_c * rr_c).sum() / denom) if denom > 0 else float("nan")
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        bs, br = rs[idx], rr[idx]
        bs_c = bs - bs.mean()
        br_c = br - br.mean()
        d2 = math.sqrt(float((bs_c**2).sum()) * float((br_c**2).sum()))
        boots[i] = float((bs_c * br_c).sum() / d2) if d2 > 0 else 0.0
    ci_low, ci_high = np.percentile(boots, [2.5, 97.5])
    ic_std = float(boots.std(ddof=1))
    t_stat = ic / (ic_std / math.sqrt(n)) if ic_std > 0 else float("nan")
    return {
        "n": n,
        "ic": round(ic, 6),
        "t_stat": round(t_stat, 3),
        "ci95_low": round(float(ci_low), 6),
        "ci95_high": round(float(ci_high), 6),
    }


def _ref_spearman_point(signals: list[float], rets: list[float]) -> float:
    res = _ref_spearman_ic(signals, rets, n_boot=10, seed=1)
    return res["ic"] if res["ic"] is not None else 0.0


def _ref_compare_signals_ic(obs_a, obs_b, *, n_boot: int = 2000, seed: int = 0) -> dict:
    map_a = {o.event_key: o for o in obs_a}
    map_b = {o.event_key: o for o in obs_b}
    common = sorted(set(map_a) & set(map_b))
    if len(common) < 3:
        return {"n": len(common), "delta_ic": None, "ci95_low": None, "ci95_high": None}
    rng = np.random.default_rng(seed)
    n = len(common)
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        keys = [common[j] for j in idx]
        ic_a = _ref_spearman_point([map_a[k].signal for k in keys], [map_a[k].forward_ret for k in keys])
        ic_b = _ref_spearman_point([map_b[k].signal for k in keys], [map_b[k].forward_ret for k in keys])
        deltas[i] = ic_a - ic_b
    point = _ref_spearman_point([map_a[k].signal for k in common], [map_a[k].forward_ret for k in common]) - _ref_spearman_point(
        [map_b[k].signal for k in common], [map_b[k].forward_ret for k in common]
    )
    ci_low, ci_high = np.percentile(deltas, [2.5, 97.5])
    return {
        "n": n,
        "delta_ic": round(float(point), 6),
        "ci95_low": round(float(ci_low), 6),
        "ci95_high": round(float(ci_high), 6),
        "h1_supported": bool(ci_low > 0),
    }


# ---------------------------------------------------------------------------
# 测试样本构造
# ---------------------------------------------------------------------------


def _bitwise_eq(a, b) -> bool:
    """逐位相等（NaN 位置一致即视为相等——两侧同为 NaN 是同一行为）。"""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_bitwise_eq(a[k], b[k]) for k in a)
    if isinstance(a, float) and isinstance(b, float):
        return (a == b) or (math.isnan(a) and math.isnan(b))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_bitwise_eq(x, y) for x, y in zip(a, b, strict=True))
    return a == b


class _Obs:
    def __init__(self, key: str, signal: float, forward_ret: float) -> None:
        self.event_key = key
        self.signal = signal
        self.forward_ret = forward_ret


def _make_obs(rng: np.random.Generator, n: int, *, ties: bool = False) -> list[_Obs]:
    # ties=True 为并列值形态：信号取小基数（合成信号 0/±x 常见并列）
    signals = rng.choice([0.0, 1.0, -1.0, 2.0], size=n) if ties else rng.normal(size=n)
    rets = 0.3 * signals + rng.normal(scale=1.8, size=n)
    return [_Obs(f"e{i:05d}", float(signals[i]), float(rets[i])) for i in range(n)]


_SCENARIOS = [
    (597, 0, False),  # v3 实际观测规模
    (200, 7, False),
    (97, 3, True),  # 大量并列
    (10, 0, True),  # 小样本 + 并列
    (3, 1, False),  # 最小样本
]


@pytest.mark.parametrize("n,seed,ties", _SCENARIOS)
def test_spearman_ic_bitwise_equivalent(n, seed, ties):
    rng = np.random.default_rng(1000 + seed + n)
    signals = rng.normal(size=n).tolist() if not ties else rng.choice([0.0, 1.0, -1.0], size=n).tolist()
    rets = (0.4 * np.asarray(signals) + rng.normal(scale=2.0, size=n)).tolist()
    got = spearman_ic(signals, rets, n_boot=2000, seed=seed)
    want = _ref_spearman_ic(signals, rets, n_boot=2000, seed=seed)
    assert _bitwise_eq(got, want), f"n={n} seed={seed} ties={ties}\n got={got}\nwant={want}"


def test_spearman_ic_small_sample_identity():
    assert spearman_ic([0.1, 0.2], [0.1, 0.2]) == {"n": 2, "ic": None}
    assert _bitwise_eq(
        spearman_ic([0.1, 0.2, 0.3], [0.1, 0.2, 0.1], n_boot=1),
        _ref_spearman_ic([0.1, 0.2, 0.3], [0.1, 0.2, 0.1], n_boot=1),
    )


@pytest.mark.parametrize("n,seed,ties", _SCENARIOS)
def test_compare_signals_ic_bitwise_equivalent(n, seed, ties):
    rng = np.random.default_rng(2000 + seed + n)
    obs_a = _make_obs(rng, n, ties=ties)
    obs_b = _make_obs(rng, n, ties=ties)
    got = compare_signals_ic(obs_a, obs_b, n_boot=2000, seed=seed)
    want = _ref_compare_signals_ic(obs_a, obs_b, n_boot=2000, seed=seed)
    assert _bitwise_eq(got, want), f"n={n} seed={seed} ties={ties}\n got={got}\nwant={want}"


def test_compare_signals_ic_partial_overlap_and_small():
    rng = np.random.default_rng(99)
    a = _make_obs(rng, 50)
    b = _make_obs(rng, 50)[:30]  # 部分重叠
    assert _bitwise_eq(
        compare_signals_ic(a, b, n_boot=100, seed=5), _ref_compare_signals_ic(a, b, n_boot=100, seed=5)
    )
    # <3 个共同事件
    a2 = [_Obs("k1", 1.0, 0.1), _Obs("k2", 2.0, 0.2)]
    b2 = [_Obs("k1", 1.0, 0.1), _Obs("k2", 2.0, 0.2)]
    got = compare_signals_ic(a2, b2, n_boot=10, seed=0)
    assert got == {"n": 2, "delta_ic": None, "ci95_low": None, "ci95_high": None}
    assert "h1_supported" not in got
