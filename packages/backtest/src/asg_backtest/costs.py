"""交易成本模型（口径见 RESEARCH_PROTOCOL §6）。"""

from __future__ import annotations

from dataclasses import dataclass

from asg_domain.constants import DEFAULT_COSTS


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = DEFAULT_COSTS["commission_bps"]
    stamp_duty_bps: float = DEFAULT_COSTS["stamp_duty_bps"]
    slippage_bps: float = DEFAULT_COSTS["slippage_bps"]
    min_commission_cny: float = DEFAULT_COSTS["min_commission_cny"]

    @classmethod
    def from_config(cls, cfg: dict | None) -> CostModel:
        base = DEFAULT_COSTS
        cfg = cfg or {}
        return cls(
            commission_bps=float(cfg.get("commission_bps", base["commission_bps"])),
            stamp_duty_bps=float(cfg.get("stamp_duty_bps", base["stamp_duty_bps"])),
            slippage_bps=float(cfg.get("slippage_bps", base["slippage_bps"])),
            min_commission_cny=float(cfg.get("min_commission_cny", base["min_commission_cny"])),
        )

    def buy_cost(self, amount: float) -> dict[str, float]:
        """买入侧：佣金 + 滑点（滑点按价格恶化计，不以现金收取——回测里以成交价上移实现；
        此处返回现金口径的佣金与滑点近似）。"""
        commission = max(amount * self.commission_bps / 10000.0, self.min_commission_cny if amount > 0 else 0.0)
        slippage = amount * self.slippage_bps / 10000.0
        return {"commission": round(commission, 2), "stamp_duty": 0.0, "slippage": round(slippage, 2), "total": round(commission + slippage, 2)}

    def sell_cost(self, amount: float) -> dict[str, float]:
        """卖出侧：佣金 + 印花税 + 滑点。"""
        commission = max(amount * self.commission_bps / 10000.0, self.min_commission_cny if amount > 0 else 0.0)
        stamp = amount * self.stamp_duty_bps / 10000.0
        slippage = amount * self.slippage_bps / 10000.0
        return {"commission": round(commission, 2), "stamp_duty": round(stamp, 2), "slippage": round(slippage, 2), "total": round(commission + stamp + slippage, 2)}

    def effective_entry_price(self, price: float) -> float:
        """买入成交价（滑点上移）。"""
        return round(price * (1.0 + self.slippage_bps / 10000.0), 4)

    def effective_exit_price(self, price: float) -> float:
        """卖出成交价（滑点下移）。"""
        return round(price * (1.0 - self.slippage_bps / 10000.0), 4)


def sensitivity_grid(slippage_bps_options: tuple[float, ...] = (5.0, 10.0, 20.0, 50.0)) -> list[CostModel]:
    """成本敏感性网格（Q3）。"""
    return [CostModel(slippage_bps=s) for s in slippage_bps_options]
