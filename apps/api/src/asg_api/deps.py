"""应用级组件：限流（令牌桶）、审计、公共依赖。"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from asg_domain.db import AuditLog, User
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from asg_api.security import role_can, verify_token

from .settings import get_settings


class RateLimiter:
    """每用户滑动窗口计数器（内存实现，单机定位——见 ADR-008 残余风险）。"""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            q = self._events[key]
            while q and q[0] < now - 60.0:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


class LoginGuard:
    """登录失败锁定（5 次失败锁 5 分钟）。"""

    def __init__(self, max_failures: int = 5, lock_seconds: int = 300) -> None:
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check_locked(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            fails = [t for t in self._failures.get(key, []) if t > now - self.lock_seconds]
            self._failures[key] = fails
            return len(fails) >= self.max_failures

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures[key].append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


_limiter = RateLimiter(get_settings().rate_limit_per_minute)


def rate_limit(request: Request) -> None:
    """每应用实例的限流（测试可注入收紧）。"""
    limiter: RateLimiter = getattr(request.app.state, "rate_limiter", None) or _limiter
    key = request.client.host if request.client else "anonymous"
    if not limiter.check(key):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


def login_guard_for(request: Request) -> LoginGuard:
    """每应用实例的登录锁定（避免跨实例共享状态）。"""
    guard: LoginGuard | None = getattr(request.app.state, "login_guard", None)
    if guard is None:
        guard = LoginGuard()
        request.app.state.login_guard = guard
    return guard


def write_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    target: str = "",
    request_id: str = "",
    result: str = "ok",
    detail: dict | None = None,
) -> None:
    session.add(AuditLog(actor=actor, action=action, target=target[:120], request_id=request_id, result=result, detail=detail or {}))
    session.commit()


def get_db(request: Request) -> Session:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    return db()


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未认证")
    token = authorization[len("Bearer ") :]
    payload = verify_token(get_settings().secret_key, token)
    if payload is None:
        raise HTTPException(status_code=401, detail="凭证无效或已过期")
    db: Session = get_db(request)
    user = db.get(User, payload.get("sub", ""))
    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return user


def require(capability: str):
    """依赖工厂：校验当前用户具备指定能力（RBAC）。"""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if not role_can(user.role, capability):
            raise HTTPException(status_code=403, detail=f"权限不足（需要 {capability}）")
        return user

    return _dep
