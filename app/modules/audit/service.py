"""只追加审计服务。

依据 docs/04-认证、会话与凭证安全.md 5.6 与 docs/09-HTTP-API通用约定与健康检查.md
8.1：审计记录操作者、时间、请求 ID、对象、动作、前后状态与来源。调用方负责把
审计写入与业务写入放在同一事务；本模块只提供插入与查询，不提供修改/删除。
"""

import json
from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.listing import ListParams, SortOrder, order_by, resolve_sort
from app.db.types import utc_now
from app.modules.audit.models import AuditLog

SOURCE_WEB = "WEB"
SOURCE_API = "API"
SOURCE_MCP = "MCP"
AUDIT_SOURCES = frozenset({SOURCE_WEB, SOURCE_API, SOURCE_MCP})


def record_audit(
    session: Session,
    *,
    actor_user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    before: Mapping[str, object] | None = None,
    after: Mapping[str, object] | None = None,
    request_id: str | None = None,
    source: str = SOURCE_API,
    api_key_id: str | None = None,
    now: datetime | None = None,
) -> AuditLog:
    """插入一条审计；不提交事务，由调用方决定边界。"""
    if source not in AUDIT_SOURCES:
        raise ValueError(f"未知审计来源：{source}")
    row = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=_dump(before),
        after_json=_dump(after),
        request_id=request_id,
        source=source,
        api_key_id=api_key_id,
        created_at=now or utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def list_audit_logs(
    session: Session,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    source: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    sort: str | None = None,
    order: SortOrder | None = None,
) -> tuple[list[AuditLog], int]:
    conditions = []
    if entity_type:
        conditions.append(AuditLog.entity_type == entity_type)
    if entity_id:
        conditions.append(AuditLog.entity_id == entity_id)
    if action:
        conditions.append(AuditLog.action == action)
    if source:
        conditions.append(AuditLog.source == source)
    if created_from is not None:
        conditions.append(AuditLog.created_at >= created_from)
    if created_to is not None:
        conditions.append(AuditLog.created_at <= created_to)

    params = ListParams(page=page, page_size=page_size, sort=sort, order=order)
    column, direction = resolve_sort(
        params,
        fields={
            "created_at": AuditLog.created_at,
            "action": AuditLog.action,
            "entity_type": AuditLog.entity_type,
            "source": AuditLog.source,
        },
        default="created_at",
        default_order=SortOrder.DESC,
        resource="audit_log",
    )
    total = session.scalar(select(func.count()).select_from(AuditLog).where(*conditions))
    rows = list(
        session.scalars(
            select(AuditLog)
            .where(*conditions)
            .order_by(order_by(column, direction), AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return rows, int(total or 0)


def audit_payload(row: AuditLog) -> dict[str, object]:
    """把审计行转换为 API 输出；JSON 字段还原为对象。"""
    return {
        "id": row.id,
        "actor_user_id": row.actor_user_id,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "before": _load(row.before_json),
        "after": _load(row.after_json),
        "request_id": row.request_id,
        "source": row.source,
        "api_key_id": row.api_key_id,
        "created_at": row.created_at,
    }


def _dump(value: Mapping[str, object] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)


def _load(value: str | None) -> object:
    if value is None:
        return None
    return json.loads(value)
