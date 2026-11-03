"""首位公司管理员初始化。

依据 docs/04-认证、会话与凭证安全.md 5.2 与 docs/13-Docker部署与运维.md 10.4：
仅当数据库没有任何用户时读取初始化变量并创建首位公司管理员；否则忽略。
检查、创建和写入在同一 ``BEGIN IMMEDIATE`` 事务内完成，重试幂等。
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api_keys import (
    REVOKE_REASON_ACCOUNT_DISABLED,
    REVOKE_REASON_ROLE_CHANGED,
    revoke_all_api_keys,
)
from app.auth.login_names import validate_display_name, validate_login_name
from app.auth.passwords import hash_password
from app.auth.sessions import revoke_all_sessions
from app.config import Settings
from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.modules.users.models import Role, User, UserStatus


def bootstrap_initial_admin(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> User | None:
    """空库时创建首位公司管理员；已有用户时返回 None。"""
    if settings.bootstrap_admin_username is None or settings.bootstrap_admin_password is None:
        return None

    created_at = now or utc_now()

    with session_factory() as session, begin_immediate(session):
        user_count = session.scalar(select(func.count()).select_from(User))
        if int(user_count or 0) > 0:
            return None

        login_name = validate_login_name(
            settings.bootstrap_admin_username, field="BOOTSTRAP_ADMIN_USERNAME"
        )
        display_name = validate_display_name(
            settings.bootstrap_admin_username, field="BOOTSTRAP_ADMIN_USERNAME"
        )
        user = User(
            login_name=login_name,
            password_hash=hash_password(settings.bootstrap_admin_password),
            display_name=display_name,
            role=Role.COMPANY_ADMIN.value,
            status=UserStatus.ACTIVE.value,
            force_password_change=True,
            password_version=1,
            version=1,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(user)
        session.flush()
        session.expunge(user)
        return user


def apply_user_state_change(
    session: Session,
    user: User,
    *,
    role: Role | None = None,
    status: UserStatus | None = None,
    now: datetime | None = None,
) -> User:
    """在调用方 ``BEGIN IMMEDIATE`` 事务内修改角色或状态并撤销全部凭证。

    角色变化和禁用都会使既有会话与 API Key 立即失效；最后管理员保护由
    P3 的调用方在同一事务内先完成统计与校验。未变化时不重复写审计或撤销。
    """
    changed_at = now or utc_now()
    changed = False
    revoked_reason: str | None = None

    if role is not None and user.role != role.value:
        user.role = role.value
        changed = True
        revoked_reason = REVOKE_REASON_ROLE_CHANGED
    if status is not None and user.status != status.value:
        user.status = status.value
        changed = True
        revoked_reason = REVOKE_REASON_ACCOUNT_DISABLED

    if not changed:
        return user

    user.version += 1
    user.updated_at = changed_at
    revoke_all_sessions(session, user.id, changed_at)
    revoke_all_api_keys(session, user.id, revoked_reason or REVOKE_REASON_ROLE_CHANGED, changed_at)
    session.flush()
    return user
