"""API Key 的创建、查询、吊销与校验。

依据 docs/04-认证、会话与凭证安全.md 5.5：
- 完整 Key 由可识别前缀和至少 256 位随机数组成，只在创建成功响应中返回一次；
- 数据库只保存 SHA-256 哈希、末四位和元数据；
- 每用户最多 10 把未吊销 Key；吊销幂等；
- 改密、重置、角色变化和禁用时在用户状态事务内撤销全部有效 Key。
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.api.listing import ListParams, SortOrder, order_by, resolve_sort
from app.auth.models import API_KEY_NAME_MAX_LENGTH, ApiKey
from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.errors import AppError
from app.modules.audit.service import record_audit
from app.modules.users.models import User, UserStatus

API_KEY_PREFIX = "osk_"
API_KEY_RANDOM_BYTES = 32
MAX_ACTIVE_API_KEYS = 10
REVOKE_REASON_USER = "USER_REVOKED"
REVOKE_REASON_PASSWORD_CHANGED = "PASSWORD_CHANGED"
REVOKE_REASON_PASSWORD_RESET = "PASSWORD_RESET"
REVOKE_REASON_ROLE_CHANGED = "ROLE_CHANGED"
REVOKE_REASON_ACCOUNT_DISABLED = "ACCOUNT_DISABLED"


@dataclass(frozen=True, slots=True)
class ApiKeyIdentity:
    user: User
    api_key: ApiKey


def generate_api_key() -> tuple[str, str, str]:
    """返回 (完整 Key, SHA-256 哈希, 末四位)。"""
    raw_key = API_KEY_PREFIX + secrets.token_urlsafe(API_KEY_RANDOM_BYTES)
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return raw_key, key_hash, raw_key[-4:]


def validate_api_key_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        reason = "用途名称不能为空"
    elif len(normalized) > API_KEY_NAME_MAX_LENGTH:
        reason = f"用途名称不能超过 {API_KEY_NAME_MAX_LENGTH} 位"
    else:
        return normalized
    raise AppError(
        "VALIDATION_ERROR",
        "请求参数校验失败",
        status_code=422,
        items=[{"field": "name", "reason": reason}],
    )


def create_api_key(
    session_factory: sessionmaker[Session],
    user_id: str,
    name: str,
    *,
    now: datetime | None = None,
    request_id: str | None = None,
    source: str = "API",
) -> tuple[ApiKey, str]:
    """在写事务内创建 Key；完整明文只返回给调用方一次。"""
    normalized_name = validate_api_key_name(name)
    created_at = now or utc_now()
    with session_factory() as session, begin_immediate(session):
        user = session.get(User, user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise AppError(
                "ACCOUNT_DISABLED",
                "账户不可用",
                status_code=403,
                resource="user",
            )
        active_count = session.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        )
        if int(active_count or 0) >= MAX_ACTIVE_API_KEYS:
            raise AppError(
                "API_KEY_LIMIT_REACHED",
                f"每名用户最多 {MAX_ACTIVE_API_KEYS} 把有效 API Key，请先吊销",
                status_code=409,
                resource="api_key",
                items=[{"limit": MAX_ACTIVE_API_KEYS}],
            )
        raw_key, key_hash, key_suffix = generate_api_key()
        row = ApiKey(
            user_id=user_id,
            name=normalized_name,
            key_hash=key_hash,
            key_suffix=key_suffix,
            created_at=created_at,
        )
        session.add(row)
        session.flush()
        record_audit(
            session,
            actor_user_id=user_id,
            action="API_KEY_CREATED",
            entity_type="api_key",
            entity_id=row.id,
            after={
                "id": row.id,
                "name": row.name,
                "key_suffix": row.key_suffix,
                "revoked_at": None,
            },
            request_id=request_id,
            source=source,
            now=created_at,
        )
        session.expunge(row)
        return row, raw_key


def list_api_keys(
    session: Session,
    user_id: str,
    *,
    include_revoked: bool,
    page: int,
    page_size: int,
    sort: str | None = None,
    order: SortOrder | None = None,
) -> tuple[list[ApiKey], int]:
    conditions = [ApiKey.user_id == user_id]
    if not include_revoked:
        conditions.append(ApiKey.revoked_at.is_(None))
    params = ListParams(page=page, page_size=page_size, sort=sort, order=order)
    column, direction = resolve_sort(
        params,
        fields={
            "name": ApiKey.name,
            "created_at": ApiKey.created_at,
            "last_used_at": ApiKey.last_used_at,
            "revoked_at": ApiKey.revoked_at,
        },
        default="created_at",
        default_order=SortOrder.DESC,
        resource="api_key",
    )
    total = session.scalar(select(func.count()).select_from(ApiKey).where(*conditions))
    rows = list(
        session.scalars(
            select(ApiKey)
            .where(*conditions)
            .order_by(order_by(column, direction), ApiKey.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return rows, int(total or 0)


def revoke_api_key(
    session_factory: sessionmaker[Session],
    user_id: str,
    key_id: str,
    *,
    now: datetime | None = None,
    request_id: str | None = None,
    source: str = "API",
) -> ApiKey:
    """吊销本人 Key；已吊销时幂等返回当前记录。"""
    revoked_at = now or utc_now()
    with session_factory() as session, begin_immediate(session):
        row = session.get(ApiKey, key_id)
        if row is None or row.user_id != user_id:
            raise AppError(
                "API_KEY_NOT_FOUND",
                "API Key 不存在",
                status_code=404,
                resource="api_key",
            )
        if row.revoked_at is None:
            row.revoked_at = revoked_at
            row.revoke_reason = REVOKE_REASON_USER
            session.flush()
            record_audit(
                session,
                actor_user_id=user_id,
                action="API_KEY_REVOKED",
                entity_type="api_key",
                entity_id=row.id,
                after={
                    "id": row.id,
                    "name": row.name,
                    "key_suffix": row.key_suffix,
                    "revoked_at": row.revoked_at.isoformat(),
                    "revoke_reason": row.revoke_reason,
                },
                request_id=request_id,
                source=source,
                now=revoked_at,
            )
        session.flush()
        session.expunge(row)
        return row


def revoke_all_api_keys(
    session: Session,
    user_id: str,
    reason: str,
    now: datetime,
) -> int:
    """在调用方事务内撤销该用户全部有效 Key。"""
    result = session.execute(
        update(ApiKey)
        .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=reason)
    )
    return int(result.rowcount or 0)


def revoke_all_api_keys_for_user(
    session_factory: sessionmaker[Session],
    user_id: str,
    reason: str,
    *,
    now: datetime | None = None,
) -> int:
    """独立写事务版本，供角色变化、禁用等用户状态事务调用方使用。"""
    revoked_at = now or utc_now()
    with session_factory() as session, begin_immediate(session):
        return revoke_all_api_keys(session, user_id, reason, revoked_at)


def authenticate_api_key(
    session: Session,
    raw_key: str,
    *,
    now: datetime | None = None,
) -> ApiKeyIdentity | None:
    """校验完整 Key；revoked_at 非空或账户非启用时立即失败。"""
    if not raw_key.startswith(API_KEY_PREFIX):
        return None
    computed_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    row = session.scalar(select(ApiKey).where(ApiKey.key_hash == computed_hash))
    if row is None or not hmac.compare_digest(row.key_hash, computed_hash):
        return None
    if row.revoked_at is not None:
        return None
    user = session.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        return None
    row.last_used_at = now or utc_now()
    session.flush()
    return ApiKeyIdentity(user=user, api_key=row)
