"""asg_models：词典/sklearn 基线、LLM provider、结构化输出与预测 runner。"""

from .baselines import (
    MajorityBaseline,
    SklearnFineGrainedBaseline,
    direction_only_metrics,
    fine_grained_metrics,
)
from .bilingual import analyze_bilingual
from .bilingual_baseline import BilingualFineGrainedBaseline
from .lexicon import classify_with_lexicon, lexicon_scores
from .providers import (
    ChatProvider,
    MockChatProvider,
    OpenAICompatProvider,
    make_provider_from_env,
)
from .runner import TextItem, prediction_metrics_summary, predictions_of_run, run_prediction
from .schema import SYSTEM_PROMPT, LabelOutput, build_user_prompt, parse_model_output

__all__ = [
    "SYSTEM_PROMPT",
    "BilingualFineGrainedBaseline",
    "ChatProvider",
    "LabelOutput",
    "MajorityBaseline",
    "MockChatProvider",
    "OpenAICompatProvider",
    "SklearnFineGrainedBaseline",
    "TextItem",
    "analyze_bilingual",
    "build_user_prompt",
    "classify_with_lexicon",
    "direction_only_metrics",
    "fine_grained_metrics",
    "lexicon_scores",
    "make_provider_from_env",
    "parse_model_output",
    "prediction_metrics_summary",
    "predictions_of_run",
    "run_prediction",
]
