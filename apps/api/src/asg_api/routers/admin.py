"""管理路由：用户管理、审计日志查询（仅 admin）。"""

from __future__ import annotations

from asg_domain.db import AuditLog, User
from asg_domain.enums import UserRole
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from asg_api.deps import get_db, require, write_audit
from asg_api.security import hash_password

router = APIRouter(prefix="/api/admin", tags=["管理"])


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern="^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=8, max_length=64)
    role: str
    display_name: str = ""


@router.get("/users")
def list_users(user=Depends(require("admin.users")), db: Session = Depends(get_db)):
    rows = db.execute(select(User)).scalars().all()
    return {"items": [{"username": u.username, "role": u.role, "display_name": u.display_name, "active": u.active} for u in rows]}


@router.post("/users")
def create_user(body: CreateUserRequest, user=Depends(require("admin.users")), db: Session = Depends(get_db)):
    if body.role not in {r.value for r in UserRole}:
        raise HTTPException(status_code=400, detail="非法角色")
    if db.execute(select(User).where(User.username == body.username)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    pw_hash, salt = hash_password(body.password)
    u = User(username=body.username, password_hash=pw_hash, password_salt=salt, role=body.role, display_name=body.display_name)
    db.add(u)
    db.commit()
    write_audit(db, actor=user.username, action="admin.user_create", target=body.username)
    return {"username": u.username, "role": u.role}


@router.get("/audit")
def audit_logs(limit: int = 200, user=Depends(require("admin.audit")), db: Session = Depends(get_db)):
    rows = db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 1000))).scalars().all()
    return {
        "items": [
            {
                "actor": r.actor,
                "action": r.action,
                "target": r.target,
                "result": r.result,
                "request_id": r.request_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }
