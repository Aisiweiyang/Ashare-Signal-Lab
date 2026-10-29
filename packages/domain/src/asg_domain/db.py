"""SQLAlchemy ORM：AshareSignal Lab 全部表（规格 §5 + 用户/审计）。

约定（ADR-003）：
- 时间列一律 naive datetime，语义为东八区；
- ID 形如 `<prefix>_<hex12>`（ids.new_id）；
- JSON 列用 sa.JSON（SQLite/PostgreSQL 通用）；
- 原始层（source_documents / source_snapshots）只追加，修正走版本。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .enums import FillStatus, QualityState, RunState, UserRole
from .errors import BindingError
from .ids import new_id


class Base(DeclarativeBase):
    pass


def _rid(prefix: str) -> str:
    return new_id(prefix)


# ---------------------------------------------------------------- 用户与审计


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("usr"))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    password_salt: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.VIEWER.value)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AuditLog(Base):
    """只追加审计日志：API 层禁止提供删除/更新。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(128), default="")
    request_id: Mapped[str] = mapped_column(String(40), default="")
    result: Mapped[str] = mapped_column(String(20), default="ok")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


# ---------------------------------------------------------------- 证券与行情


class Issuer(Base):
    """发行人（公司）。entity_id 对外 contract 即 issuer_id（ent_ 前缀）。"""

    __tablename__ = "issuers"

    issuer_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("ent"))
    name: Mapped[str] = mapped_column(String(128))
    short_name: Mapped[str] = mapped_column(String(64), default="")
    industry: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Security(Base):
    """证券。security_id 对外 contract 即 security_id（sec_ 前缀）。"""

    __tablename__ = "securities"

    security_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("sec"))
    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.issuer_id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(12), index=True)  # 如 000001
    exchange: Mapped[str] = mapped_column(String(8))  # SZSE / SSE / BSE
    name: Mapped[str] = mapped_column(String(64))
    list_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delist_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (UniqueConstraint("symbol", "exchange", name="uq_security_symbol_exchange"),)


class TradingCalendarDay(Base):
    """物化日历快照：数据质量断言用（行情连续性检查）。"""

    __tablename__ = "trading_calendar_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    is_trading: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(32), default="builtin_fixture")


class PriceBar(Base):
    """日频行情。adjusted 标识复权口径（本系统内部统一后复权价计算收益）。"""

    __tablename__ = "price_bars"

    bar_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("bar"))
    security_id: Mapped[str] = mapped_column(ForeignKey("securities.security_id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    pre_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    limit_up: Mapped[float | None] = mapped_column(Float, nullable=True)
    limit_down: Mapped[float | None] = mapped_column(Float, nullable=True)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    adjusted: Mapped[bool] = mapped_column(Boolean, default=False)  # True=后复权
    source: Mapped[str] = mapped_column(String(32), default="")

    __table_args__ = (
        UniqueConstraint("security_id", "trade_date", "adjusted", name="uq_price_bar"),
    )


class CorporateAction(Base):
    __tablename__ = "corporate_actions"

    action_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("act"))
    security_id: Mapped[str] = mapped_column(ForeignKey("securities.security_id"), index=True)
    ex_date: Mapped[date] = mapped_column(Date, index=True)
    action_type: Mapped[str] = mapped_column(String(20))  # dividend / split / rights
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# ---------------------------------------------------------------- 文本原始层


class SourceDocument(Base):
    """原始文档，只追加。修正 = 新版本行（version+1，supersedes_id 指向旧版）。"""

    __tablename__ = "source_documents"

    source_doc_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("doc"))
    source_id: Mapped[str] = mapped_column(String(64), index=True)  # 来源标识（contract 字段）
    url: Mapped[str] = mapped_column(String(512), default="")
    title: Mapped[str] = mapped_column(String(256), default="")
    lang: Mapped[str] = mapped_column(String(8), default="zh")
    raw_content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    published_at_precision: Mapped[str] = mapped_column(String(16), default="unknown")  # datetime/date/unknown
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SourceSnapshot(Base):
    """原始字节快照（对象存储 URI + 哈希），用于复现与合规审计。"""

    __tablename__ = "source_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("snp"))
    source_doc_id: Mapped[str] = mapped_column(ForeignKey("source_documents.source_doc_id"), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    storage_uri: Mapped[str] = mapped_column(String(512), default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- 清洗与聚类


class TextCluster(Base):
    """转载/重复簇：canonical = 簇内 published_at 最早的可信版本。"""

    __tablename__ = "text_clusters"

    cluster_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("clu"))
    canonical_doc_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    first_published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    method: Mapped[str] = mapped_column(String(32), default="simhash_v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MarketText(Base):
    """清洗后的可分析文本（对齐到证券与可得时间）。"""

    __tablename__ = "market_texts"

    market_text_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("mtx"))
    source_doc_id: Mapped[str] = mapped_column(ForeignKey("source_documents.source_doc_id"), index=True)
    cluster_id: Mapped[str | None] = mapped_column(ForeignKey("text_clusters.cluster_id"), nullable=True, index=True)
    security_id: Mapped[str | None] = mapped_column(ForeignKey("securities.security_id"), nullable=True, index=True)
    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("issuers.issuer_id"), nullable=True)
    text_type: Mapped[str] = mapped_column(String(24), default="news")  # news/announcement/research/user_file
    title: Mapped[str] = mapped_column(String(256), default="")
    cleaned_text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    quality_flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    strict_ok: Mapped[bool] = mapped_column(Boolean, default=False)  # 可进严格回测
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class EvidenceSpan(Base):
    """证据跨度：标注/预测引用的字符区间。"""

    __tablename__ = "evidence_spans"

    span_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("evs"))
    market_text_id: Mapped[str] = mapped_column(ForeignKey("market_texts.market_text_id"), index=True)
    owner_kind: Mapped[str] = mapped_column(String(16))  # annotation / prediction
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(String(256), default="")


