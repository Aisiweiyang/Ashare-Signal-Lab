"""FastAPI 应用：中间件（CSP/请求ID）、路由装配、启动初始化与演示用户。"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from asg_domain.db import User, init_db, make_engine, make_session_factory
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from asg_api.security import hash_password

from .settings import get_settings

DEMO_PASSWORD = "Demo12345!"


def ensure_demo_users(session_factory) -> list[str]:
    """创建演示用户（已存在则跳过）。返回创建的用户名列表。"""
    created: list[str] = []
    demo = [
        ("admin", "admin", "系统管理员"),
        ("researcher", "researcher", "研究员"),
        ("adjudicator", "adjudicator", "仲裁员"),
        ("annotator_a", "annotator", "标注员A"),
        ("annotator_b", "annotator", "标注员B"),
        ("viewer", "viewer", "访客"),
    ]
    with session_factory() as s:
        for username, role, display in demo:
            if s.execute(select(User).where(User.username == username)).scalar_one_or_none() is not None:
                continue
            pw_hash, salt = hash_password(DEMO_PASSWORD)
            s.add(User(username=username, password_hash=pw_hash, password_salt=salt, role=role, display_name=display))
            created.append(username)
        s.commit()
    return created


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if getattr(app.state, "db_session_factory", None) is not None:
        # 测试（tests/e2e live_server）预先注入了独立引擎：不覆盖，服务直接使用注入库。
        # ADR-014 绑定校验上线后，「实验静默落到另一个库」不再是无害行为——
        # 旧版 lifespan 的覆盖曾让 E2E 在默认演示库上运行（绑定机制使其现形）。
        yield
        return
    engine = make_engine(settings.database_url)
    init_db(engine)
    app.state.engine = engine
    app.state.db_session_factory = make_session_factory(engine)
    created = ensure_demo_users(app.state.db_session_factory)
    if created:
        print(f"[启动] 已创建演示用户: {', '.join(created)}；初始密码 {DEMO_PASSWORD}（生产环境必须修改）")
    # 连接器插件发现（ADR-011）：失败隔离，单个插件损坏不影响服务启动
    from asg_connectors import REGISTRY

    for line in REGISTRY.discover_plugins():
        print(f"[连接器] {line}")
    yield
    engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AshareSignal Lab API",
        version="0.1.0",
        description="面向中文 A 股的细粒度金融情绪研究与短期市场反应系统（研究用途，非投资建议）",
        lifespan=lifespan,
    )
    # 每实例独立的限流器与登录锁定（测试隔离也依赖这一点）
    from .deps import LoginGuard, RateLimiter, rate_limit

    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.login_guard = LoginGuard()
    # 全局限流：所有路由（含认证）统一限流（THREAT_MODEL 限流与滥用）
    app.router.dependencies = [Depends(rate_limit)]

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Request-ID"] = request_id
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite dev
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # 不向客户端泄漏内部细节
        return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})

    from .routers import admin, analysis, auth, data, experiments, export, labeling, texts

    for router in (auth.router, texts.router, labeling.router, experiments.router, data.router, admin.router, export.router, analysis.router):
        app.include_router(router)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "asharesignal-lab", "research_only": True, "real_trading_disabled": True}

    # 托管前端构建产物（apps/web/dist 存在时），单命令演示用。
    # 必须在所有 API 路由之后注册：挂载在 "/"，仅作兜底。
    from pathlib import Path

    web_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if web_dist.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")

    return app


app = create_app()
