"""用户生命周期与首位公司管理员初始化。

依据 docs/04-认证、会话与凭证安全.md 5.2、5.4 与 docs/03-角色、权限与账户
生命周期.md 4.3：
- 空库首次启动按初始化变量创建首位公司管理员，重复启动幂等；
- 管理员可创建用户、改显示名/角色、禁用和重置密码；
- 至少保留一个启用公司管理员，角色/禁用变更在 ``BEGIN IMMEDIATE`` 内重新统计；
- 角色变化与禁用立即撤销会话和全部 API Key，全部写入与审计同事务。
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.listing import ListParams, SortOrder, order_by, resolve_sort
from app.auth.api_keys import (
    REVOKE_REASON_ACCOUNT_DISABLED,
    REVOKE_REASON_ROLE_CHANGED,
    revoke_all_api_keys,
)
from app.auth.login_names import validate_display_name, validate_login_name
from app.auth.passwords import hash_password, validate_password
from app.auth.sessions import revoke_all_sessions
from app.config import Settings
from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.errors import AppError
from app.modules.audit.service import record_audit
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
        record_audit(
            session,
            actor_user_id=None,
            action="SYSTEM_INITIALIZED",
            entity_type="user",
            entity_id=user.id,
            after=user_snapshot(user),
            source="API",
            now=created_at,
        )
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
    """在调用方 ``BEGIN IMMEDIATE`` 事务内修改角色或状态并撤销全部凭证。"""
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


def create_user(
    session_factory: sessionmaker[Session],
    actor_user_id: str | None,
    *,
    login_name: str,
    display_name: str,
    role: str,
    initial_password: str,
    request_id: str | None = None,
    source: str = "API",
) -> User:
    """创建公司管理员或操作员；初始密码首次登录必须修改。"""
    normalized_login = validate_login_name(login_name)
    normalized_display = validate_display_name(display_name)
    _validate_role(role)
    validate_password(initial_password, field="initial_password")
    created_at = utc_now()

    try:
        with session_factory() as session, begin_immediate(session):
            existing = session.scalar(select(User.id).where(User.login_name == normalized_login))
            if existing is not None:
                raise _login_conflict(normalized_login)
            user = User(
                login_name=normalized_login,
                password_hash=hash_password(initial_password),
                display_name=normalized_display,
                role=role,
                status=UserStatus.ACTIVE.value,
                force_password_change=True,
                password_version=1,
                version=1,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(user)
            session.flush()
            record_audit(
                session,
                actor_user_id=actor_user_id,
                action="USER_CREATED",
                entity_type="user",
                entity_id=user.id,
                after=user_snapshot(user),
                request_id=request_id,
                source=source,
                now=created_at,
            )
            session.expunge(user)
            return user
    except IntegrityError as exc:
        if "users.login_name" in str(exc.orig):
            raise _login_conflict(normalized_login) from exc
        raise


def update_user(
    session_factory: sessionmaker[Session],
    actor_user_id: str | None,
    target_user_id: str,
    *,
    display_name: str | None = None,
    role: str | None = None,
    version: int | None = None,
    request_id: str | None = None,
    source: str = "API",
) -> User:
    """修改显示名或角色；角色变化撤销目标用户全部凭证并保护最后管理员。"""
    if role is not None:
        _validate_role(role)
    changed_at = utc_now()

    with session_factory() as session, begin_immediate(session):
        user = _require_user(session, target_user_id)
        if version is not None and user.version != version:
            raise _version_conflict()

        before = user_snapshot(user)
        display_changed = False
        role_changed = False
        if display_name is not None:
            normalized_display = validate_display_name(display_name)
            if user.display_name != normalized_display:
                user.display_name = normalized_display
                display_changed = True
        if role is not None and user.role != role:
            if (
                user.role == Role.COMPANY_ADMIN.value
                and user.status == UserStatus.ACTIVE.value
                and role != Role.COMPANY_ADMIN.value
                and _active_admin_count(session) <= 1
            ):
                raise _last_admin_error()
            apply_user_state_change(session, user, role=Role(role), now=changed_at)
            role_changed = True

        if not display_changed and not role_changed:
            session.expunge(user)
            return user
        if display_changed and not role_changed:
            user.version += 1
            user.updated_at = changed_at
            session.flush()

        record_audit(
            session,
            actor_user_id=actor_user_id,
            action="USER_UPDATED",
            entity_type="user",
            entity_id=user.id,
            before=before,
            after=user_snapshot(user),
            request_id=request_id,
            source=source,
            now=changed_at,
        )
        session.expunge(user)
        return user


def disable_user(
    session_factory: sessionmaker[Session],
    actor_user_id: str | None,
    target_user_id: str,
    *,
    version: int | None = None,
    request_id: str | None = None,
    source: str = "API",
) -> User:
    """禁用用户并撤销全部会话/API Key；最后一名启用管理员不可禁用。"""
    changed_at = utc_now()

    with session_factory() as session, begin_immediate(session):
        user = _require_user(session, target_user_id)
        if version is not None and user.version != version:
            raise _version_conflict()
        if user.status == UserStatus.DISABLED.value:
            session.expunge(user)
            return user

        if (
            user.role == Role.COMPANY_ADMIN.value
            and user.status == UserStatus.ACTIVE.value
            and _active_admin_count(session) <= 1
        ):
            raise _last_admin_error()

        before = user_snapshot(user)
        apply_user_state_change(session, user, status=UserStatus.DISABLED, now=changed_at)
        record_audit(
            session,
            actor_user_id=actor_user_id,
            action="USER_DISABLED",
            entity_type="user",
            entity_id=user.id,
            before=before,
            after=user_snapshot(user),
            request_id=request_id,
            source=source,
            now=changed_at,
        )
        session.expunge(user)
        return user


def list_users(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    role: str | None = None,
    status: str | None = None,
    login_name: str | None = None,
    sort: str | None = None,
    order: SortOrder | None = None,
) -> tuple[list[User], int]:
    conditions = []
    if role is not None:
        conditions.append(User.role == role)
    if status is not None:
        conditions.append(User.status == status)
    if login_name:
        conditions.append(User.login_name == normalize_login_lookup(login_name))
    params = ListParams(page=page, page_size=page_size, sort=sort, order=order)
    column, direction = resolve_sort(
        params,
        fields={
            "login_name": User.login_name,
            "display_name": User.display_name,
            "role": User.role,
            "status": User.status,
            "created_at": User.created_at,
            "updated_at": User.updated_at,
        },
        default="created_at",
        default_order=SortOrder.ASC,
        resource="user",
    )
    total = session.scalar(select(func.count()).select_from(User).where(*conditions))
    rows = list(
        session.scalars(
            select(User)
            .where(*conditions)
            .order_by(order_by(column, direction), User.login_name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return rows, int(total or 0)


def normalize_login_lookup(login_name: str) -> str:
    return login_name.strip().casefold()


def user_snapshot(user: User) -> dict[str, object]:
    """审计用用户快照；不包含密码哈希、会话或 Key。"""
    return {
        "id": user.id,
        "login_name": user.login_name,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
        "force_password_change": bool(user.force_password_change),
        "version": user.version,
    }


def reset_user_password(
    session_factory: sessionmaker[Session],
    actor_user_id: str | None,
    target_user_id: str,
    new_password: str,
    *,
    request_id: str | None = None,
    source: str = "API",
    now: datetime | None = None,
) -> User:
    """管理员重置密码并写审计；响应不回显密码。"""
    from app.auth.account import reset_password

    reset_at = now or utc_now()
    return reset_password(
        session_factory,
        target_user_id,
        new_password,
        now=reset_at,
        actor_user_id=actor_user_id,
        request_id=request_id,
        source=source,
    )


def _validate_role(role: str) -> None:
    if role not in {member.value for member in Role}:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            items=[{"field": "role", "reason": "角色必须是 COMPANY_ADMIN 或 OPERATOR"}],
        )


def _active_admin_count(session: Session) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == Role.COMPANY_ADMIN.value, User.status == UserStatus.ACTIVE.value)
    )
    return int(count or 0)


def _require_user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise AppError(
            "USER_NOT_FOUND",
            "用户不存在",
            status_code=404,
            resource="user",
            items=[{"id": user_id}],
        )
    return user


def _login_conflict(login_name: str) -> AppError:
    return AppError(
        "LOGIN_NAME_CONFLICT",
        "登录名已被使用",
        status_code=409,
        resource="user",
        items=[{"login_name": login_name}],
    )


def _version_conflict() -> AppError:
    return AppError(
        "VERSION_CONFLICT",
        "数据已被其他操作修改，请刷新后重试",
        status_code=409,
        resource="user",
        items=[],
    )


def _last_admin_error() -> AppError:
    return AppError(
        "LAST_ADMIN_PROTECTED",
        "必须至少保留一名启用的公司管理员",
        status_code=409,
        resource="user",
        items=[{"role": Role.COMPANY_ADMIN.value}],
    )
