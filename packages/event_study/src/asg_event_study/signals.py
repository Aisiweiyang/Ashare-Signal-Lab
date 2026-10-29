"""细粒度信号构建（预注册公式，常数来自 asg_domain.constants）。

signal = DIR_SIGN[direction] * VS_MULT[vs_expectation] * CERT_MULT[certainty]

三分类对照信号：sign(direction)。多数类对照：0。
每条 (文本→信号) 携带 available_at；聚合到 (security, decision_day) 时取当日
「可得信号」的等权平均，且要求每条满足 available_at <= decision_time（严格门控）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from asg_domain.constants import CERT_MULT, DIR_SIGN, VS_MULT
from asg_domain.errors import AvailabilityError


@dataclass(frozen=True)
class TextSignal:
    market_text_id: str
    security_id: str | None
    available_at: dt.datetime | None
    labels: dict[str, str]
    signal: float


def label_to_signal(labels: dict[str, str], *, kind: str = "fine_grained") -> float:
    if kind == "fine_grained":
        return round(
            DIR_SIGN.get(labels.get("direction", "unknown"), 0.0)
            * VS_MULT.get(labels.get("vs_expectation", "unknown"), 0.0)
            * CERT_MULT.get(labels.get("certainty", "unknown"), 0.0),
            6,
        )
    if kind == "direction_3class":
        return DIR_SIGN.get(labels.get("direction", "unknown"), 0.0)
    if kind == "dummy_majority":
        return 0.0
    raise ValueError(f"未知信号类型: {kind}")


def aggregate_daily_signal(
    text_signals: list[TextSignal],
    *,
    decision_day: dt.date,
    decision_time: dt.datetime,
    security_id: str,
    strict: bool = True,
) -> float | None:
    """聚合某证券某决策日的信号（等权平均）。

    strict=True：任何一条 available_at 缺失或晚于 decision_time 都会抛
    AvailabilityError（绝不静默丢弃——丢弃会引入幸存者偏差）。
    strict=False 仅用于诊断统计，结果标记 exploratory（不得进结论）。
    """
    vals: list[float] = []
    for ts in text_signals:
        if ts.security_id != security_id:
            continue
        if strict:
            if ts.available_at is None:
                raise AvailabilityError(f"文本 {ts.market_text_id} 缺 available_at，严格模式拒绝")
            if ts.available_at > decision_time:
                raise AvailabilityError(f"文本 {ts.market_text_id} 在决策时点后可得（{ts.available_at} > {decision_time}）")
        vals.append(ts.signal)
    if not vals:
        return None
    return round(sum(vals) / len(vals), 6)


def signal_name(kind: str) -> str:
    return {
        "fine_grained": "细粒度信号(方向×预期×确定性)",
        "direction_3class": "三分类方向信号",
        "dummy_majority": "多数类对照信号",
    }.get(kind, kind)
