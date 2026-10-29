"""运行 research/configs 下的实验配置。

用法: uv run python workers/run_experiment.py research/configs/exp_fine_vs_3class_v1.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asg_domain.db import init_db, make_engine, make_session_factory  # noqa: E402
from asg_api.settings import get_settings  # noqa: E402
from asg_pipeline import ExperimentConfig, run_full_experiment  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: run_experiment.py <config.yaml>")
        return 2
    cfg = ExperimentConfig.from_yaml(sys.argv[1])
    engine = make_engine(get_settings().database_url)
    init_db(engine)
    session = make_session_factory(engine)()
    manifest = run_full_experiment(session, cfg)
    print(f"[完成] run_id={manifest['run_id']}")
    print(f"  金标(严格)={manifest['n_gold_strict']}  样本外={manifest['n_oos_events']}")
    print(f"  IC 金标: {manifest['ic_gold_fine_vs_dir3']}")
    print(f"  IC 预测: {manifest['ic_pred_fine_vs_dir3']}")
    print(f"  H1: {manifest['h1_verdict']}")
    print(f"  回测: {manifest['backtest']['metrics']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
