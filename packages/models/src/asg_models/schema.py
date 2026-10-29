"""结构化输出契约：所有 provider 的预测必须通过该 schema 强校验。

校验失败 → QuarantinedOutputError / schema_valid=False（隔离区），永不静默填默认值。
"""

from __future__ import annotations

import json
import re

from asg_domain.enums import Certainty, Direction, EventType, TargetType, VsExpectation
from asg_domain.errors import QuarantinedOutputError
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LabelOutput(BaseModel):
    """LLM/模型输出的唯一合法形态。"""

    model_config = ConfigDict(extra="forbid")

    direction: Direction
    vs_expectation: VsExpectation
    certainty: Certainty
    target_type: TargetType = TargetType.COMPANY
    event_type: EventType = EventType.OTHER
    evidence_quote: str = Field(default="", max_length=200)
    rationale: str = Field(default="", max_length=300)

    @field_validator("evidence_quote", "rationale")
    @classmethod
    def _no_control_chars(cls, v: str) -> str:
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v)

    def probabilities_view(self) -> dict[str, str]:
        # 单点输出无概率分布；由 provider 外层补充（如 mock 的词频得分 softmax）
        return {}

    def to_labels(self) -> dict[str, str]:
        return {
            "direction": self.direction.value,
            "vs_expectation": self.vs_expectation.value,
            "certainty": self.certainty.value,
            "target_type": self.target_type.value,
            "event_type": self.event_type.value,
        }


def parse_model_output(raw: str) -> LabelOutput:
    """从原始输出解析 LabelOutput：提取 JSON 块 → pydantic 强校验。

    任何失败抛 QuarantinedOutputError（调用方负责落隔离区）。
    """
    if not raw or not raw.strip():
        raise QuarantinedOutputError("空输出")
    # 容忍 markdown 代码块包裹
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise QuarantinedOutputError("输出中未找到 JSON 对象")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise QuarantinedOutputError(f"JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise QuarantinedOutputError("JSON 顶层必须是对象")
    try:
        return LabelOutput.model_validate(data)
    except Exception as e:  # pydantic ValidationError → 隔离
        raise QuarantinedOutputError(f"schema 校验失败: {e}") from e


OUTPUT_JSON_INSTRUCTION = (
    "只输出一个 JSON 对象，不要输出任何其他文字。字段：\n"
    'direction: "bullish"|"bearish"|"neutral"|"unknown"；\n'
    'vs_expectation: "above"|"inline"|"below"|"no_expectation"|"unknown"；\n'
    'certainty: "certain"|"speculative"|"conditional"|"unknown"；\n'
    'target_type: "company"|"security"|"industry"|"index_macro"|"other"；\n'
    'event_type: "earnings"|"orders"|"ma"|"regulatory"|"shareholder"|"product"|"litigation"|"macro_policy"|"other"；\n'
    'evidence_quote: 支撑判断的原文片段(≤100字)；rationale: ≤120字理由。\n'
)

SYSTEM_PROMPT = (
    "你是中文 A 股市场的金融文本情绪标注助手。你必须只依据【给定文本】本身进行判断，"
    "不得使用你记忆中的任何公司后续走势、后续公告或市场行情（你没有未来知识，也不允许预测未来）。\n"
    "标签定义遵循 LABELING_GUIDE v1.0。" + OUTPUT_JSON_INSTRUCTION
)


def build_user_prompt(text: str, title: str = "") -> str:
    """用户提示词：文本仅作为数据注入，用定界符包裹（防提示注入改变指令）。"""
    safe_text = text.replace("【给定文本】", "[文本]")
    return (
        "请对下面【给定文本】开始标注。\n"
        "【给定文本开始】\n"
        + (f"标题：{title}\n" if title else "")
        + safe_text
        + "\n【给定文本结束】\n"
        + OUTPUT_JSON_INSTRUCTION
    )
