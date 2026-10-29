"""集成测试：摄取流水线幂等性、API 全流程、实验运行器、导出 contract。"""

from __future__ import annotations

import datetime as dt
import typing

import pytest
from asg_connectors.market import decision_day_for_publish, generate_price_bars
from asg_connectors.synthetic import SyntheticConnector
from asg_domain.calendar import TradingCalendar
from asg_domain.contracts import export_entities, import_entities
from asg_domain.db import MarketEvent, MarketText, TextCluster
from asg_pipeline import ingest_documents, upsert_market_data
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


class TestIngestPipeline:
    def test_ingest_with_reposts_clusters_and_events(self, db, small_connector: SyntheticConnector):
        upsert_market_data(db, securities=small_connector.securities, bars={}, cal=TradingCalendar())
        docs = small_connector.fetch_batch()
        stats = ingest_documents(db, docs, cal=TradingCalendar())
        assert stats["n_ingested"] == len(docs)
        assert stats["n_clusters"] < stats["n_texts"]  # 转载被聚合
        assert stats["n_events"] == stats["n_clusters"]  # 每簇一个事件
        # 簇首发文本事件数与簇数一致
        n_clusters = db.execute(select(func.count()).select_from(TextCluster)).scalar_one()
        n_events = db.execute(select(func.count()).select_from(MarketEvent)).scalar_one()
        assert n_clusters == n_events

    def test_ingest_idempotent(self, db, small_connector: SyntheticConnector):
        upsert_market_data(db, securities=small_connector.securities, bars={}, cal=TradingCalendar())
        docs = small_connector.fetch_batch()
        first = ingest_documents(db, docs, cal=TradingCalendar())
        second = ingest_documents(db, docs, cal=TradingCalendar())
        assert second["n_ingested"] == 0
        assert second["n_dupe_ingest"] == len(docs)
        n_texts = db.execute(select(func.count()).select_from(MarketText)).scalar_one()
        assert n_texts == first["n_texts"]

    def test_securities_upsert_idempotent(self, db, small_connector: SyntheticConnector):
        s1 = upsert_market_data(db, securities=small_connector.securities, bars={}, cal=TradingCalendar())
        s2 = upsert_market_data(db, securities=small_connector.securities, bars={}, cal=TradingCalendar())
        assert s1["n_new_securities"] == 8
        assert s2["n_new_securities"] == 0

    def test_price_bars_continuity_and_limits(self, db, small_connector: SyntheticConnector):
        bars = generate_price_bars(seed=42, events=small_connector.events, n_days=60, calendar=TradingCalendar())
        stats = upsert_market_data(db, securities=small_connector.securities, bars=bars, cal=TradingCalendar())
        assert stats["n_new_bars"] > 0
        from asg_domain.db import PriceBar

        sample = db.execute(select(PriceBar).limit(5)).scalars().all()
        for b in sample:
            if not b.suspended:
                assert b.limit_up >= b.close >= b.limit_down
                assert b.high >= b.low

    def test_date_only_precision_gates_availability(self, db, small_connector):
        """仅日期精度的文本：available_at 推到次交易日 15:00（保守）。"""
        from asg_connectors.base import RawDoc

        upsert_market_data(db, securities=small_connector.securities, bars={}, cal=TradingCalendar())
        cal = TradingCalendar()
        doc = RawDoc(
            source_id="t", external_id="x1", title="t", content="云舟数控公告净利润大增超出预期",
            published_at=dt.datetime(2026, 3, 2), published_precision="date", symbol_hint="601907",
        )
        stats = ingest_documents(db, [doc], cal=cal)
        assert stats["n_texts"] == 1
        t = db.execute(select(MarketText)).scalars().first()
        assert t.available_at is not None and t.available_at.time().hour == 15
        assert t.strict_ok is False  # 日期精度不可进严格回测
        # 决策日在 available_at 当日
        dday = decision_day_for_publish(doc.published_at, "date", cal)
        assert dday == t.available_at.date()


class TestEntityContract:
    def test_export_import_roundtrip(self):
        rows = [{"entity_id": "ent_1", "security_id": "sec_1", "event_id": "evt_1", "source_id": "syn"}]
        payload = export_entities(rows)
        assert payload["contract_version"] == "1.0"
        back = import_entities(payload)
        assert back == rows

    def test_missing_field_rejected(self):
        with pytest.raises(ValueError):
            export_entities([{"entity_id": "e", "security_id": "s"}])

    def test_version_mismatch(self):
        with pytest.raises(ValueError):
            import_entities({"contract_version": "9.9", "entities": []})


class TestExperimentRunner:
    def test_full_experiment_manifest(self, db, tmp_path):
        from asg_pipeline import ExperimentConfig, run_full_experiment

        cfg = ExperimentConfig(name="it_exp", seed=42, n_events=40, n_days=100, folds=2, horizons=(1,))
        manifest = run_full_experiment(db, cfg, runs_dir=tmp_path)
        # manifest 结构完整
        for key in ("run_id", "config_hash", "dataset_hash", "model_scores", "ic_gold_fine_vs_dir3", "backtest", "h1_verdict"):
            assert key in manifest
        # 样本外与金标规模合理
        assert manifest["n_gold_strict"] > 10
        assert manifest["n_oos_events"] > 5
        # 回测指标持久化
        assert manifest["backtest"]["metrics"]["n_fills"] >= 0
        # manifest 文件落盘
        assert (tmp_path / f"{manifest['run_id']}.json").exists()
        # 协议 §8 可追溯性：ExperimentRun/BacktestRun 记录代码 commit（阶段 23 修复的审计缺口）
        from asg_domain.db import BacktestRun, ExperimentRun, current_code_commit

        want = current_code_commit()
        exp_row = db.get(ExperimentRun, manifest["run_id"])
        assert exp_row is not None and exp_row.code_commit == want
        bt_row = db.execute(select(BacktestRun).where(BacktestRun.experiment_run_id == manifest["run_id"])).scalars().first()
        assert bt_row is not None and bt_row.code_commit == want

    def test_experiment_reproducible_with_same_seed(self, tmp_path):
        """同 seed 两次运行：合成文本与行情完全一致（预测有缓存差异不比较）。"""
        from asg_connectors.market import generate_price_bars
        from asg_connectors.synthetic import generate_universe

        _s1, e1 = generate_universe(seed=99, n_events=25, n_days=60)
        _s2, e2 = generate_universe(seed=99, n_events=25, n_days=60)
        assert [ev.text for ev in e1] == [ev.text for ev in e2]
        b1 = generate_price_bars(seed=99, events=e1, n_days=60)
        b2 = generate_price_bars(seed=99, events=e2, n_days=60)
        for sym in b1:
            assert [b["close"] for b in b1[sym]] == [b["close"] for b in b2[sym]]


