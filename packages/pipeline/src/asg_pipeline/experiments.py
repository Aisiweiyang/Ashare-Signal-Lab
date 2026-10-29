"""实验编排：配置 → 数据 → 标注/金标 → 预测 → 信号 → 事件研究/IC → 带成本回测 → 持久化。

每一步的产物都可追溯：ExperimentRun 记录 config_hash/dataset_hash/seed/commit/环境，
manifest JSON 落盘 research/runs/（负面结果同等保留）。
"""

from __future__ import annotations

import datetime as dt
import json
import time as _time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from asg_backtest import (
    CostModel,
    purge_buffer_days,
    purged_kfold_folds,
    run_backtest,
    split_rows_by_fold,
    split_rows_purged,
    walk_forward_folds,
)
from asg_connectors.market import decision_day_for_publish
from asg_connectors.synthetic import GENERATOR_VERSION
from asg_domain.calendar import TradingCalendar
from asg_domain.db import (
    BacktestRun,
    ExperimentRun,
    FillSimulation,
    MarketText,
    MetricResult,
    OrderIntent,
    SignalDefinition,
    current_code_commit,
    require_database_binding,
)
from asg_domain.enums import FillStatus, RunState, SignalKind
from asg_domain.ids import canonical_env, config_hash, dataset_hash, new_id
from asg_domain.quality import require_available
from asg_domain.timeutils import decision_time_for_event_day
from asg_event_study import (
    Bar,
    PriceBook,
    build_observations,
    compare_signals_ic,
    equal_weight_index,
    group_stats,
    ic_decay_curve,
    label_to_signal,
    spearman_ic,
)
from asg_models import (
    MajorityBaseline,
    MockChatProvider,
    SklearnFineGrainedBaseline,
    classify_with_lexicon,
    direction_only_metrics,
    fine_grained_metrics,
    predictions_of_run,
    run_prediction,
)
from asg_models.runner import TextItem
from sqlalchemy import select
from sqlalchemy.orm import Session

from asg_pipeline.demo import seed_demo


@dataclass
class ExperimentConfig:
    name: str = "exp_fine_vs_3class_v1"
    seed: int = 20260907
    n_events: int = 130
    n_days: int = 180
    alpha: float = 0.012
    provider: str = "mock"  # mock | llm
    folds: int = 3
    embargo_days: int = 2
    horizons: tuple[int, ...] = (1, 2)
    costs: dict = field(default_factory=lambda: {"slippage_bps": 10.0})
    backtest_top_k: int = 3
    min_abs_signal: float = 0.05
    # 下一阶段扩展（v1 配置不含这些键 → 默认值保持 v1 行为不变）：
    split_scheme: str = "walk_forward"  # walk_forward | purged_kfold（协议 §6 预留对照）
    kfold_splits: int = 5
    event_type_ablation: bool = False  # 分事件类型消融（exploratory）
    # 事件层聚类兜底（ADR-015，v4 预注册开启；默认 False 保证既有配置指标不变）：
    event_layer_fallback: bool = False

    @classmethod
    def from_yaml(cls, path: str | Path) -> ExperimentConfig:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        cfg = cls(
            name=raw.get("name", cls.name),
            seed=int(raw.get("seed", 20260907)),
            n_events=int(raw.get("n_events", 130)),
            n_days=int(raw.get("n_days", 180)),
            alpha=float(raw.get("alpha", 0.012)),
            provider=str(raw.get("provider", "mock")),
            folds=int(raw.get("folds", 3)),
            embargo_days=int(raw.get("embargo_days", 2)),
            horizons=tuple(raw.get("horizons", [1, 2])),
            costs=dict(raw.get("costs", {"slippage_bps": 10.0})),
            backtest_top_k=int(raw.get("backtest_top_k", 3)),
            min_abs_signal=float(raw.get("min_abs_signal", 0.05)),
            split_scheme=str(raw.get("split_scheme", "walk_forward")),
            kfold_splits=int(raw.get("kfold_splits", 5)),
            event_type_ablation=bool(raw.get("event_type_ablation", False)),
            event_layer_fallback=bool(raw.get("event_layer_fallback", False)),
        )
        if cfg.split_scheme not in ("walk_forward", "purged_kfold"):
            raise ValueError(f"未知 split_scheme: {cfg.split_scheme}")
        return cfg

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["horizons"] = list(self.horizons)
        if not self.event_layer_fallback:
            # 默认关闭时不进 config_hash / manifest config——v1/v2/v3 配置哈希保持
            # 不变（固化基线与 v3 金标准的 config_hash 断言依赖这一点）；
            # v4 开启时该键显式出现并被计入哈希。
            d.pop("event_layer_fallback", None)
        return d


