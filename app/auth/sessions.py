"""会话建立、认证、CSRF 与失效。

依据 docs/04-认证、会话与凭证安全.md 5.4：
- 会话 ID 使用至少 256 位密码学安全随机数，数据库只保存其哈希；
- CSRF Token 不落库，只保存哈希并随会话轮换；
- Cookie 为 HttpOnly、SameSite=Lax、Path=/，内网 HTTP 下不设置 Secure；
- 空闲 8 小时、绝对 24 小时；改密、重置、禁用或角色变化时失效。
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.auth.models import AuthSession
from app.config import Settings
from app.db.types import utc_now
from app.modules.users.models import User

SESSION_COOKIE_NAME = "only_stock_session"
CSRF_HEADER_NAME = "X-CSRF-Token"
SESSION_ID_BYTES = 32
SESSION_IDLE_REFRESH_SECONDS = 60
_CSRF_CONTEXT = b"only-stock-csrf-v1"


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    session: AuthSession
    raw_token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    session: AuthSession
    user: User
    raw_token: str
    csrf_token: str


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def derive_csrf_token(raw_session_token: str) -> str:
    """由会话密钥派生 CSRF Token；数据库只保存其哈希。"""
    return hmac.new(raw_session_token.encode("utf-8"), _CSRF_CONTEXT, hashlib.sha256).hexdigest()


def create_session(
    session: Session,
    user: User,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> SessionCredentials:
    """创建会话行；调用方负责提交事务。"""
    created_at = now or utc_now()
    raw_token = secrets.token_urlsafe(SESSION_ID_BYTES)
    csrf_token = derive_csrf_token(raw_token)
    row = AuthSession(
        id_hash=hash_secret(raw_token),
        user_id=user.id,
        csrf_hash=hash_secret(csrf_token),
        created_at=created_at,
        last_seen_at=created_at,
        absolute_expires_at=created_at + timedelta(hours=settings.session_absolute_hours),
    )
    session.add(row)
    session.flush()
    return SessionCredentials(session=row, raw_token=raw_token, csrf_token=csrf_token)


def authenticate_session(
    session: Session,
    raw_token: str,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> AuthenticatedSession | None:
    """校验会话；过期会话立即标记撤销并返回 None。"""
    current_time = now or utc_now()
    row = session.scalar(select(AuthSession).where(AuthSession.id_hash == hash_secret(raw_token)))
    if row is None or row.revoked_at is not None:
        return None
    if _is_expired(row, settings, current_time):
        row.revoked_at = current_time
        session.flush()
        return None

    user = session.get(User, row.user_id)
    if user is None:
        return None
    if (current_time - row.last_seen_at).total_seconds() >= SESSION_IDLE_REFRESH_SECONDS:
        row.last_seen_at = current_time
        session.flush()
    return AuthenticatedSession(
        session=row,
        user=user,
        raw_token=raw_token,
        csrf_token=derive_csrf_token(raw_token),
    )


def is_session_valid(row: AuthSession, settings: Settings, now: datetime) -> bool:
    return row.revoked_at is None and not _is_expired(row, settings, now)


def verify_csrf(row: AuthSession, submitted_token: str | None) -> bool:
    if not submitted_token:
        return False
    return hmac.compare_digest(row.csrf_hash, hash_secret(submitted_token))


def revoke_session(row: AuthSession, now: datetime) -> None:
    if row.revoked_at is None:
        row.revoked_at = now


def revoke_other_sessions(session: Session, user_id: str, keep_id_hash: str, now: datetime) -> int:
    result = session.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.id_hash != keep_id_hash,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    return int(result.rowcount or 0)


def revoke_all_sessions(session: Session, user_id: str, now: datetime) -> int:
    result = session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return int(result.rowcount or 0)


def purge_expired_sessions(session: Session, now: datetime) -> int:
    result = session.execute(delete(AuthSession).where(AuthSession.absolute_expires_at < now))
    return int(result.rowcount or 0)


def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,
    )


def _is_expired(row: AuthSession, settings: Settings, now: datetime) -> bool:
    idle_deadline = row.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    return row.absolute_expires_at <= now or idle_deadline <= now
