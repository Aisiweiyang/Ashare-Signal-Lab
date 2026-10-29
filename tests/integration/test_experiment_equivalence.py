"""性能优化等价性测试（阶段 11）：优化不得改变任何研究指标。

三道防线：
1. 跨进程确定性（ADR-013）：不同 PYTHONHASHSEED 的两个子进程同 seed 全流程运行，
   manifest 研究指标逐位一致——守护「同 seed + 同代码逐位可复现」一等约束
   （历史缺陷：set 迭代顺序 + uuid4 主键曾使金标内容随进程漂移，见 TEST_REPORT §14）；
2. v1 快速基线：与 tests/integration/baselines/eq_baseline_v1.json（确定性修复后
   固化）逐位一致，作为优化迭代的高速回归防线；
3. v3 金标准：与只读金标准 tests/integration/baselines/eq_baseline_v3.json 在「跨抽样顺序
   稳定」的字段上逐一相等（规模/结构/三条件判定），H1 结论（金标口径获支持、
   预测口径不支持）不得翻转。金标准的 IC 小数位是其运行进程随机标注顺序下的
   一次抽样（TEST_REPORT §14 新旧对照），故不做小数级比较——该缺陷由防线 1
   阻断，今后任何漂移都会被防线 1/2 捕获。

运行纪律（ADR-012）：全部使用测试临时全新库，manifest 落 tmp_path，
不触碰 data/ 与只追加的 research/runs/。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from asg_pipeline import ExperimentConfig, run_full_experiment

pytestmark = pytest.mark.integration

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PINNED_V1 = Path(__file__).resolve().parent / "baselines" / "eq_baseline_v1.json"
_GOLDEN_V3 = Path(__file__).resolve().parent / "baselines" / "eq_baseline_v3.json"

# 随运行实例变化/口径变更的字段：豁免逐位比较。
# - dataset_hash：固化基线文件在 ADR-014 口径收口（剔除 uuid4 主键）之前固化，
#   其值为旧口径——基线是只读锚点不回写，故对本文件内的基线比较豁免；
#   新口径的「同 seed 两次运行 dataset_hash 相同」验收由下方跨进程测试承担。
# - 其余为 id/时间戳/耗时。
_VOLATILE = {
    "run_id",
    "started_at",
    "finished_at",
    "env",
    "timings_seconds",
    "dataset_hash",
    "task_id",
}


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in _VOLATILE}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _assert_manifest_equal(got: dict, want: dict, *, label: str) -> None:
    a, b = _strip_volatile(got), _strip_volatile(want)
    assert a.keys() == b.keys(), f"[{label}] manifest 键集不一致: {a.keys() ^ b.keys()}"
    for k in b:
        assert a[k] == b[k], f"[{label}] 指标发生变化: {k}\n  基线: {b[k]!r}\n  当前: {a[k]!r}"


def _v1_config() -> ExperimentConfig:
    # 与 tests/integration/baselines/eq_baseline_v1.json 固化时的配置逐字段一致
    return ExperimentConfig(
        name="eq_baseline_v1",
        seed=20260907,
        n_events=130,
        n_days=180,
        alpha=0.012,
        provider="mock",
        folds=3,
        embargo_days=2,
        horizons=(1, 2),
        event_type_ablation=True,
        split_scheme="walk_forward",
    )


_SUBPROCESS_RUNNER = """
import json, sys, tempfile
from pathlib import Path
from asg_domain.db import make_engine, init_db, make_session_factory
from asg_pipeline import ExperimentConfig, run_full_experiment

out = Path(sys.argv[1])
tmp = Path(tempfile.mkdtemp(prefix="eqx-", dir=str(out.parent)))
engine = make_engine(f"sqlite:///{(tmp / 'eq.db').as_posix()}")
init_db(engine)
session = make_session_factory(engine)()
cfg = ExperimentConfig(
    name="eq_cross_process", seed=20260907, n_events=130, n_days=180, alpha=0.012,
    provider="mock", folds=3, embargo_days=2, horizons=(1, 2),
    event_type_ablation=True, split_scheme="walk_forward",
)
manifest = run_full_experiment(session, cfg, runs_dir=tmp)
for key in ("run_id", "started_at", "finished_at", "env", "timings_seconds"):
    manifest.pop(key, None)