class EntityLink(Base):
    __tablename__ = "entity_links"

    link_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("lnk"))
    market_text_id: Mapped[str] = mapped_column(ForeignKey("market_texts.market_text_id"), index=True)
    issuer_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    security_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    method: Mapped[str] = mapped_column(String(24))  # regex_code / alias_table / manual
    span_start: Mapped[int] = mapped_column(Integer, default=0)
    span_end: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class MarketEvent(Base):
    """事件层：event_id 对外 contract。一个事件通常锚定一条首发文本。"""

    __tablename__ = "market_events"

    event_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("evt"))
    market_text_id: Mapped[str] = mapped_column(ForeignKey("market_texts.market_text_id"), index=True)
    cluster_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(24), default="other")
    summary: Mapped[str] = mapped_column(String(512), default="")
    effective_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class EventSecurityLink(Base):
    __tablename__ = "event_security_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("market_events.event_id"), index=True)
    security_id: Mapped[str] = mapped_column(ForeignKey("securities.security_id"), index=True)
    exposure: Mapped[str] = mapped_column(String(16), default="primary")  # primary/secondary


# ---------------------------------------------------------------- 标注


class AnnotationTask(Base):
    __tablename__ = "annotation_tasks"

    task_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("tsk"))
    name: Mapped[str] = mapped_column(String(128))
    guide_version: Mapped[str] = mapped_column(String(16), default="1.0")
    sampling_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/closed
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskItem(Base):
    __tablename__ = "task_items"

    item_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("itm"))
    task_id: Mapped[str] = mapped_column(ForeignKey("annotation_tasks.task_id"), index=True)
    market_text_id: Mapped[str] = mapped_column(ForeignKey("market_texts.market_text_id"), index=True)
    order_no: Mapped[int] = mapped_column(Integer, default=0)


class SentimentAnnotation(Base):
    """一条标注。is_preview=True 的 LLM 预标注绝不参与金标与一致性。"""

    __tablename__ = "sentiment_annotations"

    annotation_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("ann"))
    task_id: Mapped[str] = mapped_column(ForeignKey("annotation_tasks.task_id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("task_items.item_id"), index=True)
    market_text_id: Mapped[str] = mapped_column(ForeignKey("market_texts.market_text_id"), index=True)
    annotator_pseudonym: Mapped[str] = mapped_column(String(40), index=True)
    guide_version: Mapped[str] = mapped_column(String(16), default="1.0")
    direction: Mapped[str] = mapped_column(String(16))
    vs_expectation: Mapped[str] = mapped_column(String(20))
    certainty: Mapped[str] = mapped_column(String(16))
    target_type: Mapped[str] = mapped_column(String(16), default="company")
    event_type: Mapped[str] = mapped_column(String(24), default="other")
    security_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    evidence_spans: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(String(512), default="")
    is_preview: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_state: Mapped[str] = mapped_column(String(16), default=QualityState.PENDING.value)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("item_id", "annotator_pseudonym", name="uq_annotation_per_annotator"),
    )


class AdjudicationDecision(Base):
    """仲裁记录，只追加。"""

    __tablename__ = "adjudication_decisions"

    decision_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("adj"))
    item_id: Mapped[str] = mapped_column(ForeignKey("task_items.item_id"), index=True)
    task_id: Mapped[str] = mapped_column(String(40), index=True)
    adjudicator: Mapped[str] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(16))
    vs_expectation: Mapped[str] = mapped_column(String(20))
    certainty: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(String(512), default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AgreementRun(Base):
    __tablename__ = "agreement_runs"

    run_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("agr"))
    task_id: Mapped[str] = mapped_column(String(40), index=True)
    kappas: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"direction": 0.71, ...}
    n_items: Mapped[int] = mapped_column(Integer, default=0)
    n_pairs: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    config_hash: Mapped[str] = mapped_column(String(64), default="")