def _load_price_book(session: Session) -> tuple[PriceBook, list[dt.date]]:
    from asg_domain.db import PriceBar

    rows = session.execute(select(PriceBar)).scalars().all()
    bars = [
        Bar(
            security_id=b.security_id,
            trade_date=b.trade_date,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            pre_close=b.pre_close,
            limit_up=b.limit_up,
            limit_down=b.limit_down,
            suspended=b.suspended,
        )
        for b in rows
    ]
    days = sorted({b.trade_date for b in bars})
    return PriceBook(bars), days


def run_full_experiment(
    session: Session,
    cfg: ExperimentConfig,
    *,
    runs_dir: str | Path = "research/runs",
    seed_demo_data: bool = True,
    allow_unbound: bool = False,
) -> dict:
    """主入口：完整闭环。返回 manifest dict（同时落盘）。

    ADR-014 打开前校验：库内 DatabaseBinding 与 cfg.seed / 当前生成器版本
    不匹配、或未绑定遗留库未显式豁免 → BindingError 拒绝运行（API 实验路由
    经本入口同样受校验，RBAC 权限不变）。全新空库放行，首次播种即落绑定。
    """
    started = dt.datetime.now()
    code_commit = current_code_commit()
    require_database_binding(
        session.get_bind(),
        seed=cfg.seed,
        generator_version=GENERATOR_VERSION,
        allow_unbound=allow_unbound,
    )
    cal = TradingCalendar()
    cfg_hash = config_hash(cfg.to_dict())
    timings: dict[str, float] = {}
    t_phase = _time.monotonic()

    exp = ExperimentRun(
        name=cfg.name,
        kind="fine_vs_3class_full",
        config=cfg.to_dict(),
        config_hash=cfg_hash,
        code_commit=code_commit,
        seed=cfg.seed,
        state=RunState.RUNNING.value,
        env=canonical_env(),
    )
    session.add(exp)
    session.flush()

    # 1) 演示数据（幂等性由 upsert 保证；首次写入即落库级绑定，ADR-014）
    if seed_demo_data:
        demo_stats = seed_demo(
            session,
            seed=cfg.seed,
            n_events=cfg.n_events,
            n_days=cfg.n_days,
            event_layer_fallback=cfg.event_layer_fallback,
            allow_unbound=allow_unbound,
        )
    else:
        demo_stats = {"skipped": True}
    session.expire_all()
    timings["seed"] = round(_time.monotonic() - t_phase, 3)
    t_phase = _time.monotonic()

    # 2) 金标 + 文本
    from asg_domain.db import AnnotationTask
    from asg_labeling.service import build_gold_set

    tasks = (
        session.execute(select(AnnotationTask).order_by(AnnotationTask.created_at)).scalars().all()
    )
    if not tasks:
        raise RuntimeError("数据库中无标注任务；先 seed_demo")
    gold = build_gold_set(session, tasks[-1].task_id)
    text_by_id = {t.market_text_id: t for t in session.execute(select(MarketText)).scalars().all()}

    gold_rows = []
    for g in gold:
        t = text_by_id.get(g["market_text_id"])
        if t is None or t.security_id is None or not t.strict_ok:
            continue
        gold_rows.append(
            {
                "market_text_id": g["market_text_id"],
                "security_id": t.security_id,
                "cluster_id": t.cluster_id,
                "published_at": t.published_at,
                "available_at": t.available_at,
                "precision": (t.quality_flags or {}).get("precision", "unknown"),
                "text": t.cleaned_text,
                "direction": g["direction"],
                "vs_expectation": g["vs_expectation"],
                "certainty": g["certainty"],
                "event_type": g.get("event_type", "other"),
            }
        )
    if len(gold_rows) < 15:
        raise RuntimeError(f"严格可用的金标样本过少（{len(gold_rows)}），不足以支撑实验")

    # 确定性排序（ADR-013）：金标行顺序决定预测、bootstrap 重抽与回测的随机数
    # 消费顺序；(published_at, text_hash) 是与运行实例无关的内容稳定键，
    # 保证同 seed 跨进程逐位可复现。
    from asg_domain.ids import text_hash as _text_hash

    gold_rows.sort(key=lambda r: (r["published_at"], _text_hash(r["text"])))
    # 研究路径事件键用确定性序号：market_text_id 是 uuid4（随运行实例变化），
    # 以它排序/对齐会让 bootstrap 的重抽位置跨进程漂移（ic CI 不可复现）。
    for _i, _r in enumerate(gold_rows):
        _r["event_key"] = f"cev_{_i:06d}"

    dhash = dataset_hash(_dataset_hash_input_rows(gold_rows))
    exp.dataset_hash = dhash
    timings["gold_build"] = round(_time.monotonic() - t_phase, 3)
    t_phase = _time.monotonic()

    # 3) 预测：mock/LLM provider（带缓存与预算）
    provider_note = ""
    if cfg.provider == "llm":
        try:
            from asg_api.settings import get_settings

            s = get_settings()
            from asg_models import make_provider_from_env

            chat = make_provider_from_env(s)
            provider_note = "llm(配置端点)"
        except Exception as e:  # pragma: no cover - 配置缺失路径
            chat = MockChatProvider(seed=cfg.seed, latency_ms=0.0)
            provider_note = f"llm配置失败降级mock: {e}"
    else:
        # latency_ms=0（阶段 16）：mock 默认的 5ms 模拟延迟在数百条研究预测上
        # 是纯等待（v4 619 条 ≈3.3s）；输出逐位不变，仅 latency_ms 记录值变化
        chat = MockChatProvider(seed=cfg.seed, latency_ms=0.0)
        provider_note = "mock"

    items = [TextItem(r["market_text_id"], "", r["text"]) for r in gold_rows]
    pred_run = run_prediction(
        session,
        items=items,
        chat_provider=chat,
        model_kind="mock" if cfg.provider != "llm" else "llm",
        model_name="mock_lexicon_v1" if cfg.provider != "llm" else "configured-llm",
        seed=cfg.seed,
        input_dataset_hash=dhash,
        notes=provider_note,
    )
    session.flush()
    preds = {p.market_text_id: p for p in predictions_of_run(session, pred_run.run_id)}
    for r in gold_rows:
        p = preds.get(r["market_text_id"])
        if p:
            r["pred_direction"] = p.labels.get("direction", "unknown")
            r["pred_vs"] = p.labels.get("vs_expectation", "unknown")
            r["pred_cert"] = p.labels.get("certainty", "unknown")
    timings["prediction"] = round(_time.monotonic() - t_phase, 3)
    t_phase = _time.monotonic()

    # 4) 事件研究样本：决策日 + 信号（金标口径 + 预测口径）
    price_book, all_days = _load_price_book(session)
    benchmark = equal_weight_index(price_book, all_days)

    def _to_event(r: dict, pred: bool) -> dict:
        dday = decision_day_for_publish(r["published_at"], r.get("precision", "unknown"), cal)
        labels = {
            "direction": r["pred_direction" if pred else "direction"],
            "vs_expectation": r["pred_vs" if pred else "vs_expectation"],
            "certainty": r["pred_cert" if pred else "certainty"],
        }
        return {
            "event_key": r["event_key"],
            "market_text_id": r["market_text_id"],
            "security_id": r["security_id"],
            "cluster_id": r["cluster_id"],
            "decision_day": dday,
            "published_at": r["published_at"],
            "available_at": r["available_at"],
            "event_type": r.get("event_type", "other"),
            "labels": labels,
            "signal_fine": label_to_signal(labels, kind="fine_grained"),
            "signal_dir3": label_to_signal(labels, kind="direction_3class"),
        }

    events_gold = [_to_event(r, pred=False) for r in gold_rows]
    events_pred = [_to_event(r, pred=True) for r in gold_rows]

    # 5) 模型评价（金标 vs 预测，样本外折上；切分方案由配置决定）
    folds_used: list[int] = []
    folds_skipped: list[int] = []
    if cfg.split_scheme == "purged_kfold":
        # 协议 §6 预留对照：purged K-fold，测试窗 ±buffer 清除边界标签重叠 + 簇清除
        folds = purged_kfold_folds(
            event_days=[e["decision_day"] for e in events_gold],
            n_splits=cfg.kfold_splits,
            embargo_days=cfg.embargo_days,
        )
        buffer = purge_buffer_days(embargo_days=cfg.embargo_days, horizon=max(cfg.horizons))

        def _split(fold):
            return split_rows_purged(events_gold, fold, buffer_days=buffer)
    else:
        folds = walk_forward_folds(
            event_days=[e["decision_day"] for e in events_gold],
            n_folds=cfg.folds,
            embargo_days=cfg.embargo_days,
        )

        def _split(fold):
            return split_rows_by_fold(events_gold, fold)

    sklearn_model: SklearnFineGrainedBaseline | None = None
    majority = MajorityBaseline()
    oos_pred_rows: list[dict] = []
    for fold in folds:
        train, test = _split(fold)
        if not test or not train:
            # 该折无可训练样本或无测试样本：如实计数，不硬造结论
            folds_skipped.append(fold.fold_id)
            continue
        folds_used.append(fold.fold_id)
        train_rows = []
        text_by_key = {r["event_key"]: r["text"] for r in gold_rows}
        for e in train:
            train_rows.append(
                {
                    "text": text_by_key[e["event_key"]],
                    "direction": e["labels"]["direction"],
                    "vs_expectation": e["labels"]["vs_expectation"],
                    "certainty": e["labels"]["certainty"],
                }
            )
        majority.fit(train_rows)
        try:
            sklearn_model = SklearnFineGrainedBaseline(seed=cfg.seed).fit(train_rows)
        except ValueError:
            sklearn_model = None
        for e in test:
            text = text_by_key[e["event_key"]]
            pred = preds.get(e["market_text_id"])
            row = {
                "fold": fold.fold_id,
                "event_key": e["event_key"],
                "gold": e["labels"],
                "majority": majority.predict_one(text)["labels"],
                "lexicon": classify_with_lexicon(text)["labels"],
                "sklearn": sklearn_model.predict_one(text)["labels"] if sklearn_model else None,
                "mock_llm": (
                    {
                        "direction": pred.labels.get("direction"),
                        "vs_expectation": pred.labels.get("vs_expectation"),
                        "certainty": pred.labels.get("certainty"),
                    }
                    if pred is not None
                    else None
                ),
            }
            oos_pred_rows.append(row)

    model_scores: dict[str, dict] = {}
    for model_key in ("majority", "lexicon", "sklearn", "mock_llm"):
        rows = [r for r in oos_pred_rows if r[model_key]]
        if not rows:
            continue
        model_scores[model_key] = {
            "n_oos": len(rows),
            "direction_only": direction_only_metrics(
                [r["gold"] for r in rows], [r[model_key] for r in rows]
            ),
            "fine_grained": fine_grained_metrics(
                [r["gold"] for r in rows], [r[model_key] for r in rows]
            ),
        }

    # 6) IC 对比（金标信号 + 预测信号；H1 判定）
    obs_gold_fine = build_observations(
        price_book=price_book,
        benchmark_nav=benchmark,
        events=[{**e, "signal": e["signal_fine"]} for e in events_gold],
        horizon=cfg.horizons[0],
    )
    obs_gold_dir3 = build_observations(
        price_book=price_book,
        benchmark_nav=benchmark,
        events=[{**e, "signal": e["signal_dir3"]} for e in events_gold],
        horizon=cfg.horizons[0],
    )
    obs_pred_fine = build_observations(
        price_book=price_book,
        benchmark_nav=benchmark,
        events=[{**e, "signal": e["signal_fine"]} for e in events_pred],
        horizon=cfg.horizons[0],
    )
    obs_pred_dir3 = build_observations(
        price_book=price_book,
        benchmark_nav=benchmark,
        events=[{**e, "signal": e["signal_dir3"]} for e in events_pred],
        horizon=cfg.horizons[0],
    )
    ic_gold = compare_signals_ic(obs_gold_fine, obs_gold_dir3, seed=cfg.seed)
    ic_pred = compare_signals_ic(obs_pred_fine, obs_pred_dir3, seed=cfg.seed)
    decay = ic_decay_curve(
        price_book=price_book,
        benchmark_nav=benchmark,
        events=[{**e, "signal": e["signal_fine"]} for e in events_gold],
        horizons=cfg.horizons,
        seed=cfg.seed,
    )
    group_by_cert: dict[str, dict] = {}
    for cert in ("certain", "conditional", "speculative"):
        sub = [o for o in obs_gold_fine if o.labels.get("certainty") == cert]
        group_by_cert[cert] = group_stats(sub, seed=cfg.seed)

    # 6b) 组件消融（exploratory，标注于 manifest）：分别去掉确定性 / 相对预期维度
    def _ablate(events: list[dict], *, drop: str) -> list[float]:
        from asg_domain.constants import CERT_MULT as _C
        from asg_domain.constants import DIR_SIGN as _D
        from asg_domain.constants import VS_MULT as _V

        out = []
        for e in events:
            lab = e["labels"]
            if drop == "certainty":
                s = _D.get(lab["direction"], 0.0) * _V.get(lab["vs_expectation"], 0.0)
            elif drop == "vs":
                s = _D.get(lab["direction"], 0.0) * _C.get(lab["certainty"], 0.0)
            else:
                raise ValueError(drop)
            out.append(round(s, 6))
        return out

    ablation: dict[str, dict] = {}
    for drop in ("certainty", "vs"):
        obs_ab = build_observations(
            price_book=price_book,
            benchmark_nav=benchmark,
            events=[
                {**e, "signal": s}
                for e, s in zip(events_gold, _ablate(events_gold, drop=drop), strict=True)
            ],
            horizon=cfg.horizons[0],
        )
        ablation[f"drop_{drop}"] = compare_signals_ic(obs_gold_fine, obs_ab, seed=cfg.seed)

    # 6c) 分时期（walk-forward 折）IC：时间稳定性检查
    ic_by_fold: list[dict] = []
    for fold in folds:
        test_events = [
            e for e in events_gold if fold.test_start <= e["decision_day"] <= fold.test_end
        ]
        if len(test_events) < 5:
            continue
        obs_f = build_observations(
            price_book=price_book,
            benchmark_nav=benchmark,
            events=[{**e, "signal": e["signal_fine"]} for e in test_events],
            horizon=cfg.horizons[0],
        )
        obs_d = build_observations(
            price_book=price_book,
            benchmark_nav=benchmark,
            events=[{**e, "signal": e["signal_dir3"]} for e in test_events],
            horizon=cfg.horizons[0],
        )
        ic_by_fold.append(
            {
                "fold": fold.fold_id,
                "test": f"{fold.test_start}~{fold.test_end}",
                "n": len(test_events),
                "ic_fine": spearman_ic(
                    [o.signal for o in obs_f], [o.forward_ret for o in obs_f], seed=cfg.seed
                )["ic"],
                "ic_dir3": spearman_ic(
                    [o.signal for o in obs_d], [o.forward_ret for o in obs_d], seed=cfg.seed
                )["ic"],
            }
        )

    # 6d) 分事件类型消融（exploratory，配置开启时）：各类型内 fine vs dir3 的 IC 与异常收益
    event_type_stats: dict[str, dict] = {}
    if cfg.event_type_ablation:
        for et in sorted({e["event_type"] for e in events_gold}):
            sub = [e for e in events_gold if e["event_type"] == et]
            if len(sub) < 5:
                continue  # 样本过少的类型不做分组结论（如实跳过）
            obs_f = build_observations(
                price_book=price_book,
                benchmark_nav=benchmark,
                events=[{**e, "signal": e["signal_fine"]} for e in sub],
                horizon=cfg.horizons[0],
            )
            obs_d = build_observations(
                price_book=price_book,
                benchmark_nav=benchmark,
                events=[{**e, "signal": e["signal_dir3"]} for e in sub],
                horizon=cfg.horizons[0],
            )
            event_type_stats[et] = {
                "n": len(sub),
                "ic_fine_vs_dir3": compare_signals_ic(obs_f, obs_d, seed=cfg.seed),
                "abnormal_fine": group_stats(obs_f, seed=cfg.seed),
            }
    timings["model_eval_and_event_study"] = round(_time.monotonic() - t_phase, 3)
    t_phase = _time.monotonic()

    # 7) 带成本回测（预测信号，样本外事件，严格 available_at 门控）
    signal_def = session.execute(
        select(SignalDefinition).where(SignalDefinition.name == "fine_grained_v1")
    ).scalar_one_or_none()
    if signal_def is None:
        signal_def = SignalDefinition(
            name="fine_grained_v1",
            kind=SignalKind.FINE_GRAINED.value,
            formula={"components": ["direction", "vs_expectation", "certainty"], "op": "product"},
            params={"pre_registered": True, "protocol": "RESEARCH_PROTOCOL v1.0"},
        )
        session.add(signal_def)
        session.flush()

    test_event_keys = {r["event_key"] for r in oos_pred_rows}
    bt_signal_rows: list[dict] = []
    for e in events_pred:
        if e["event_key"] not in test_event_keys:
            continue
        require_available(
            e["available_at"],
            decision_time_for_event_day(e["decision_day"]),
            f"事件 {e['event_key']}",
        )
        bt_signal_rows.append(
            {
                "security_id": e["security_id"],
                "decision_day": e["decision_day"],
                "signal": e["signal_fine"],
                "available_at_used": e["available_at"],
            }
        )
    bt_result = run_backtest(
        price_book=price_book,
        signal_rows=bt_signal_rows,
        cost_model=CostModel.from_config(cfg.costs),
        top_k=cfg.backtest_top_k,
        min_abs_signal=cfg.min_abs_signal,
        seed=cfg.seed,
    )
    bt_run = BacktestRun(
        signal_id=signal_def.signal_id,
        experiment_run_id=exp.run_id,
        config={**cfg.to_dict(), "costs": cfg.costs},
        config_hash=config_hash(cfg.costs),
        code_commit=code_commit,
        seed=cfg.seed,
        state=RunState.SUCCEEDED.value,
        env=canonical_env(),
        notes=f"样本外事件回测（{len(bt_signal_rows)} 个信号行）",
    )
    session.add(bt_run)
    session.flush()
    # 成交行按 intent 对象恒等回链（阶段 11）：fills 引用与 order_intents 相同的
    # OrderIntentRow 对象，替代逐 fill 回查数据库。
    orm_intent_by_row: dict[int, OrderIntent] = {}
    for intent in bt_result.order_intents:
        oi = OrderIntent(
            backtest_run_id=bt_run.run_id,
            trade_date=intent.trade_date,
            security_id=intent.security_id,
            side=intent.side,
            intended_qty=intent.intended_qty,
            signal_value=intent.signal_value,
            available_at_used=intent.available_at_used,
        )
        session.add(oi)
        orm_intent_by_row[id(intent)] = oi
    session.flush()
    for f in bt_result.fills:
        oi = orm_intent_by_row.get(id(f.intent))
        session.add(
            FillSimulation(
                backtest_run_id=bt_run.run_id,
                intent_id=oi.intent_id if oi is not None else new_id("ord"),
                status=f.status.value,
                fill_price=f.fill_price,
                fill_qty=f.fill_qty,
                costs=f.costs,
                reason=f.reason[:120],
            )
        )
    for name, value in bt_result.metrics.items():
        if isinstance(value, (int, float)) and value is not None:
            session.add(
                MetricResult(backtest_run_id=bt_run.run_id, name=str(name), value=float(value))
            )
    rejection_stats: dict[str, int] = {}
    for f in bt_result.fills:
        if f.status != FillStatus.FILLED:
            rejection_stats[f.status.value] = rejection_stats.get(f.status.value, 0) + 1

    # 7b) 成本敏感性（Q3）：同一信号行在不同滑点下的净结果
    cost_sensitivity: list[dict] = []
    for slip in (5.0, 10.0, 20.0, 50.0):
        grid_cfg = {**cfg.costs, "slippage_bps": slip}
        g = run_backtest(
            price_book=price_book,
            signal_rows=bt_signal_rows,
            cost_model=CostModel.from_config(grid_cfg),
            top_k=cfg.backtest_top_k,
            min_abs_signal=cfg.min_abs_signal,
            seed=cfg.seed,
        )
        cost_sensitivity.append({"slippage_bps": slip, **g.metrics})
    timings["backtest"] = round(_time.monotonic() - t_phase, 3)

    # 8) 汇总与持久化
    manifest = {
        "run_id": exp.run_id,
        "name": cfg.name,
        "config": cfg.to_dict(),
        "config_hash": cfg_hash,
        "seed": cfg.seed,
        "dataset_hash": dhash,
        "provider_note": provider_note,
        "demo_stats": demo_stats,
        "n_gold_strict": len(gold_rows),
        "n_oos_events": len(oos_pred_rows),
        "model_scores": model_scores,
        "ic_gold_fine_vs_dir3": ic_gold,
        "ic_pred_fine_vs_dir3": ic_pred,
        "ic_decay_fine_gold": decay,
        "ic_by_fold_gold": ic_by_fold,
        "split_scheme": cfg.split_scheme,
        "folds_used": {"ids": folds_used, "skipped_empty": folds_skipped},
        "ablation_gold_exploratory": ablation,
        "ic_by_event_type_gold_exploratory": event_type_stats,
        "cost_sensitivity_backtest": cost_sensitivity,
        "abnormal_by_certainty_gold": group_by_cert,
        "backtest": {
            "run_id": bt_run.run_id,
            "metrics": bt_result.metrics,
            "rejections": rejection_stats,
            "n_intents": len(bt_result.order_intents),
        },
        "h1_verdict": {
            "supported_on_gold": ic_gold.get("h1_supported", False),
            "supported_on_predictions": ic_pred.get("h1_supported", False),
            "note": "判定标准见 RESEARCH_PROTOCOL §5；合成数据上仅支持方法验证结论",
        },
        "timings_seconds": timings,
        "started_at": started.isoformat(),
        "finished_at": dt.datetime.now().isoformat(),
        "env": canonical_env(),
    }
    manifest = _sanitize_json(manifest)
    exp.state = RunState.SUCCEEDED.value
    exp.metrics = _sanitize_json(
        {
            "ic_gold": ic_gold,
            "ic_pred": ic_pred,
            "model_scores": model_scores,
            "backtest": bt_result.metrics,
        }
    )
    exp.finished_at = dt.datetime.now()

    runs_path = Path(runs_dir)
    runs_path.mkdir(parents=True, exist_ok=True)
    manifest_path = runs_path / f"{exp.run_id}.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, default=str)
    exp.manifest_uri = str(manifest_path)
    session.commit()
    return manifest


def _sanitize_json(obj):
    """递归把 NaN/Inf 换成 None（JSON 不允许 nan；HTTP 响应与 manifest 都要干净）。"""
    import math

    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


# dataset_hash 输入剔除的字段：uuid4 主键类（随运行实例随机，ADR-013 已知残留）
# 与全文 text（行内容以标签/时间/序号为准，与收口前口径一致）。
_DATASET_HASH_EXCLUDED = frozenset({"market_text_id", "security_id", "cluster_id", "text"})


def _dataset_hash_input_rows(gold_rows: list[dict]) -> list[dict[str, str]]:
    """dataset_hash 输入口径（ADR-014 收口，ADR-013 遗留残留的处理）。

    剔除 uuid4 主键类字段后仅剩内容稳定字段（event_key/published_at/
    available_at/precision/三维标签/event_type）——同 seed 两次运行的
    dataset_hash **相同**（跨进程等价性测试验收）。

    行为变更声明：新口径哈希与历史 manifest（旧口径混入 uuid4，同 seed 两次
    运行必不同）不可比；历史 manifest 只追加不回写，新旧对照见 TEST_REPORT §16。
    """
    return [{k: str(v) for k, v in r.items() if k not in _DATASET_HASH_EXCLUDED} for r in gold_rows]