# dataset_hash 保留在比较集内：ADR-014 口径收口后它是内容血缘哈希，
# 「同 seed 跨进程相同」本身是本测试的验收项之一（不再是已知残留）。
manifest.get("backtest", {}).pop("run_id", None)
manifest["demo_stats"].pop("task_id", None)
out.write_text(json.dumps(manifest, ensure_ascii=False, default=str), encoding="utf-8")
"""


def test_cross_process_bitwise_determinism(tmp_path):
    """不同 PYTHONHASHSEED 子进程：研究指标逐位一致（ADR-013 回归防线）；
    dataset_hash（收口后的内容血缘哈希）同样逐位一致（ADR-014 验收）。"""
    runner = tmp_path / "_eq_runner.py"
    runner.write_text(_SUBPROCESS_RUNNER, encoding="utf-8")
    env_base = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
    outs = []
    for i, seed in enumerate(("1", "42")):
        out = tmp_path / f"manifest_{i}.json"
        subprocess.run(
            [sys.executable, str(runner), str(out)],
            check=True,
            cwd=str(_PROJECT_ROOT),
            env={**env_base, "PYTHONHASHSEED": seed},
            timeout=600,
        )
        outs.append(json.loads(out.read_text(encoding="utf-8")))
    assert outs[0] == outs[1], "同 seed 在不同进程哈希种子下指标不一致（违反逐位可复现）"
    # ADR-014 验收：dataset_hash 不再随运行实例漂移（旧口径两次运行必不同）
    assert outs[0]["dataset_hash"] == outs[1]["dataset_hash"], "dataset_hash 跨进程不一致（口径收口失效）"
    # 关键数字兜底
    assert outs[0]["n_gold_strict"] == 98
    assert outs[0]["ic_gold_fine_vs_dir3"]["delta_ic"] == 0.024248


def test_v1_config_research_metrics_bitwise_stable(db, tmp_path):
    """快速防线：v1 配置全链路指标与确定性基线逐位一致。"""
    baseline = json.loads(_PINNED_V1.read_text(encoding="utf-8"))
    manifest = run_full_experiment(db, _v1_config(), runs_dir=tmp_path)
    _assert_manifest_equal(manifest, baseline, label="v1 pinned baseline")
    assert manifest["n_gold_strict"] == 98
    assert manifest["n_oos_events"] == 72


def test_v3_golden_stable_fields_and_verdict(db, tmp_path):
    """金标准防线：v3 配置（800 事件）相对只读金标准 exp_2639af7d5b6d.json 的
    顺序稳定字段逐一相等；H1 结论不得翻转。"""
    assert _GOLDEN_V3.exists(), f"只读金标准 manifest 缺失: {_GOLDEN_V3}"
    golden = json.loads(_GOLDEN_V3.read_text(encoding="utf-8"))
    cfg = ExperimentConfig.from_yaml(
        _PROJECT_ROOT / "research" / "configs" / "exp_fine_vs_3class_v3_scale800.yaml"
    )
    manifest = run_full_experiment(db, cfg, runs_dir=tmp_path)

    # ADR-012 验收检查：全新临时库，行情确由本次运行生成
    assert manifest["demo_stats"]["market_data"] == {"n_new_securities": 0, "n_new_bars": 1440, "n_days": 180}

    # 顺序稳定字段：与金标准逐一相等
    assert manifest["config_hash"] == golden["config_hash"]
    assert manifest["seed"] == golden["seed"]
    assert manifest["name"] == golden["name"]
    assert manifest["split_scheme"] == golden["split_scheme"]
    assert manifest["folds_used"] == golden["folds_used"]
    assert manifest["n_gold_strict"] == golden["n_gold_strict"] == 604
    assert manifest["n_oos_events"] == golden["n_oos_events"] == 431
    assert manifest["ic_gold_fine_vs_dir3"]["n"] == golden["ic_gold_fine_vs_dir3"]["n"] == 597
    assert manifest["ic_pred_fine_vs_dir3"]["n"] == golden["ic_pred_fine_vs_dir3"]["n"] == 597
    assert manifest["provider_note"] == golden["provider_note"]
    assert manifest["demo_stats"]["ingest"] == golden["demo_stats"]["ingest"]
    assert manifest["demo_stats"]["n_gold"] == golden["demo_stats"]["n_gold"]
    assert manifest["demo_stats"]["n_adjudicated"] == golden["demo_stats"]["n_adjudicated"]
    assert manifest["demo_stats"]["n_canonical"] == golden["demo_stats"]["n_canonical"]
    assert manifest["demo_stats"]["generator_version"] == golden["demo_stats"]["generator_version"]

    et_g = golden["ic_by_event_type_gold_exploratory"]
    et_m = manifest["ic_by_event_type_gold_exploratory"]
    assert set(et_m) == set(et_g)
    assert {k: v["n"] for k, v in et_m.items()} == {k: v["n"] for k, v in et_g.items()}

    assert list(manifest["model_scores"]) == list(golden["model_scores"])
    for model, scores in golden["model_scores"].items():
        assert manifest["model_scores"][model]["n_oos"] == scores["n_oos"]

    # H1 结论不得翻转（协议 §5 三条件的判定结果，而非某次抽样的具体小数）
    assert manifest["h1_verdict"] == golden["h1_verdict"]
    assert manifest["h1_verdict"]["supported_on_gold"] is True
    assert manifest["h1_verdict"]["supported_on_predictions"] is False
    assert manifest["ic_gold_fine_vs_dir3"]["ci95_low"] > 0  # 条件 1
    assert manifest["ic_pred_fine_vs_dir3"]["ci95_low"] <= 0
    assert len(manifest["ic_by_fold_gold"]) == len(golden["ic_by_fold_gold"]) == 3
    assert all(
        f["ic_fine"] > f["ic_dir3"] for f in manifest["ic_by_fold_gold"]
    ), "条件 3（3/3 折细粒度更高）不得翻转"

    # 抽样敏感小数与金标准的差异幅度必须微小（顺序重抽效应，非系统性偏移）
    dg = golden["ic_gold_fine_vs_dir3"]["delta_ic"]
    dm = manifest["ic_gold_fine_vs_dir3"]["delta_ic"]
    assert abs(dm - dg) < 0.01, f"金标 ΔIC 漂移过大: golden={dg}, 当前={dm}"
