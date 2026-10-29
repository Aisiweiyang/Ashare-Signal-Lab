"""事件研究与统计推断：分组 CAR、IC、bootstrap 置信区间、衰减曲线。"""

from __future__ import annotations

import bisect
import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np

from .returns import PriceBook


@dataclass
class EventObservation:
    """一个事件的一次观测：信号 + 前向收益 + 基准收益。"""

    event_key: str
    security_id: str
    decision_day: dt.date
    signal: float
    forward_ret: float
    benchmark_ret: float
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def abnormal_ret(self) -> float:
        return self.forward_ret - self.benchmark_ret


def build_observations(
    *,
    price_book: PriceBook,
    benchmark_nav: dict[dt.date, float],
    events: list[dict],
    horizon: int = 1,
) -> list[EventObservation]:
    """events 元素: {event_key, security_id, decision_day, signal, labels?}。

    forward/benchmark 数据不足的事件被剔除（并计入 n_dropped）。
    """
    obs: list[EventObservation] = []
    days_sorted = sorted(benchmark_nav)
    nav_values = [benchmark_nav[d] for d in days_sorted]
    for ev in events:
        fr = price_book.forward_return(ev["security_id"], ev["decision_day"], horizon)
        if fr is None:
            continue
        entry_d = fr["entry_date"]
        exit_d = fr["exit_date"]
        # 基准同窗收益
        entry_nav = nav_on_or_before(days_sorted, nav_values, entry_d)
        exit_nav = nav_on_or_before(days_sorted, nav_values, exit_d)
        if entry_nav is None or exit_nav is None or entry_nav <= 0:
            continue
        bench = exit_nav / entry_nav - 1.0
        obs.append(
            EventObservation(
                event_key=ev["event_key"],
                security_id=ev["security_id"],
                decision_day=ev["decision_day"],
                signal=float(ev["signal"]),
                forward_ret=fr["ret"],
                benchmark_ret=bench,
                labels=ev.get("labels", {}),
            )
        )
    return obs


def nav_on_or_before(days: list[dt.date], values: list[float], day: dt.date) -> float | None:
    """最后一个 ≤ day 的净值（days 升序）。二分与线性扫描语义一致。"""
    pos = bisect.bisect_right(days, day) - 1
    return values[pos] if pos >= 0 else None


