"""认证路由：登录（含失败锁定）、当前用户信息。"""

from __future__ import annotations

from asg_domain.db import User
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from asg_api.deps import get_current_user, get_db, login_guard_for, rate_limit, write_audit
from asg_api.security import issue_token, verify_password
from asg_api.settings import get_settings

router = APIRouter(prefix="/api/auth", tags=["认证"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    display_name: str


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit)])
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    guard = login_guard_for(request)
    key = body.username
    if guard.check_locked(key):
        write_audit(db, actor=key, action="login", result="locked", request_id=request.headers.get("X-Request-ID", ""))
        raise HTTPException(status_code=423, detail="失败次数过多，账户临时锁定")
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not user.active or not verify_password(body.password, user.password_hash, user.password_salt):
        guard.record_failure(key)
        # 统一错误信息，不区分“用户不存在/密码错误”
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    guard.reset(key)
    settings = get_settings()
    token = issue_token(settings.secret_key, user.user_id, user.role, settings.access_token_expire_minutes)
    write_audit(db, actor=user.username, action="login", result="ok", request_id=request.headers.get("X-Request-ID", ""))
    return TokenResponse(access_token=token, role=user.role, display_name=user.display_name)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username, "role": user.role, "display_name": user.display_name}
