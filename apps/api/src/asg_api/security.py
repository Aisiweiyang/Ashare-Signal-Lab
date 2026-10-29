"""认证与 RBAC（ADR-008）：PBKDF2 口令哈希 + HMAC 签名 token + 角色能力表。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from asg_domain.enums import UserRole

PBKDF2_ITERATIONS = 120_000
TOKEN_TOLERANCE_SECONDS = 5


def hash_password(password: str, *, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return base64.b64encode(digest).decode(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return hmac.compare_digest(base64.b64encode(digest).decode(), password_hash)


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(secret_key: str, user_id: str, role: str, expires_in_minutes: int) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "exp": int(time.time()) + expires_in_minutes * 60,
        "jti": secrets.token_hex(8),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(secret_key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(secret_key: str, token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        expect = _b64e(hmac.new(secret_key.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("exp", 0) + TOKEN_TOLERANCE_SECONDS < time.time():
            return None
        return payload
    except Exception:
        return None


# ---- RBAC 能力表（THREAT_MODEL T1/T2 的唯一事实源）----
# capability -> 允许的最低角色集合
CAPABILITIES: dict[str, set[str]] = {
    "texts.read": {UserRole.VIEWER, UserRole.ANNOTATOR, UserRole.RESEARCHER, UserRole.ADJUDICATOR, UserRole.ADMIN},
    "labeling.annotate": {UserRole.ANNOTATOR, UserRole.RESEARCHER, UserRole.ADJUDICATOR, UserRole.ADMIN},
    "labeling.adjudicate": {UserRole.ADJUDICATOR, UserRole.ADMIN},
    "labeling.agreement": {UserRole.RESEARCHER, UserRole.ADJUDICATOR, UserRole.ADMIN},
    "data.import": {UserRole.RESEARCHER, UserRole.ADMIN},
    "experiments.run": {UserRole.RESEARCHER, UserRole.ADMIN},
    "experiments.read": {UserRole.VIEWER, UserRole.ANNOTATOR, UserRole.RESEARCHER, UserRole.ADJUDICATOR, UserRole.ADMIN},
    "export.data": {UserRole.RESEARCHER, UserRole.ADMIN},
    "admin.users": {UserRole.ADMIN},
    "admin.audit": {UserRole.ADMIN},
}


def role_can(role: str, capability: str) -> bool:
    allowed = CAPABILITIES.get(capability, set())
    return role in {r.value for r in allowed}