# ---------------------------------------------------------------- 模型与预测


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    prompt_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("prm"))
    name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    template: Mapped[str] = mapped_column(Text)
    schema_hash: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ModelVersion(Base):
    __tablename__ = "model_versions"

    model_version_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("mdl"))
    provider_kind: Mapped[str] = mapped_column(String(24))  # mock/lexicon/sklearn/llm
    model_name: Mapped[str] = mapped_column(String(64))
    model_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trained_on_dataset_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    artifacts_uri: Mapped[str] = mapped_column(String(512), default="")
    model_card: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PredictionRun(Base):
    __tablename__ = "prediction_runs"

    run_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("prd"))
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.model_version_id"), index=True)
    prompt_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    input_dataset_hash: Mapped[str] = mapped_column(String(64), default="")
    config_hash: Mapped[str] = mapped_column(String(64), default="")
    seed: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default=RunState.PENDING.value, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    env: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(String(512), default="")


class ModelPrediction(Base):
    """单条预测。schema_valid=False 的行永远在隔离区，不参与任何指标。"""

    __tablename__ = "model_predictions"

    prediction_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("ped"))
    run_id: Mapped[str] = mapped_column(ForeignKey("prediction_runs.run_id"), index=True)
    market_text_id: Mapped[str] = mapped_column(String(40), index=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    probabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    uncertainty: Mapped[float] = mapped_column(Float, default=0.0)
    raw_output: Mapped[str] = mapped_column(Text, default="")
    schema_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    quarantine_reason: Mapped[str] = mapped_column(String(256), default="")
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# ---------------------------------------------------------------- 数据集、切分、实验


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"

    dataset_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("dst"))
    name: Mapped[str] = mapped_column(String(64), index=True)
    purpose: Mapped[str] = mapped_column(String(24), default="eval")  # train/test/eval/gold
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SplitDefinition(Base):
    __tablename__ = "split_definitions"

    split_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("spl"))
    name: Mapped[str] = mapped_column(String(64), index=True)
    method: Mapped[str] = mapped_column(String(24), default="walk_forward")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    folds: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class FeatureSnapshot(Base):
    """特征/信号值快照：严格回测的可用性以 available_at 校验。"""

    __tablename__ = "feature_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_set_id: Mapped[str] = mapped_column(String(40), index=True)
    security_id: Mapped[str] = mapped_column(String(40), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    feature_name: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float] = mapped_column(Float)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"

    run_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("exp"))
    name: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="model_eval")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    dataset_hash: Mapped[str] = mapped_column(String(64), default="")
    code_commit: Mapped[str] = mapped_column(String(64), default="")
    seed: Mapped[int] = mapped_column(Integer, default=0)
    model_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    split_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default=RunState.PENDING.value, index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manifest_uri: Mapped[str] = mapped_column(String(512), default="")
    env: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(String(512), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------- 信号与回测


class SignalDefinition(Base):
    __tablename__ = "signal_definitions"

    signal_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("sig"))
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    formula: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # 组件与权重
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    run_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("btk"))
    signal_id: Mapped[str] = mapped_column(ForeignKey("signal_definitions.signal_id"), index=True)
    experiment_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    code_commit: Mapped[str] = mapped_column(String(64), default="")
    seed: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default=RunState.PENDING.value, index=True)
    env: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(String(512), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OrderIntent(Base):
    __tablename__ = "order_intents"

    intent_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("ord"))
    backtest_run_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.run_id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    security_id: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(8))  # buy / sell / close
    intended_qty: Mapped[float] = mapped_column(Float, default=0.0)
    signal_value: Mapped[float] = mapped_column(Float, default=0.0)
    available_at_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class FillSimulation(Base):
    __tablename__ = "fill_simulations"

    fill_id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _rid("fil"))
    backtest_run_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.run_id"), index=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("order_intents.intent_id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=FillStatus.FILLED.value)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_qty: Mapped[float] = mapped_column(Float, default=0.0)
    costs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(String(128), default="")
    sim_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MetricResult(Base):
    __tablename__ = "metric_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.run_id"), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float] = mapped_column(Float)
    ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- 数据版本绑定（ADR-014）


