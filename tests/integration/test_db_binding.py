"""数据库绑定契约测试（ADR-014 落地；阶段 11 的 5 项 skip 骨架在本阶段去 skip）。

ADR-012 的遗留问题：存储侧此前无法阻止「共享库混用不同 seed/数据版本」
（upsert_market_data 幂等跳过旧行情，事件是新 seed、行情是旧 seed，只能靠
运行后人工核对 n_new_bars 检出）。ADR-014 落地后：

- `DatabaseBinding` 表：首次播种/写行情即落 seed/generator_version/code_commit；
- `require_database_binding`：实验入口（run_full_experiment / API 实验路由）打开前
  校验——绑定不匹配、或未绑定遗留库未显式豁免 → 抛 BindingError；
- `bind_database`：幂等落绑定；未绑定但已含研究数据的库拒绝自动绑定（遗留库
  必须走全新库，或经显式豁免/采用路径并留审计痕）。

预期接口（已实现，见 packages/domain/src/asg_domain/db.py）：

    from asg_domain.db import bind_database, require_database_binding
    bind_database(engine_or_session, seed=..., generator_version=..., code_commit=...)
    require_database_binding(engine, seed=..., generator_version=...)   # 不匹配抛 BindingError
    require_database_binding(engine, allow_unbound=True)                # 显式豁免（一次性演示库，留审计痕）
"""

from __future__ import annotations

import pytest
from asg_connectors.synthetic import GENERATOR_VERSION, SyntheticConnector
from asg_domain.calendar import TradingCalendar
from asg_domain.db import (
    AuditLog,
    DatabaseBinding,
    bind_database,
    init_db,
    make_engine,
    make_session_factory,
    require_database_binding,
)
from asg_domain.errors import BindingError
from asg_pipeline import ExperimentConfig, run_full_experiment, seed_demo, upsert_market_data
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import auth_headers

pytestmark = pytest.mark.integration

# 与 tests/integration/test_pipeline_integration.py 的快速实验配置同量级（约数秒）
_EXP_KWARGS = {"n_events": 40, "n_days": 100, "folds": 2, "horizons": (1,)}


def _exp_cfg(name: str, seed: int) -> ExperimentConfig:
    return ExperimentConfig(name=name, seed=seed, **_EXP_KWARGS)


def _binding(db: Session) -> DatabaseBinding | None:
    with Session(db.get_bind()) as s:
        return s.execute(select(DatabaseBinding).order_by(DatabaseBinding.id)).scalars().first()


def test_binding_mismatch_rejected(db, tmp_path):
    """库绑定 seed=A 时以 seed=B 打开实验必须被拒绝（BindingError）。"""
    seed_demo(db, seed=7, n_events=40)
    assert _binding(db) is not None and _binding(db).seed == 7

    # 引擎级契约：匹配放行、不匹配拒绝
    assert require_database_binding(db.get_bind(), seed=7, generator_version=GENERATOR_VERSION) is not None
    with pytest.raises(BindingError):
        require_database_binding(db.get_bind(), seed=42, generator_version=GENERATOR_VERSION)

    # 实验入口级契约：跨 seed 运行在启动时被拒绝（ADR-012 混库场景的机器强制）
    with pytest.raises(BindingError):
        run_full_experiment(db, _exp_cfg("binding_conflict", 42), runs_dir=tmp_path)


def test_unbound_database_rejected_for_experiments(db, small_connector: SyntheticConnector, tmp_path):
    """无绑定记录的遗留库（含当前全部既有库）默认拒绝跑实验。"""
    # 模拟遗留库：已写入研究数据（证券/行情），但无绑定记录（迁移后初始状态）
    upsert_market_data(db, securities=small_connector.securities, bars={}, cal=TradingCalendar())
    assert _binding(db) is None

    with pytest.raises(BindingError) as ei:
        run_full_experiment(db, _exp_cfg("legacy_reject", 42), runs_dir=tmp_path)
    # 面向升级用户的消息必须可操作（阶段 23）：指出删除旧库/换库的具体出路
    assert "data/ashare_lab.db" in str(ei.value) and "ASG_DATABASE_URL" in str(ei.value)


def test_unbound_database_allowed_with_explicit_optout(db, small_connector: SyntheticConnector, tmp_path):
    """allow_unbound=True 显式豁免路径可用（一次性演示库场景），且豁免必须留痕。"""
    upsert_market_data(db, securities=small_connector.securities, bars={}, cal=TradingCalendar())
    manifest = run_full_experiment(db, _exp_cfg("legacy_exempt", 42), runs_dir=tmp_path, allow_unbound=True)

    # 行情确由本次运行生成（8 证券 × 100 交易日），未被旧行情污染
    assert manifest["demo_stats"]["market_data"] == {"n_new_securities": 0, "n_new_bars": 800, "n_days": 100}

    # 豁免写审计留痕；库被显式采用为本次运行的数据版本
    with Session(db.get_bind()) as s:
        audits = s.execute(select(AuditLog).where(AuditLog.action == "database_binding_exempt")).scalars().all()
        row = s.execute(select(DatabaseBinding)).scalars().first()
    assert audits, "allow_unbound 豁免必须写审计留痕"
    assert row is not None and row.seed == 42 and row.generator_version == GENERATOR_VERSION