class TestEventLayerFallback:
    """事件层兜底接入（ADR-015 → v4 预注册）：默认路径逐位不变，opt-in 路径生效。"""

    _UNIVERSE: typing.ClassVar[dict] = {"seed": 20260912, "n_events": 130, "n_days": 180}

    def _ingest(self, db, *, fallback: bool):
        from asg_domain.db import SourceDocument

        conn = SyntheticConnector(**self._UNIVERSE)
        upsert_market_data(db, securities=conn.securities, bars={}, cal=TradingCalendar())
        stats = ingest_documents(db, conn.fetch_batch(), cal=TradingCalendar(), event_layer_fallback=fallback)
        texts = db.execute(select(MarketText)).scalars().all()
        src_docs = {s.source_doc_id: s for s in db.execute(select(SourceDocument)).scalars().all()}
        return conn, stats, texts, src_docs

    @staticmethod
    def _items_of(texts):
        from asg_text_pipeline.dedup import DedupItem

        return [DedupItem(item_id=t.market_text_id, text=t.cleaned_text, published_at=t.published_at) for t in texts]

    @staticmethod
    def _cluster_of_texts(texts):
        from asg_text_pipeline.dedup import cluster_duplicates

        return cluster_duplicates(TestEventLayerFallback._items_of(texts))

    def test_default_path_untouched(self, db):
        """默认（不开兜底）：库内簇结构与直接调用 cluster_duplicates 完全一致。"""
        _conn, stats, texts, _src = self._ingest(db, fallback=False)
        clustering = self._cluster_of_texts(texts)
        assert stats["n_clusters"] == clustering.n_clusters
        assert {c.cluster_id for c in db.execute(select(TextCluster)).scalars().all()} == set(clustering.canonical_of)
        assert {t.market_text_id: t.cluster_id for t in texts} == clustering.cluster_of
        assert {c.method for c in db.execute(select(TextCluster)).scalars().all()} == {"simhash_v1"}

    def test_fallback_path_uses_refine_clusters(self, db):
        """开启兜底：库内簇结构与 refine_clusters（实体链接的 security_of）一致。"""
        from asg_text_pipeline.event_layer import EventLayerConfig, refine_clusters

        conn, stats, texts, _src = self._ingest(db, fallback=True)
        base = self._cluster_of_texts(texts)
        security_of = {t.market_text_id: (t.security_id or "") for t in texts}
        refined = refine_clusters(self._items_of(texts), base, security_of=security_of, config=EventLayerConfig())
        assert stats["n_clusters"] == refined.n_clusters
        assert {t.market_text_id: t.cluster_id for t in texts} == refined.cluster_of
        assert {c.method for c in db.execute(select(TextCluster)).scalars().all()} == {"simhash_v1+event_layer_v1"}
        # 该宇宙上兜底确实改变聚类（§15.3：130 on 126→120 簇）
        assert stats["n_clusters"] != self._cluster_of_texts(texts).n_clusters
        # 簇首发（canonical）仍是每簇一条事件
        assert stats["n_events"] == stats["n_clusters"]
        assert conn.generator_version  # 生成器版本随绑定记录落库（ADR-014）

    def test_fallback_quality_report_same_run(self, db):
        """同一次运行内的兜底前后真值对照（§13.1 口径，§15.3 已知数字）。"""
        from asg_pipeline.event_quality import fallback_quality_report

        conn, _stats, texts, _src = self._ingest(db, fallback=True)
        report = fallback_quality_report(db, conn)
        assert report["n_texts"] == len(texts)
        base, full = report["baseline_simhash"], report["with_fallback"]
        # 与 research/runs/event_layer_2026-09-14.json 的 130 on 行一致（同 seed 同代码）
        assert base["false_merge_instances"] == 11
        assert base["missed_reposts"] == 7 and base["n_repost_true"] == 55
        assert full["false_merge_instances"] == 11
        assert full["missed_reposts"] == 1  # 7/55 → 1/55：接入即改善
        assert full["missed_repost_rate"] < base["missed_repost_rate"]

    def test_config_hash_stable_without_fallback_flag(self):
        """默认配置的 config_hash 不受新字段影响（既有配置哈希不变的保证）。"""
        from asg_domain.ids import config_hash
        from asg_pipeline import ExperimentConfig

        plain = ExperimentConfig(name="x", seed=1)
        assert "event_layer_fallback" not in plain.to_dict()
        enabled = ExperimentConfig(name="x", seed=1, event_layer_fallback=True)
        assert enabled.to_dict()["event_layer_fallback"] is True
        assert config_hash(plain.to_dict()) != config_hash(enabled.to_dict())
