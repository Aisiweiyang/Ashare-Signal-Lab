"""akshare 日行情适配器（可选依赖，默认停用；许可证结论见 docs/OSS_EVALUATION.md）。

上游项目：akfamily/akshare（MIT）。许可证核验结论（2026-09-07）：代码 MIT，
但其抓取的上游站点数据许可各不相同——因此本适配器：
- 仅作为**本地研究**的行情读取通道，不缓存、不再分发、不随系统打包上游数据；
- 默认停用（ConnectorMeta.enabled=False，且创建实例时须显式 enabled=True 或
  环境变量 ASG_AKSHARE_ENABLED=true，双重确认）；
- akshare 未安装 → ConnectorUnavailableError，由调用方降级到合成/fixture 路径
  （`uv sync --extra market` 安装）；
- 接口为 `stock_zh_a_hist`（日频，前复权可选）。akshare 各版本列名有过变更，
  这里按「中文列名 → 字段」映射并容忍缺失次要列；核心列缺失时如实报错。

涨跌停口径：主板 ±10%（`limit_pct` 可调）。创业板/科创板 ±20%、ST ±5% 等板块差异
需调用方按证券属性传入对应 `limit_pct`——真实板块元数据属生产轨工作，此处不虚构。
"""

from __future__ import annotations

import datetime as _dt
import importlib
import os
import time
from typing import Any

from asg_domain.errors import ConnectorUnavailableError

from .base import ConnectorMeta, MarketBar

SOURCE_ID = "akshare_hist_daily"

# akshare stock_zh_a_hist 的中文列名 → 内部字段（核心列缺一不可）
_CORE_COLUMNS = {
    "日期": "trade_date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
}
_OPTIONAL_COLUMNS = {"成交量": "volume", "成交额": "amount"}


def _env_enabled() -> bool:
    return os.environ.get("ASG_AKSHARE_ENABLED", "").strip().lower() in {"1", "true", "yes"}


class AkshareDailyBarsConnector:
    """MarketConnector 的 akshare 实现（单 symbol 拉取，进程内限速）。"""

    meta = ConnectorMeta(
        source_id=SOURCE_ID,
        display_name="akshare A 股日行情（可选，默认停用）",
        allowed_use="仅限本地研究；上游数据许可由 akshare 及其上游站点声明，不得再分发",
        rate_limit_per_minute=30,
        enabled=False,  # 默认停用；实例按 enabled 参数/环境变量决定可用性
    )

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        limit_pct: float = 0.10,
        adjust: str = "qfq",
        min_interval_seconds: float = 2.0,
    ) -> None:
        self.meta.enabled = _env_enabled() if enabled is None else enabled
        self.limit_pct = limit_pct
        self.adjust = adjust
        self._min_interval = min_interval_seconds
        self._last_call = 0.0
        self._ak: Any = None

    # —— 依赖与限速 ——
    def _load_akshare(self) -> Any:
        if self._ak is None:
            try:
                self._ak = importlib.import_module("akshare")
            except ImportError as e:
                raise ConnectorUnavailableError(
                    "akshare 未安装；可选安装：uv sync --extra market（许可证见 docs/OSS_EVALUATION.md）"
                ) from e
        return self._ak

    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    # —— 主接口 ——
    def fetch_price_bars(self, symbol: str, *, start: _dt.date, end: _dt.date) -> list[MarketBar]:
        if not self.meta.enabled:
            raise ConnectorUnavailableError(
                "akshare 适配器默认停用；显式启用需 AkshareDailyBarsConnector(enabled=True) "
                "或环境变量 ASG_AKSHARE_ENABLED=true（合规确认后）"
            )
        if start > end:
            raise ValueError(f"日期区间非法: {start} > {end}")
        ak = self._load_akshare()
        self._throttle()
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=self.adjust,
        )
        return self.to_market_bars(symbol, df)

    # —— 纯函数归一化（独立出来便于无网络测试） ——
    def to_market_bars(self, symbol: str, df: Any) -> list[MarketBar]:
        """akshare DataFrame → MarketBar 列表。核心列缺失抛 ConnectorError。"""
        from asg_domain.errors import ConnectorError

        if df is None or len(df) == 0:
            return []
        cols = set(df.columns)
        missing = [c for c in _CORE_COLUMNS if c not in cols]
        if missing:
            raise ConnectorError(
                f"akshare 返回缺少核心列 {missing}（版本接口变更？）；现有列: {sorted(cols)}"
            )
        rows = df.to_dict("records")
        bars: list[MarketBar] = []
        prev_close: float | None = None
        for r in rows:
            close = float(r["收盘"])
            if prev_close is None:
                # 首行无前收：用涨跌额反推，缺列则该日 pre_close 记为当日收盘（并标记）
                change = float(r.get("涨跌额", 0.0) or 0.0)
                pre = close - change
            else:
                pre = prev_close
            volume = float(r.get("成交量", 0.0) or 0.0)
            amount = float(r.get("成交额", 0.0) or 0.0)
            trade_date = _dt.date.fromisoformat(str(r["日期"])[:10])
            limit_up = round(pre * (1.0 + self.limit_pct), 2)
            limit_down = round(pre * (1.0 - self.limit_pct), 2)
            bars.append(
                MarketBar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=round(float(r["开盘"]), 2),
                    high=round(float(r["最高"]), 2),
                    low=round(float(r["最低"]), 2),
                    close=round(close, 2),
                    pre_close=round(pre, 2),
                    volume=volume,
                    amount=amount,
                    suspended=volume <= 0.0,
                    limit_up=limit_up,
                    limit_down=limit_down,
                )
            )
            prev_close = close
        return bars
