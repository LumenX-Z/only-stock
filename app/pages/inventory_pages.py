"""库存与流水页面。"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.listing import ListQuery
from app.db.database import get_session
from app.modules.inventory.service import (
    list_inventory,
    list_transactions,
    local_day_bounds,
)
from app.pages import common
from app.pages.dependencies import PageUser
from app.pages.templating import render

router = APIRouter(include_in_schema=False)


def _uuid_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


@router.get("/inventory")
def inventory_page(
    request: Request,
    auth: PageUser,
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
) -> Response:
    query = request.query_params
    warehouse_id = _uuid_or_none(query.get("warehouse_id"))
    material_id = _uuid_or_none(query.get("material_id"))
    rows, total = list_inventory(
        session,
        warehouse_id=warehouse_id,
        material_id=material_id,
        code=(query.get("code") or "").strip() or None,
        name=(query.get("name") or "").strip() or None,
        include_zero=query.get("include_zero") in {"1", "true", "on"},
        page=params.page,
        page_size=params.page_size,
    )
    return render(
        request,
        "inventory/list.html",
        auth=auth,
        items=rows,
        total=total,
        page=params.page,
        page_size=params.page_size,
        warehouses=common.select_options(session, "warehouse", include_inactive=True),
        materials=common.select_options(session, "material", include_inactive=True),
        categories=common.category_index(session),
        filters={
            "warehouse_id": warehouse_id or "",
            "material_id": material_id or "",
            "code": query.get("code") or "",
            "name": query.get("name") or "",
            "include_zero": query.get("include_zero") in {"1", "true", "on"},
        },
    )


@router.get("/inventory/transactions")
def transactions_page(
    request: Request,
    auth: PageUser,
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
) -> Response:
    query = request.query_params
    start_date = _date_or_none(query.get("start_date"))
    end_date = _date_or_none(query.get("end_date"))
    start_at, end_before = local_day_bounds(
        start_date, end_date, request.app.state.settings.timezone
    )
    rows, total = list_transactions(
        session,
        warehouse_id=_uuid_or_none(query.get("warehouse_id")),
        material_id=_uuid_or_none(query.get("material_id")),
        project_id=_uuid_or_none(query.get("project_id")),
        department_id=_uuid_or_none(query.get("department_id")),
        start_at=start_at,
        end_before=end_before,
        page=params.page,
        page_size=params.page_size,
    )
    return render(
        request,
        "inventory/transactions.html",
        auth=auth,
        items=rows,
        total=total,
        page=params.page,
        page_size=params.page_size,
        warehouses=common.select_options(session, "warehouse", include_inactive=True),
        materials=common.select_options(session, "material", include_inactive=True),
        projects=common.select_options(session, "project", include_inactive=True),
        departments=common.select_options(session, "department", include_inactive=True),
        filters={
            "warehouse_id": _uuid_or_none(query.get("warehouse_id")) or "",
            "material_id": _uuid_or_none(query.get("material_id")) or "",
            "project_id": _uuid_or_none(query.get("project_id")) or "",
            "department_id": _uuid_or_none(query.get("department_id")) or "",
            "start_date": query.get("start_date") or "",
            "end_date": query.get("end_date") or "",
        },
    )


def _date_or_none(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
