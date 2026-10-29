"""预注册信号常数（RESEARCH_PROTOCOL §6 v1.0 冻结）。

这是「细粒度信号构建」与「合成数据反应注入」共用的唯一事实源：
- 信号构建（event_study）用它把三维标签映射为数值信号；
- 合成行情（connectors.market）用它注入真实反应，保证 H1 可被诚实检验。

修改这些值 = 修改研究协议，必须在 RESEARCH_PROTOCOL.md Change Log 登记。
"""

from __future__ import annotations

VS_MULT: dict[str, float] = {
    "above": 1.2,
    "below": 1.2,
    "inline": 0.5,
    "no_expectation": 0.7,
    "unknown": 0.0,
}

CERT_MULT: dict[str, float] = {
    "certain": 1.0,
    "conditional": 0.75,
    "speculative": 0.45,
    "unknown": 0.0,
}

DIR_SIGN: dict[str, float] = {
    "bullish": 1.0,
    "bearish": -1.0,
    "neutral": 0.0,
    "unknown": 0.0,
}

# 回测成本默认参数（可被配置覆盖；口径见 RESEARCH_PROTOCOL §6）
DEFAULT_COSTS = {
    "commission_bps": 2.5,      # 佣金（双边，万2.5）
    "stamp_duty_bps": 5.0,      # 印花税（仅卖出，2023-08 减半后口径 千0.5）
    "slippage_bps": 10.0,       # 滑点（默认10bp；敏感性 5/10/20/50）
    "min_commission_cny": 5.0,  # 最低佣金
}

LIMIT_PRICE_EPS = 1e-6  # 涨跌停可成交性判断容差
