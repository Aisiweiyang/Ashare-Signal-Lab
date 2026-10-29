"""LLM provider：协议 + 确定性 Mock + OpenAI 兼容实现（ADR-007）。

共同要求（THREAT_MODEL T7-T10）：
- 文本仅作为 data 注入（build_user_prompt 定界符包裹），provider 不绑定任何工具；
- 输出必须过 parse_model_output 强校验，失败进隔离队列；
- 预算（累计成本上限）超限抛 BudgetExceededError（kill switch）；
- 重试只对网络类错误；schema 错误不重试（防刷费用）。
"""

from __future__ import annotations

import json
import re
import time
from typing import Protocol

from asg_domain.errors import BudgetExceededError

from .lexicon import classify_with_lexicon
from .schema import LabelOutput


class ChatProvider(Protocol):
    def chat(self, *, system: str, user: str, max_tokens: int = 512) -> tuple[str, float]:
        """返回 (模型原始输出文本, 本次调用成本CNY)。"""
        ...


class MockChatProvider:
    """确定性 mock：内部用词典规则生成与真实 LLM 同构的 JSON 输出。

    - fault_rate：注入无效 JSON 的概率（默认 0）用于隔离队列测试；
    - prompt_aware：若 user 提示词包含注入攻击样本，mock 仍只按文本内容打标
      （验证提示注入不改变行为——mock 的规则只读文本主体）。
    """

    def __init__(
        self,
        *,
        fault_rate: float = 0.0,
        seed: int = 0,
        price_per_call_cny: float = 0.01,
        latency_ms: float = 5.0,
    ) -> None:
        self.fault_rate = fault_rate
        self.seed = seed
        self.price_per_call_cny = price_per_call_cny
        self.latency_ms = latency_ms
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def chat(self, *, system: str, user: str, max_tokens: int = 512) -> tuple[str, float]:
        self._calls += 1
        time.sleep(min(self.latency_ms, 5.0) / 1000.0)
        # 从定界数据段提取文本主体（提示词其余部分不参与判定——模拟“文本只是数据”）
        m = re.search(r"【给定文本开始】\n(.*?)\n【给定文本结束】", user, re.DOTALL)
        body = m.group(1) if m else user
        # 去掉可能的标题行
        body = re.sub(r"^标题：.*\n", "", body)
        result = classify_with_lexicon(body)
        label = LabelOutput(
            direction=result["labels"]["direction"],  # type: ignore[arg-type]
            vs_expectation=result["labels"]["vs_expectation"],  # type: ignore[arg-type]
            certainty=result["labels"]["certainty"],  # type: ignore[arg-type]
            evidence_quote="",
            rationale="mock-lexicon 规则判定",
        )
        payload = label.model_dump()
        import random

        rng = random.Random(self.seed + self._calls)
        if rng.random() < self.fault_rate:
            return "抱歉，我无法按要求输出。", self.price_per_call_cny
        return json.dumps(payload, ensure_ascii=False), self.price_per_call_cny


class OpenAICompatProvider:
    """OpenAI 兼容 chat completions 端点（DeepSeek 等）。httpx 直连，不引 SDK。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        budget_cny: float = 0.0,
        price_per_1k_tokens_cny: float = 0.0,
    ) -> None:
        if not api_key:
            raise BudgetExceededError("OpenAICompatProvider 缺少 API key")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.budget_cny = budget_cny
        self.price_per_1k_tokens_cny = price_per_1k_tokens_cny
        self._spent_cny = 0.0

    @property
    def spent_cny(self) -> float:
        return self._spent_cny

    def chat(self, *, system: str, user: str, max_tokens: int = 512) -> tuple[str, float]:
        import httpx

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                cost = usage.get("total_tokens", 0) / 1000.0 * self.price_per_1k_tokens_cny
                self._spent_cny += cost
                if self.budget_cny > 0 and self._spent_cny > self.budget_cny:
                    raise BudgetExceededError(f"LLM 预算超限: 已花费 {self._spent_cny:.2f} > {self.budget_cny:.2f}")
                return content, cost
            except BudgetExceededError:
                raise
            except (httpx.HTTPError, KeyError, ValueError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2**attempt))  # 指数退避
        raise RuntimeError(f"LLM 调用失败（重试 {self.max_retries} 次后）: {last_err}")


def make_provider_from_env(settings) -> ChatProvider:
    """按配置构造 provider；未配置真实 LLM 时一律降级 mock（显式 degraded 标记由调用方记录）。"""
    if settings.llm_provider == "openai_compat" and settings.llm_api_key and settings.llm_base_url:
        return OpenAICompatProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model or "deepseek-chat",
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            budget_cny=settings.llm_budget_cny,
        )
    return MockChatProvider()