class DatabaseBinding(Base):
    """库级数据版本绑定：首次播种/写行情时落记录，单例行（ADR-014）。

    未绑定库（全部既有遗留库迁移后的初始状态）默认拒绝实验用途；
    绑定后 seed 或生成器版本不匹配的实验在启动时被 BindingError 拒绝——
    把 ADR-012「跨 seed 必须全新库」纪律从事后核验（n_new_bars）提升为
    打开时的机器强制，杜绝「事件来自新 seed、行情复用旧 seed」的混库。
    """

    __tablename__ = "database_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None=非种子数据路径（外部导入）
    generator_version: Mapped[str] = mapped_column(String(64), default="")
    code_commit: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# ---------------------------------------------------------------- 引擎与会话


def make_engine(db_url: str, *, echo: bool = False):
    """创建引擎。SQLite 开启外键强制；连接前确保父目录存在。"""
    if db_url.startswith("sqlite:///"):
        import pathlib

        path_part = db_url[len("sqlite:///") :]
        if path_part and path_part != ":memory:" and not path_part.startswith("/"):
            pathlib.Path(path_part).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url, echo=echo, connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {})

    if db_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine) -> None:
    """建表（测试/演示用；生产走 alembic 迁移）。"""
    Base.metadata.create_all(engine)


# ---------------------------------------------------------------- 数据版本绑定（ADR-014）

_RESEARCH_DATA_TABLES = (Security, PriceBar, SourceDocument)


