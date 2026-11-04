"""审计查询页面与备份管理页面。"""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.listing import ListQuery
from app.db.database import get_session
from app.errors import AppError
from app.modules.audit.schemas import AuditLogEntry
from app.modules.audit.service import audit_payload, list_audit_logs
from app.modules.backups.service import create_backup, list_backups
from app.modules.inventory.service import local_day_bounds
from app.pages import common
from app.pages.dependencies import PageAdmin, PageAdminForm
from app.pages.templating import flash, redirect, render
from app.request_context import get_request_id

router = APIRouter(include_in_schema=False)

ENTITY_TYPES = [
    ("user", "用户"),
    ("session", "会话"),
    ("api_key", "API Key"),
    ("warehouse", "仓库"),
    ("department", "部门"),
    ("category", "分类"),
    ("material", "物资"),
    ("project", "项目"),
    ("manufacturer", "厂家"),
    ("document", "单据"),
    ("backup", "备份"),
]
SOURCES = [("WEB", "页面"), ("API", "API"), ("MCP", "MCP")]


@router.get("/audit")
def audit_page(
    request: Request,
    auth: PageAdmin,
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
) -> Response:
    query = request.query_params
    start_date = _date_or_none(query.get("start_date"))
    end_date = _date_or_none(query.get("end_date"))
    start_at, end_before = local_day_bounds(
        start_date, end_date, request.app.state.settings.timezone
    )
    created_to = end_before - timedelta(microseconds=1) if end_before else None
    entity_type = (query.get("entity_type") or "").strip() or None
    action = (query.get("action") or "").strip() or None
    source = (query.get("source") or "").strip() or None
    rows, total = list_audit_logs(
        session,
        entity_type=entity_type,
        action=action,
        source=source,
        created_from=start_at,
        created_to=created_to,
        page=params.page,
        page_size=params.page_size,
    )
    users = {user.id: user.display_name for user in common.user_options(session)}
    entries = [
        AuditLogEntry.model_validate(audit_payload(row)).model_dump(mode="python") for row in rows
    ]
    for entry in entries:
        entry["actor_name"] = users.get(entry["actor_user_id"] or "", entry["actor_user_id"])
    return render(
        request,
        "audit/list.html",
        auth=auth,
        entries=entries,
        total=total,
        page=params.page,
        page_size=params.page_size,
        entity_types=ENTITY_TYPES,
        sources=SOURCES,
        filters={
            "entity_type": entity_type or "",
            "action": action or "",
            "source": source or "",
            "start_date": query.get("start_date") or "",
            "end_date": query.get("end_date") or "",
        },
    )


@router.get("/backups")
def backups_page(
    request: Request,
    auth: PageAdmin,
    params: ListQuery,
) -> Response:
    items, total = list_backups(
        request.app.state.settings,
        page=params.page,
        page_size=params.page_size,
        sort=params.sort,
        order=params.order,
    )
    return render(
        request,
        "backups/list.html",
        auth=auth,
        items=items,
        total=total,
        page=params.page,
        page_size=params.page_size,
        retention=request.app.state.settings.backup_retention_sets,
        error=None,
    )


@router.post("/backups")
async def create_backup_page(
    request: Request,
    auth: PageAdminForm,
    params: ListQuery,
) -> Response:
    try:
        info = create_backup(
            request.app.state.settings,
            request.app.state.runtime.session_factory,
            actor_user_id=auth.user.id,
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        items, total = list_backups(
            request.app.state.settings,
            page=params.page,
            page_size=params.page_size,
            sort=params.sort,
            order=params.order,
        )
        return render(
            request,
            "backups/list.html",
            auth=auth,
            status_code=exc.status_code,
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
            retention=request.app.state.settings.backup_retention_sets,
            error=common.error_view(exc, get_request_id() or ""),
        )
    flash(auth, f"备份集 {info.id} 已成功创建", detail="完整性校验通过，已计入保留集。")
    return redirect("/backups")


def _date_or_none(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
