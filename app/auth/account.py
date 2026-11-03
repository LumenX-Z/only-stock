"""登录会话与密码生命周期。

依据 docs/04-认证、会话与凭证安全.md 5.2、5.4 与 docs/03-角色、权限与账户
生命周期.md 4.3：
- 登录在写事务内复核密码哈希后建立会话，避免与改密、重置并发；
- 本人改密成功后清除强制改密标记、撤销其他会话和全部 API Key，轮换当前会话；
- 管理员重置密码撤销全部会话和 Key、设置强制改密，响应不回显密码。
以上写入均在同一 ``BEGIN IMMEDIATE`` 事务内完成。
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api_keys import (
    REVOKE_REASON_PASSWORD_CHANGED,
    REVOKE_REASON_PASSWORD_RESET,
    revoke_all_api_keys,
)
from app.auth.models import AuthSession
from app.auth.passwords import (
    hash_password,
    validate_password,
    verify_dummy_password,
    verify_password,
)
from app.auth.sessions import (
    SessionCredentials,
    create_session,
    hash_secret,
    is_session_valid,
    purge_expired_sessions,
    revoke_all_sessions,
    revoke_other_sessions,
    revoke_session,
)
from app.config import Settings
from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.errors import AppError
from app.modules.audit.service import record_audit
from app.modules.users.models import User, UserStatus


def authenticate_and_create_session(
    session_factory: sessionmaker[Session],
    settings: Settings,
    login_name: str,
    password: str,
    *,
    now: datetime | None = None,
) -> tuple[SessionCredentials, User]:
    """校验密码并在写事务内建立会话。

    Argon2 校验在写锁外完成；取得 ``BEGIN IMMEDIATE`` 后重新读取用户并比较
    密码哈希，避免与改密、重置密码并发时用旧哈希建立仍有效的会话。
    """
    with session_factory() as lookup:
        user = lookup.scalar(select(User).where(User.login_name == login_name))
        if user is None:
            verify_dummy_password(password)
            raise _invalid_credentials()
        user_id = user.id
        stored_hash = user.password_hash
        status = user.status

    if not verify_password(stored_hash, password):
        raise _invalid_credentials()
    if status != UserStatus.ACTIVE.value:
        raise _account_disabled()

    authenticated_at = now or utc_now()
    with session_factory() as session, begin_immediate(session):
        user = session.get(User, user_id)
        if user is None or user.password_hash != stored_hash:
            raise _invalid_credentials()
        if user.status != UserStatus.ACTIVE.value:
            raise _account_disabled()

        purge_expired_sessions(session, authenticated_at)
        credentials = create_session(session, user, settings, now=authenticated_at)
        session.flush()
        session.expunge(user)
        session.expunge(credentials.session)
        return credentials, user


def _invalid_credentials() -> AppError:
    return AppError(
        "INVALID_CREDENTIALS",
        "登录名或密码不正确",
        status_code=401,
        resource="login",
    )


def _account_disabled() -> AppError:
    return AppError(
        "ACCOUNT_DISABLED",
        "账户不可用",
        status_code=403,
        resource="user",
    )


def change_password(
    session_factory: sessionmaker[Session],
    settings: Settings,
    raw_session_token: str,
    old_password: str,
    new_password: str,
    *,
    now: datetime | None = None,
) -> tuple[SessionCredentials, User]:
    validate_password(new_password, field="new_password")
    changed_at = now or utc_now()

    with session_factory() as session, begin_immediate(session):
        current = session.scalar(
            select(AuthSession).where(AuthSession.id_hash == hash_secret(raw_session_token))
        )
        if current is None or not is_session_valid(current, settings, changed_at):
            raise AppError(
                "UNAUTHORIZED",
                "未认证或认证已失效",
                status_code=401,
                resource="session",
            )
        user = session.get(User, current.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise AppError(
                "ACCOUNT_DISABLED",
                "账户不可用",
                status_code=403,
                resource="user",
            )
        if not verify_password(user.password_hash, old_password):
            raise AppError(
                "INVALID_OLD_PASSWORD",
                "原密码不正确",
                status_code=400,
                resource="user",
            )

        user.password_hash = hash_password(new_password)
        user.force_password_change = False
        user.password_version += 1
        user.version += 1
        user.updated_at = changed_at

        revoke_other_sessions(session, user.id, current.id_hash, changed_at)
        revoke_session(current, changed_at)
        revoke_all_api_keys(session, user.id, REVOKE_REASON_PASSWORD_CHANGED, changed_at)
        credentials = create_session(session, user, settings, now=changed_at)
        session.flush()
        session.expunge(user)
        session.expunge(credentials.session)
        return credentials, user


def reset_password(
    session_factory: sessionmaker[Session],
    target_user_id: str,
    new_password: str,
    *,
    now: datetime | None = None,
    actor_user_id: str | None = None,
    request_id: str | None = None,
    source: str = "API",
) -> User:
    validate_password(new_password, field="new_password")
    reset_at = now or utc_now()

    with session_factory() as session, begin_immediate(session):
        user = session.get(User, target_user_id)
        if user is None:
            raise AppError(
                "USER_NOT_FOUND",
                "用户不存在",
                status_code=404,
                resource="user",
            )
        user.password_hash = hash_password(new_password)
        user.force_password_change = True
        user.password_version += 1
        user.version += 1
        user.updated_at = reset_at

        revoke_all_sessions(session, user.id, reset_at)
        revoke_all_api_keys(session, user.id, REVOKE_REASON_PASSWORD_RESET, reset_at)
        record_audit(
            session,
            actor_user_id=actor_user_id,
            action="PASSWORD_RESET",
            entity_type="user",
            entity_id=user.id,
            after={"id": user.id, "force_password_change": True, "version": user.version},
            request_id=request_id,
            source=source,
            now=reset_at,
        )
        session.flush()
        session.expunge(user)
        return user


def revoke_credentials_for_user(
    session_factory: sessionmaker[Session],
    user_id: str,
    reason: str,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """角色变化或禁用时撤销该用户全部会话与 API Key。

    P3 的用户状态事务应在同一 ``BEGIN IMMEDIATE`` 内直接调用
    ``revoke_all_sessions`` 与 ``revoke_all_api_keys``，本函数供独立场景使用。
    """
    revoked_at = now or utc_now()
    with session_factory() as session, begin_immediate(session):
        sessions = revoke_all_sessions(session, user_id, revoked_at)
        keys = revoke_all_api_keys(session, user_id, reason, revoked_at)
        return sessions, keys
