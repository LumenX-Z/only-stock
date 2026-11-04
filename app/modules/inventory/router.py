"""库存查询 HTTP 接口。

依据 docs/11-单据与库存业务API.md 8.5：
- ``GET /inventory`` 查询实时库存，支持仓库、物资、编码/名称、是否含零库存与分页；
- ``GET /inventory/transactions`` 查询有效单据流水，按明细行返回逐行项目数量；
- 两种角色均可读取，所有数量为十进制字符串。
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth.permissions import RequirePasswordChanged
from app.db.database import get_session
from app.modules.inventory.quantity import format_quantity_milli
from app.modules.inventory.schemas import (
    InventoryItemOut,
    InventoryListResponse,
    InventoryTransactionOut,
    InventoryTransactionsResponse,
)
from app.modules.inventory.service import list_inventory, list_transactions, local_day_bounds

router = APIRouter(prefix="/api/v1", tags=["inventory"])

PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("/inventory", response_model=InventoryListResponse)
def inventory_endpoint(
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    page: PageNumber = 1,
    page_size: PageSize = 20,
    warehouse_id: UUID | None = None,
    material_id: UUID | None = None,
    code: str | None = None,
    name: str | None = None,
    include_zero: bool = False,
) -> InventoryListResponse:
    rows, total = list_inventory(
        session,
        warehouse_id=None if warehouse_id is None else str(warehouse_id),
        material_id=None if material_id is None else str(material_id),
        code=code,
        name=name,
        include_zero=include_zero,
        page=page,
        page_size=page_size,
    )
    return InventoryListResponse(
        items=[
            InventoryItemOut(
                warehouse_id=row.warehouse_id,
                warehouse_code=row.warehouse_code,
                warehouse_name=row.warehouse_name,
                material_id=row.material_id,
                code=row.material_code,
                material_name=row.material_name,
                unit=row.unit,
                quantity_type=row.quantity_type,
                balance=format_quantity_milli(row.balance_milli),
            )
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/inventory/transactions", response_model=InventoryTransactionsResponse)
def inventory_transactions_endpoint(
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    request: Request,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    warehouse_id: UUID | None = None,
    material_id: UUID | None = None,
    project_id: UUID | None = None,
    department_id: UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> InventoryTransactionsResponse:
    start_at, end_before = local_day_bounds(
        start_date, end_date, request.app.state.settings.timezone
    )
    rows, total = list_transactions(
        session,
        warehouse_id=None if warehouse_id is None else str(warehouse_id),
        material_id=None if material_id is None else str(material_id),
        project_id=None if project_id is None else str(project_id),
        department_id=None if department_id is None else str(department_id),
        start_at=start_at,
        end_before=end_before,
        page=page,
        page_size=page_size,
    )
    return InventoryTransactionsResponse(
        items=[
            InventoryTransactionOut(
                id=row.line_id,
                line_no=row.line_no,
                doc_no=row.doc_no,
                direction=row.doc_type,
                quantity=format_quantity_milli(row.quantity_milli),
                warehouse_id=row.warehouse_id,
                warehouse_code=row.warehouse_code,
                warehouse_name=row.warehouse_name,
                department_id=row.department_id,
                department_code=row.department_code,
                department_name=row.department_name,
                material_id=row.material_id,
                material_code=row.material_code,
                material_name=row.material_name,
                unit=row.unit,
                project_id=row.project_id,
                project_code=row.project_code,
                project_name=row.project_name,
                manufacturer_id=row.manufacturer_id,
                manufacturer_code=row.manufacturer_code,
                manufacturer_name=row.manufacturer_name,
                submitted_at=row.submitted_at,
            )
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )
