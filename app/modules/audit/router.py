"""审计查询接口。

依据 docs/10-账户、基础资料与系统维护API.md 8.4：仅公司管理员可按对象、动作和
时间查询审计；审计只追加，不提供修改或删除接口。
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.listing import ListQuery
from app.api.responses import COMMON_ERROR_RESPONSES
from app.auth.permissions import CompanyAdmin
from app.db.database import get_session
from app.modules.audit.schemas import AuditLogEntry, AuditLogListResponse
from app.modules.audit.service import audit_payload, list_audit_logs

router = APIRouter(
    prefix="/api/v1/audit-logs",
    tags=["audit"],
    responses=COMMON_ERROR_RESPONSES,
)


@router.get("", response_model=AuditLogListResponse)
def get_audit_logs(
    _auth: CompanyAdmin,
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    source: Literal["WEB", "API", "MCP"] | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> AuditLogListResponse:
    rows, total = list_audit_logs(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        source=source,
        created_from=_as_utc(created_from),
        created_to=_as_utc(created_to),
        page=params.page,
        page_size=params.page_size,
        sort=params.sort,
        order=params.order,
    )
    return AuditLogListResponse(
        items=[AuditLogEntry.model_validate(audit_payload(row)) for row in rows],
        page=params.page,
        page_size=params.page_size,
        total=total,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    """查询时间按 UTC 解释；``UtcDateTime`` 拒绝无时区时间。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
