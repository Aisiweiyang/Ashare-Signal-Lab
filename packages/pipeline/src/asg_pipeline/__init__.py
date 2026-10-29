"""asg_pipeline：端到端研究流水线（摄取/对齐/金标/预测/实验编排）。"""

from .demo import seed_demo
from .experiments import ExperimentConfig, run_full_experiment
from .ingest import availability_for, ingest_documents, strict_eligible, upsert_market_data

__all__ = [
    "ExperimentConfig",
    "availability_for",
    "ingest_documents",
    "run_full_experiment",
    "seed_demo",
    "strict_eligible",
    "upsert_market_data",
]
