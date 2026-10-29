"""pytest fixtures：内存/临时 SQLite、迷你演示库、API 测试客户端。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from asg_connectors.synthetic import SyntheticConnector, generate_universe
from asg_domain.calendar import TradingCalendar
from asg_domain.db import make_engine, make_session_factory
from asg_event_study import Bar, PriceBook
from asg_pipeline import seed_demo
from sqlalchemy.orm import sessionmaker

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def pytest_configure():
    # xdist prepares --basetemp before any fixture runs. A fresh checkout has
    # no parent cache directory yet, unlike a long-used development tree.
    (_PROJECT_ROOT / ".pytest_tmp").mkdir(exist_ok=True)


@pytest.fixture()
def tmp_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """项目内临时目录（该环境默认系统临时根不可扫描）。"""
    base = _PROJECT_ROOT / ".pytest_tmp"
    base.mkdir(exist_ok=True)
    import tempfile

    return Path(tempfile.mkdtemp(prefix="case-", dir=base))


@pytest.fixture()
def session_factory(tmp_path: Path):
    db_file = tmp_path / "test.db"
    engine = make_engine(f"sqlite:///{db_file.as_posix()}")
    from asg_domain.db import init_db

    init_db(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture()
def db(session_factory: sessionmaker):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture()
def small_connector() -> SyntheticConnector:
    return SyntheticConnector(seed=42, n_events=30, n_days=90)


@pytest.fixture()
def seeded_db(db, small_connector: SyntheticConnector):
    """带演示数据（证券+行情+文本+任务+金标）的会话。"""
    seed_demo(db, seed=42, n_events=30, n_days=90, task_name="fixture任务")
    return db


@pytest.fixture()
def api_client(tmp_path: Path):
    """TestClient：每个测试独立数据库与独立应用状态。"""
    from asg_api.main import create_app, ensure_demo_users
    from asg_domain.db import init_db
    from fastapi.testclient import TestClient

    app = create_app()
    db_file = tmp_path / "test_api.db"
    engine = make_engine(f"sqlite:///{db_file.as_posix()}")
    init_db(engine)
    # 在进入 TestClient（触发 lifespan）**之前**注入测试引擎：并行（xdist）下
    # 各 worker 若都让 lifespan 初始化默认库，ensure_demo_users 会对同一
    # data/ashare_lab.db 竞争写入（首次真实 CI 运行即暴露此竞态，TEST_REPORT §22）。
    app.state.engine = engine  # type: ignore[attr-defined]
    app.state.db_session_factory = make_session_factory(engine)  # type: ignore[attr-defined]
    ensure_demo_users(app.state.db_session_factory)  # type: ignore[attr-defined]
    with TestClient(app) as client:
        yield client
        engine.dispose()


def auth_headers(client, username: str = "researcher", password: str = "Demo12345!") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---- 行情构造工具（单元测试共享）----


def make_bars(security_id: str, start: dt.date, closes: list[float], *, suspended_days: set[dt.date] | None = None) -> list[Bar]:
    """由收盘价序列构造 Bar 列表；涨跌停按 ±10% 推导。"""
    suspended_days = suspended_days or set()
    bars: list[Bar] = []
    prev = closes[0]
    for i, close in enumerate(closes):
        d = start + dt.timedelta(days=i)
        suspended = d in suspended_days
        limit_up = round(prev * 1.10, 2)
        limit_down = round(prev * 0.90, 2)
        bars.append(
            Bar(
                security_id=security_id,
                trade_date=d,
                open=prev,
                high=round(close * 1.01, 2),
                low=round(close * 0.99, 2),
                close=close,
                pre_close=prev,
                limit_up=limit_up,
                limit_down=limit_down,
                suspended=suspended,
            )
        )
        if not suspended:
            prev = close
    return bars


@pytest.fixture()
def mini_book() -> PriceBook:
    return PriceBook(make_bars("S1", dt.date(2026, 3, 2), [10.0, 10.5, 11.0, 10.8, 11.5, 12.0, 12.3, 12.1, 12.6, 13.0]))


@pytest.fixture()
def cal() -> TradingCalendar:
    return TradingCalendar()


@pytest.fixture(scope="session")
def synthetic_universe():
    return generate_universe(seed=7, n_events=20, n_days=60)