def test_binding_written_at_seed_time(db):
    """seed_demo / upsert_market_data 首次写入即落绑定记录（seed+generator_version+commit）。"""
    seed_demo(db, seed=7, n_events=40)
    row = _binding(db)
    assert row is not None
    assert row.seed == 7
    assert row.generator_version == GENERATOR_VERSION
    assert row.code_commit  # 尽力记录当前代码 commit（git 不可用时为 "unknown"）
    assert row.created_at is not None

    # 幂等：一致的重绑返回既有记录，不产生第二行
    again = bind_database(db, seed=7, generator_version=GENERATOR_VERSION)
    assert again.seed == 7
    with Session(db.get_bind()) as s:
        assert len(s.execute(select(DatabaseBinding)).scalars().all()) == 1


def test_generator_version_change_rejected(db):
    """同 seed 但生成器版本变化（v1→v2 类情形）必须拒绝，防止文本形态漂移混库。"""
    seed_demo(db, seed=7, n_events=40)
    old_version = "synthetic_demo_v1_plain"
    with pytest.raises(BindingError):
        bind_database(db, seed=7, generator_version=old_version)
    with pytest.raises(BindingError):
        require_database_binding(db.get_bind(), seed=7, generator_version=old_version)
    # 库内绑定未被破坏
    assert _binding(db).generator_version == GENERATOR_VERSION


# ---------------------------------------------------------------- 补充契约（本阶段新增场景）


def test_upsert_market_data_binds_on_first_write(tmp_path):
    """upsert_market_data 直接写行情路径：bind_seed 提供时首次写入即落绑定。"""
    engine = make_engine(f"sqlite:///{(tmp_path / 'upbind.db').as_posix()}")
    init_db(engine)
    s = make_session_factory(engine)()
    try:
        upsert_market_data(s, securities=[], bars={}, bind_seed=5, bind_generator_version="ext_demo_v1")
        row = s.execute(select(DatabaseBinding)).scalars().first()
        assert row is not None and row.seed == 5 and row.generator_version == "ext_demo_v1"

        # 已绑定后：不带 bind 参数的直写不改变绑定状态；不一致的改绑被拒绝
        upsert_market_data(s, securities=[], bars={})
        with Session(engine) as verify:
            rows = verify.execute(select(DatabaseBinding)).scalars().all()
        assert len(rows) == 1
        with pytest.raises(BindingError):
            upsert_market_data(s, securities=[], bars={}, bind_seed=6, bind_generator_version="ext_demo_v1")
    finally:
        s.close()
        engine.dispose()


def test_run_full_experiment_binds_fresh_database(db, tmp_path):
    """全新空库直接跑实验（跨 seed 纪律的标准工作流）：放行并在首次播种落绑定。"""
    manifest = run_full_experiment(db, _exp_cfg("binds_fresh", 42), runs_dir=tmp_path)
    assert manifest["demo_stats"]["market_data"]["n_new_bars"] == 800
    row = _binding(db)
    assert row is not None
    assert row.seed == 42
    assert row.generator_version == GENERATOR_VERSION


def test_api_experiment_route_rejects_seed_mismatch(api_client):
    """API 实验路由（既有 RBAC 权限下）同样受绑定校验：seed 不匹配 → 400。"""
    h = auth_headers(api_client, "admin")
    r = api_client.post("/api/data/seed-demo?n_events=40&seed=7", headers=h)
    assert r.status_code == 200

    hr = auth_headers(api_client, "researcher")
    r = api_client.post(
        "/api/experiments/run",
        headers=hr,
        json={"name": "binding_conflict_api", "seed": 42, "n_events": 40, "folds": 2, "provider": "mock"},
    )
    assert r.status_code == 400
    assert "seed=7" in r.json()["detail"] and "seed=42" in r.json()["detail"]


def test_api_binding_status_endpoint(api_client):
    """GET /api/data/binding：未绑定 → bound=false；播种后 → 绑定三元组可见（texts.read 权限）。"""
    h = auth_headers(api_client, "researcher")

    r = api_client.get("/api/data/binding", headers=h)
    assert r.status_code == 200
    assert r.json() == {"bound": False}

    ha = auth_headers(api_client, "admin")
    assert api_client.post("/api/data/seed-demo?n_events=40&seed=7", headers=ha).status_code == 200

    r = api_client.get("/api/data/binding", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["bound"] is True
    assert body["seed"] == 7
    assert body["generator_version"] == GENERATOR_VERSION
    assert body["code_commit"]
    assert body["created_at"]

    # texts.read 权限矩阵：全部只读及以上角色可读（viewer 为最低只读角色）
    hv = auth_headers(api_client, "viewer")
    assert api_client.get("/api/data/binding", headers=hv).status_code == 200
