"""首次播种独立演示库，重复启动复用；拒绝把合成数据混入遗留研究库。"""

from __future__ import annotations

from asg_api.main import ensure_demo_users
from asg_api.settings import get_settings
from asg_connectors.synthetic import SyntheticConnector
from asg_domain.db import (
    BindingError,
    init_db,
    make_engine,
    make_session_factory,
    require_database_binding,
)
from asg_pipeline import seed_demo
from sqlalchemy.engine import make_url


def prepare_demo(database_url: str) -> dict:
    if make_url(database_url).get_backend_name() != "sqlite":
        raise ValueError("演示启动仅支持 SQLite；生产数据库请使用部署流程。")
    engine = make_engine(database_url)
    try:
        init_db(engine)
        sf = make_session_factory(engine)
        conn = SyntheticConnector(seed=20260907, n_events=130, n_days=180)
        with sf() as session:
            binding = require_database_binding(
                session, seed=conn.seed, generator_version=conn.generator_version
            )
            if binding is not None:
                return {"status": "reused", "seed": binding.seed}
        ensure_demo_users(sf)
        with sf() as session:
            stats = seed_demo(session, seed=20260907, n_events=130)
        return {"status": "seeded", "n_gold": stats["n_gold"], "kappa": stats["kappa"]}
    finally:
        engine.dispose()


def main() -> None:
    try:
        result = prepare_demo(get_settings().database_url)
    except BindingError as exc:
        raise SystemExit(
            f"演示库校验失败：{exc}\n"
            "保留旧库，Windows 使用 scripts/demo.ps1 -FreshDemo；"
            "Linux/macOS 使用 scripts/demo.sh --fresh-demo 创建独立新库。"
        ) from exc
    print("合成演示数据：", result)


if __name__ == "__main__":
    main()