def current_code_commit() -> str:
    """尽力读取当前 git commit（写入绑定记录的 code_commit 字段）。

    无 git / 非仓库 / 调用失败时返回 "unknown"，绝不抛错——绑定记录不能因为
    环境缺少 git 而阻塞数据写入。
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return out.stdout.strip()[:64] or "unknown"
    except Exception:
        return "unknown"


def _session_of(target) -> Session:
    """绑定函数同时接受 Engine（内部短会话，自行提交）或 Session（并入调用方事务）。"""
    if isinstance(target, Session):
        return target
    return Session(target)


def _has_research_data(session: Session) -> bool:
    """库内是否已含研究数据（Security/PriceBar/SourceDocument 任一非空）。

    用于区分「全新空库」（将随首次写入落绑定）与「未绑定遗留库」（默认拒绝）。
    表尚不存在（未迁移/未建表）按空表处理。
    """
    for model in _RESEARCH_DATA_TABLES:
        try:
            if session.execute(select(func.count()).select_from(model)).scalar_one() > 0:
                return True
        except SQLAlchemyError:
            continue
    return False


def _existing_binding(session: Session) -> DatabaseBinding | None:
    return session.execute(select(DatabaseBinding).order_by(DatabaseBinding.id)).scalars().first()


def _write_binding_audit(session: Session, *, action: str, seed, generator_version: str, note: str) -> None:
    """绑定豁免/采用的审计留痕（audit_logs 表缺失时仅建该表，尽力留痕）。"""
    try:
        session.execute(select(func.count()).select_from(AuditLog))
    except SQLAlchemyError:
        Base.metadata.create_all(session.get_bind(), tables=[AuditLog.__table__])
    session.add(
        AuditLog(
            actor="system",
            action=action,
            target=f"seed={seed}",
            result="ok",
            detail={"generator_version": generator_version, "note": note},
        )
    )
    session.flush()


def bind_database(
    target,
    *,
    seed: int | None,
    generator_version: str,
    code_commit: str = "",
    allow_adopt: bool = False,
) -> DatabaseBinding:
    """落库级绑定（幂等，ADR-014）。

    - 已有一致绑定（seed 与 generator_version 都相同）→ 原样返回；
    - 绑定不一致（改绑）→ BindingError；
    - 未绑定 + 空库 → 写入绑定（首次播种/写行情路径）；
    - 未绑定 + 已含研究数据（遗留库）→ 拒绝自动绑定——绑定必须反映真实数据
      来源，静默采用会伪造血缘；allow_adopt=True 供「显式豁免的一次性演示库」
      采用并写审计留痕（与 require_database_binding 的豁免配对使用）。

    传 Engine 时内部短会话并立即提交；传 Session 时并入调用方事务（由调用方提交）。
    """
    own = not isinstance(target, Session)
    session = _session_of(target)
    try:
        row = _bind_in_session(
            session,
            seed=seed,
            generator_version=generator_version,
            code_commit=code_commit,
            allow_adopt=allow_adopt,
        )
        if own:
            session.commit()
        return row
    except Exception:
        if own:
            session.rollback()
        raise
    finally:
        if own:
            session.close()


def _bind_in_session(
    session: Session,
    *,
    seed: int | None,
    generator_version: str,
    code_commit: str,
    allow_adopt: bool,
) -> DatabaseBinding:
    row = _existing_binding(session)
    if row is not None:
        if row.seed == seed and row.generator_version == generator_version:
            return row
        raise BindingError(
            f"数据库已绑定 seed={row.seed}/generator_version={row.generator_version}，"
            f"拒绝改绑 seed={seed}/generator_version={generator_version}"
            "（ADR-014：跨 seed/数据版本的实验必须使用全新库）"
        )
    if _has_research_data(session):
        if not allow_adopt:
            raise BindingError(
                "未绑定库已含研究数据（旧版本创建的库）：拒绝自动绑定。"
                "请删除旧数据文件（默认 data/ashare_lab.db）或设置新的 ASG_DATABASE_URL 后重新播种；"
                "如需保留旧库，可经显式豁免路径（allow_adopt=True，将写审计留痕）"
            )
        _write_binding_audit(
            session,
            action="database_binding_adopt",
            seed=seed,
            generator_version=generator_version,
            note="遗留库显式采用：数据来源未经绑定记录背书，由调用方担责",
        )
    row = DatabaseBinding(
        seed=seed,
        generator_version=generator_version,
        code_commit=code_commit or current_code_commit(),
    )
    session.add(row)
    session.flush()
    return row


def require_database_binding(
    target,
    *,
    seed: int | None = None,
    generator_version: str | None = None,
    allow_unbound: bool = False,
) -> DatabaseBinding | None:
    """实验入口的打开前校验（ADR-014）：不满足绑定要求时抛 BindingError。

    - 已绑定：seed / generator_version 与请求不同 → BindingError；
    - 未绑定 + 空库（无研究数据）→ 放行：首次播种/写行情即落绑定（全新库工作流）；
    - 未绑定 + 已含研究数据（全部既有遗留库迁移后的初始状态）→ 默认拒绝；
      allow_unbound=True 显式豁免（一次性演示库场景），写审计留痕后放行。

    seed / generator_version 传 None 表示该项不校验。传 Engine 时内部短会话
    （豁免审计自行提交）；传 Session 时并入调用方事务。
    """
    own = not isinstance(target, Session)
    session = _session_of(target)
    try:
        row = _require_in_session(
            session,
            seed=seed,
            generator_version=generator_version,
            allow_unbound=allow_unbound,
        )
        if own:
            session.commit()
        return row
    except Exception:
        if own:
            session.rollback()
        raise
    finally:
        if own:
            session.close()


def _require_in_session(
    session: Session,
    *,
    seed: int | None,
    generator_version: str | None,
    allow_unbound: bool,
) -> DatabaseBinding | None:
    row = _existing_binding(session)
    if row is not None:
        if seed is not None and row.seed != seed:
            raise BindingError(
                f"数据库绑定 seed={row.seed} 与请求 seed={seed} 不匹配"
                "（ADR-014：跨 seed 实验必须使用全新库，混库运行会产生无效证据）"
            )
        if generator_version is not None and row.generator_version != generator_version:
            raise BindingError(
                f"数据库绑定 generator_version={row.generator_version} 与请求 "
                f"{generator_version} 不匹配（ADR-014：生成器版本变化须换库）"
            )
        return row
    if not _has_research_data(session):
        return None  # 空库：首次播种/写行情即落绑定
    if allow_unbound:
        _write_binding_audit(
            session,
            action="database_binding_exempt",
            seed=seed,
            generator_version=generator_version or "",
            note="未绑定遗留库显式豁免实验（一次性演示库场景）",
        )
        return None
    raise BindingError(
        "数据库未绑定且已含研究数据（旧版本创建的库）：默认拒绝实验用途。"
        "请删除旧数据文件（默认 data/ashare_lab.db）或设置新的 ASG_DATABASE_URL 后重新播种；"
        "一次性演示库可对本次运行显式 allow_unbound=True（将写审计留痕）"
    )