def group_stats(obs: list[EventObservation], *, n_boot: int = 2000, seed: int = 0) -> dict:
    """分组异常收益统计：均值、t 值、bootstrap 95% CI、样本量。"""
    if not obs:
        return {"n": 0, "mean_abnormal": None}
    arr = np.array([o.abnormal_ret for o in obs], dtype=float)
    n = len(arr)
    mean = float(arr.mean())
    se = float(arr.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    t_stat = mean / se if se and not math.isnan(se) and se > 0 else float("nan")
    rng = np.random.default_rng(seed)
    if n >= 3:
        boots = rng.choice(arr, size=(n_boot, n), replace=True).mean(axis=1)
        ci_low, ci_high = np.percentile(boots, [2.5, 97.5])
    else:
        ci_low, ci_high = float("nan"), float("nan")
    return {
        "n": n,
        "mean_abnormal": round(mean, 6),
        "std_abnormal": round(float(arr.std(ddof=1)), 6) if n > 1 else None,
        "t_stat": round(t_stat, 3),
        "ci95_low": round(float(ci_low), 6),
        "ci95_high": round(float(ci_high), 6),
    }


def spearman_ic(signals: list[float], rets: list[float], *, n_boot: int = 2000, seed: int = 0) -> dict:
    """Spearman 秩相关 IC + bootstrap 95% CI + t 值。

    bootstrap 向量化（阶段 11）：一次抽取 (n_boot, n) 索引矩阵与逐次抽取
    size=n 消耗同一随机流（等价性测试锁定），逐位结果一致。
    """
    if len(signals) != len(rets):
        raise ValueError("signals 与 rets 长度不一致")
    n = len(signals)
    if n < 3:
        return {"n": n, "ic": None}
    s = np.asarray(signals, dtype=float)
    r = np.asarray(rets, dtype=float)
    # 并列秩取平均
    rs = _rankdata(s)
    rr = _rankdata(r)
    rs_c = rs - rs.mean()
    rr_c = rr - rr.mean()
    denom = math.sqrt(float((rs_c**2).sum()) * float((rr_c**2).sum()))
    ic = float((rs_c * rr_c).sum() / denom) if denom > 0 else float("nan")
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    if n_boot > 0:
        idx = rng.integers(0, n, size=(n_boot, n))
        bs = rs[idx]
        br = rr[idx]
        bs_c = bs - bs.mean(axis=1, keepdims=True)
        br_c = br - br.mean(axis=1, keepdims=True)
        num = (bs_c * br_c).sum(axis=1)
        d2 = np.sqrt((bs_c**2).sum(axis=1) * (br_c**2).sum(axis=1))
        with np.errstate(invalid="ignore", divide="ignore"):
            boots = np.where(d2 > 0, num / d2, 0.0)
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


def _rankdata(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    sx = x[order]
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and sx[j + 1] == sx[i]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        ranks[order[i : j + 1]] = avg_rank
        i = j + 1
    return ranks


def ic_decay_curve(
    *,
    price_book: PriceBook,
    benchmark_nav: dict[dt.date, float],
    events: list[dict],
    horizons: tuple[int, ...] = (1, 2, 3, 5),
    seed: int = 0,
) -> list[dict]:
    """IC 衰减：不同持有期的 IC（Q2：细粒度信号是否衰减更快）。"""
    out: list[dict] = []
    for h in horizons:
        obs = build_observations(
            price_book=price_book,
            benchmark_nav=benchmark_nav,
            events=events,
            horizon=h,
        )
        ic = spearman_ic([o.signal for o in obs], [o.forward_ret for o in obs], seed=seed)
        out.append({"horizon": h, **ic})
    return out


def compare_signals_ic(
    obs_a: list[EventObservation],
    obs_b: list[EventObservation],
    *,
    n_boot: int = 2000,
    seed: int = 0,
) -> dict:
    """配对样本的 IC 差检验（H1 判定核心）。

    要求两个信号基于同一事件集（event_key 对齐）。bootstrap 重抽事件，
    计算 IC_a - IC_b 的分布与 95% CI；CI 下界 > 0 支持 H1。

    向量化（阶段 11）：与旧实现逐位一致——旧实现对每次重抽的原始信号/收益值
    重新求秩（不是重抽秩），且每个 bootstrap IC 先 round(·,6) 再相减；
    两者都在向量化实现中保留（_rankdata_2d + Python round 语义）。
    """
    map_a = {o.event_key: o for o in obs_a}
    map_b = {o.event_key: o for o in obs_b}
    common = sorted(set(map_a) & set(map_b))
    if len(common) < 3:
        return {"n": len(common), "delta_ic": None, "ci95_low": None, "ci95_high": None}
    n = len(common)
    sig_a = np.array([map_a[k].signal for k in common], dtype=float)
    ret_a = np.array([map_a[k].forward_ret for k in common], dtype=float)
    sig_b = np.array([map_b[k].signal for k in common], dtype=float)
    ret_b = np.array([map_b[k].forward_ret for k in common], dtype=float)

    point = _pearson_rounded(_rankdata(sig_a), _rankdata(ret_a)) - _pearson_rounded(
        _rankdata(sig_b), _rankdata(ret_b)
    )

    rng = np.random.default_rng(seed)
    deltas = np.empty(n_boot)
    if n_boot > 0:
        idx = rng.integers(0, n, size=(n_boot, n))
        deltas = _bootstrap_ic_rows(sig_a, ret_a, idx) - _bootstrap_ic_rows(sig_b, ret_b, idx)
    ci_low, ci_high = np.percentile(deltas, [2.5, 97.5])
    return {
        "n": n,
        "delta_ic": round(float(point), 6),
        "ci95_low": round(float(ci_low), 6),
        "ci95_high": round(float(ci_high), 6),
        "h1_supported": bool(ci_low > 0),
    }


def _pearson_rounded(x: np.ndarray, y: np.ndarray) -> float:
    """秩向量的 Pearson 相关，round 到 6 位——与旧 _spearman_point 返回值一致。

    零方差（全并列）时旧实现经 spearman_ic 返回 round(nan,6)=nan 并传播为
    manifest 的 None（如分类型消融的并列组），不得改为 0。
    """
    xc = x - x.mean()
    yc = y - y.mean()
    denom = math.sqrt(float((xc**2).sum()) * float((yc**2).sum()))
    ic = float((xc * yc).sum() / denom) if denom > 0 else float("nan")
    return round(ic, 6)


def _bootstrap_ic_rows(sig: np.ndarray, ret: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """对 (n_boot, n) 重抽索引逐行计算 round(·,6) 后的 Spearman IC。

    与旧实现的逐次 `_spearman_point` 逐位一致：对重抽后的**原始值**重求秩
    （含并列平均秩），Pearson 后逐元素 Python round 半偶舍入。
    """
    boots = np.empty(idx.shape[0])
    if idx.shape[0] == 0:
        return boots
    bs = sig[idx]
    br = ret[idx]
    rs = _rankdata_2d(bs)
    rr = _rankdata_2d(br)
    rs_c = rs - rs.mean(axis=1, keepdims=True)
    rr_c = rr - rr.mean(axis=1, keepdims=True)
    num = (rs_c * rr_c).sum(axis=1)
    d2 = np.sqrt((rs_c**2).sum(axis=1) * (rr_c**2).sum(axis=1))
    # 全并列（d2==0）的行：旧实现经 spearman_ic 得 round(nan,6)=nan，
    # nan 传播进 deltas 与分位数——保持一致，不得填 0。
    raw = np.full(idx.shape[0], np.nan)
    valid = d2 > 0
    raw[valid] = num[valid] / d2[valid]
    # 旧实现逐个 round(float(x), 6)（Python 语义），np.round 在二进制半值边界
    # 与其不同，这里逐元素复刻；数组规模 n_boot，开销可忽略。
    for i, v in enumerate(raw):
        boots[i] = round(float(v), 6)
    return boots


def _rankdata_2d(x: np.ndarray) -> np.ndarray:
    """逐行平均秩（并列取平均），与逐行调用 _rankdata 逐位一致。"""
    n = x.shape[1]
    order = np.argsort(x, axis=1, kind="mergesort")
    sx = np.take_along_axis(x, order, axis=1)
    starts = np.ones_like(sx, dtype=bool)
    starts[:, 1:] = sx[:, 1:] != sx[:, :-1]
    idxs = np.arange(n)
    # 每位置的并列组 [s, e]（排序后下标）：平均秩 = (s + e)/2 + 1
    s_idx = np.maximum.accumulate(np.where(starts, idxs[None, :], -1), axis=1)
    shifted = np.empty_like(starts)
    shifted[:, :-1] = starts[:, 1:]
    shifted[:, -1] = False
    e_idx = np.minimum.accumulate(np.where(shifted, idxs[None, :], n - 1)[:, ::-1], axis=1)[:, ::-1]
    avg = (s_idx + e_idx) / 2.0 + 1.0
    ranks = np.empty_like(avg)
    np.put_along_axis(ranks, order, avg, axis=1)
    return ranks
