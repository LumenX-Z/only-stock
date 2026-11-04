"""库存余额、提交校验与流水查询。

依据 docs/06-库存计算与单据业务流程.md 7.1、7.3、7.4、7.7：
- 库存只按“仓库 + 物资”汇总，草稿和已软删除单不计入；
- 入库增加库存，出库减少库存；零余额记录可能不出现在视图中，读取时按零补齐；
- 提交在 ``BEGIN IMMEDIATE`` 写事务内重新读取余额，负库存与余额上限整单拒绝。

所有写事务内的余额读取都在调用方已取得写锁之后执行，禁止以加锁前的余额为依据。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, String, and_, column, func, select, table, true
from sqlalchemy.orm import Session

from app.api.listing import ListParams, SortOrder, order_by, resolve_sort
from app.errors import AppError
from app.modules.documents.models import DocStatus, DocType, StockDocument, StockDocumentLine
from app.modules.inventory.quantity import MAX_BALANCE_MILLI, format_quantity_milli
from app.modules.materials.models import Material

INVENTORY_VIEW = table(
    "v_inventory_balance",
    column("warehouse_id", String(36)),
    column("material_id", String(36)),
    column("balance_milli", Integer),
)
_WAREHOUSES = table(
    "warehouses",
    column("id", String(36)),
    column("code", String(16)),
    column("name", String(100)),
)
_MATERIALS = table(
    "materials",
    column("id", String(36)),
    column("code", String(16)),
    column("name", String(100)),
    column("unit", String(20)),
    column("quantity_type", String(8)),
)

DIRECTION_SIGN = {DocType.INBOUND.value: 1, DocType.OUTBOUND.value: -1}


@dataclass(frozen=True, slots=True)
class InventoryRow:
    warehouse_id: str
    warehouse_code: str
    warehouse_name: str
    material_id: str
    material_code: str
    material_name: str
    unit: str
    quantity_type: str
    balance_milli: int


@dataclass(frozen=True, slots=True)
class TransactionRow:
    line_id: str
    document_id: str
    line_no: int
    doc_no: str
    doc_type: str
    warehouse_id: str
    warehouse_code: str
    warehouse_name: str
    department_id: str
    department_code: str
    department_name: str
    material_id: str
    material_code: str
    material_name: str
    unit: str
    quantity_milli: int
    project_id: str | None
    project_code: str | None
    project_name: str | None
    manufacturer_id: str | None
    manufacturer_code: str | None
    manufacturer_name: str | None
    submitted_at: datetime


def current_balances(
    session: Session, warehouse_id: str, material_ids: Sequence[str]
) -> dict[str, int]:
    """读取指定仓库、物资的当前余额；缺行按零处理。"""
    ids = list(dict.fromkeys(material_ids))
    if not ids:
        return {}
    rows = session.execute(
        select(INVENTORY_VIEW.c.material_id, INVENTORY_VIEW.c.balance_milli).where(
            INVENTORY_VIEW.c.warehouse_id == warehouse_id,
            INVENTORY_VIEW.c.material_id.in_(ids),
        )
    ).all()
    return {str(row[0]): int(row[1]) for row in rows}


def ensure_submit_balances(
    session: Session,
    *,
    warehouse_id: str,
    doc_type: str,
    totals: Mapping[str, int],
    materials: Mapping[str, Material],
) -> None:
    """在写事务内校验提交后的余额；任一物资失败即整单拒绝。"""
    if not totals:
        return
    balances = current_balances(session, warehouse_id, list(totals))
    sign = DIRECTION_SIGN[doc_type]
    over_limit: list[dict[str, object]] = []
    insufficient: list[dict[str, object]] = []

    for material_id, quantity in totals.items():
        material = materials[material_id]
        current = balances.get(material_id, 0)
        after = current + sign * quantity
        if after < 0:
            insufficient.append(
                {
                    "code": material.code,
                    "name": material.name,
                    "available": format_quantity_milli(current),
                    "requested": format_quantity_milli(quantity),
                }
            )
        elif after > MAX_BALANCE_MILLI:
            over_limit.append(
                {
                    "code": material.code,
                    "name": material.name,
                    "current": format_quantity_milli(current),
                    "requested": format_quantity_milli(quantity),
                    "result": format_quantity_milli(after),
                    "limit": format_quantity_milli(MAX_BALANCE_MILLI),
                }
            )

    if insufficient:
        raise AppError(
            "INSUFFICIENT_STOCK",
            "库存不足，出库单未生效",
            status_code=409,
            resource="inventory",
            items=insufficient,
        )
    if over_limit:
        raise _balance_limit_error(over_limit)


def ensure_removal_balances(
    session: Session,
    *,
    warehouse_id: str,
    doc_type: str,
    totals: Mapping[str, int],
    materials: Mapping[str, Material],
) -> None:
    """校验软删除本单后的余额仍处于 ``[0, MAX_BALANCE_MILLI]``。"""
    if not totals:
        return
    balances = current_balances(session, warehouse_id, list(totals))
    # 移除本单即反向应用本单方向：入库单移除后减少，出库单移除后增加。
    sign = -DIRECTION_SIGN[doc_type]
    negative: list[dict[str, object]] = []
    over_limit: list[dict[str, object]] = []

    for material_id, quantity in totals.items():
        material = materials[material_id]
        current = balances.get(material_id, 0)
        after = current + sign * quantity
        if after < 0:
            negative.append(
                {
                    "code": material.code,
                    "name": material.name,
                    "current": format_quantity_milli(current),
                    "requested": format_quantity_milli(quantity),
                    "result": format_quantity_milli(after),
                }
            )
        elif after > MAX_BALANCE_MILLI:
            over_limit.append(
                {
                    "code": material.code,
                    "name": material.name,
                    "current": format_quantity_milli(current),
                    "requested": format_quantity_milli(quantity),
                    "result": format_quantity_milli(after),
                    "limit": format_quantity_milli(MAX_BALANCE_MILLI),
                }
            )

    if negative:
        raise AppError(
            "NEGATIVE_STOCK",
            "软删除该单据会导致库存为负，操作未执行",
            status_code=409,
            resource="inventory",
            items=negative,
        )
    if over_limit:
        raise _balance_limit_error(over_limit)


def local_day_bounds(
    start_date: date | None,
    end_date: date | None,
    timezone: ZoneInfo,
) -> tuple[datetime | None, datetime | None]:
    """把本地业务日期转换为 UTC 左闭右开区间；结束日期包含当天。"""
    if start_date is not None and end_date is not None and start_date > end_date:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            resource="document",
            items=[{"field": "start_date", "reason": "开始日期不能晚于结束日期"}],
        )
    start_at = (
        datetime.combine(start_date, time.min, tzinfo=timezone) if start_date is not None else None
    )
    end_at = (
        datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone)
        if end_date is not None
        else None
    )
    return start_at, end_at


def list_inventory(
    session: Session,
    *,
    warehouse_id: str | None = None,
    material_id: str | None = None,
    code: str | None = None,
    name: str | None = None,
    include_zero: bool = False,
    page: int = 1,
    page_size: int = 20,
    sort: str | None = None,
    order: SortOrder | None = None,
) -> tuple[list[InventoryRow], int]:
    """分页查询实时库存；``include_zero`` 时补齐零余额组合。"""
    conditions: list[object] = []
    if warehouse_id:
        conditions.append(_WAREHOUSES.c.id == warehouse_id.strip())
    if material_id:
        conditions.append(_MATERIALS.c.id == material_id.strip())
    if code:
        conditions.append(_MATERIALS.c.code == code.strip())
    if name:
        conditions.append(_MATERIALS.c.name == name.strip())

    columns = (
        _WAREHOUSES.c.id.label("warehouse_id"),
        _WAREHOUSES.c.code.label("warehouse_code"),
        _WAREHOUSES.c.name.label("warehouse_name"),
        _MATERIALS.c.id.label("material_id"),
        _MATERIALS.c.code.label("material_code"),
        _MATERIALS.c.name.label("material_name"),
        _MATERIALS.c.unit.label("unit"),
        _MATERIALS.c.quantity_type.label("quantity_type"),
        func.coalesce(INVENTORY_VIEW.c.balance_milli, 0).label("balance_milli"),
    )

    if include_zero:
        base = (
            select(*columns)
            .select_from(
                _WAREHOUSES.join(_MATERIALS, true()).outerjoin(
                    INVENTORY_VIEW,
                    and_(
                        INVENTORY_VIEW.c.warehouse_id == _WAREHOUSES.c.id,
                        INVENTORY_VIEW.c.material_id == _MATERIALS.c.id,
                    ),
                )
            )
            .where(*conditions)
        )
    else:
        base = (
            select(*columns)
            .select_from(
                INVENTORY_VIEW.join(
                    _WAREHOUSES, INVENTORY_VIEW.c.warehouse_id == _WAREHOUSES.c.id
                ).join(_MATERIALS, INVENTORY_VIEW.c.material_id == _MATERIALS.c.id)
            )
            .where(INVENTORY_VIEW.c.balance_milli != 0, *conditions)
        )

    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    params = ListParams(page=page, page_size=page_size, sort=sort, order=order)
    column, direction = resolve_sort(
        params,
        fields={
            "warehouse_code": _WAREHOUSES.c.code,
            "material_code": _MATERIALS.c.code,
            "material_name": _MATERIALS.c.name,
            "balance": func.coalesce(INVENTORY_VIEW.c.balance_milli, 0),
        },
        default="warehouse_code",
        default_order=SortOrder.ASC,
        resource="inventory",
    )
    rows = session.execute(
        base.order_by(
            order_by(column, direction),
            _WAREHOUSES.c.code,
            _MATERIALS.c.code,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        InventoryRow(
            warehouse_id=str(row.warehouse_id),
            warehouse_code=str(row.warehouse_code),
            warehouse_name=str(row.warehouse_name),
            material_id=str(row.material_id),
            material_code=str(row.material_code),
            material_name=str(row.material_name),
            unit=str(row.unit),
            quantity_type=str(row.quantity_type),
            balance_milli=int(row.balance_milli),
        )
        for row in rows
    ]
    return items, total


def list_transactions(
    session: Session,
    *,
    warehouse_id: str | None = None,
    material_id: str | None = None,
    project_id: str | None = None,
    department_id: str | None = None,
    start_at: datetime | None = None,
    end_before: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    sort: str | None = None,
    order: SortOrder | None = None,
) -> tuple[list[TransactionRow], int]:
    """分页查询有效单据流水；一张单据按明细行返回。"""
    conditions: list[object] = [
        StockDocument.status == DocStatus.POSTED.value,
        StockDocument.deleted_at.is_(None),
    ]
    if warehouse_id:
        conditions.append(StockDocument.warehouse_id == warehouse_id.strip())
    if department_id:
        conditions.append(StockDocument.department_id == department_id.strip())
    if material_id:
        conditions.append(StockDocumentLine.material_id == material_id.strip())
    if project_id:
        conditions.append(StockDocumentLine.project_id == project_id.strip())
    if start_at is not None:
        conditions.append(StockDocument.submitted_at >= start_at)
    if end_before is not None:
        conditions.append(StockDocument.submitted_at < end_before)

    joined = StockDocumentLine.__table__.join(
        StockDocument.__table__, StockDocument.id == StockDocumentLine.document_id
    )
    total = int(session.scalar(select(func.count()).select_from(joined).where(*conditions)) or 0)
    params = ListParams(page=page, page_size=page_size, sort=sort, order=order)
    column, direction = resolve_sort(
        params,
        fields={
            "submitted_at": StockDocument.submitted_at,
            "doc_no": StockDocument.doc_no,
            "line_no": StockDocumentLine.line_no,
            "material_code": StockDocumentLine.material_code,
            "quantity": StockDocumentLine.quantity_milli,
        },
        default="submitted_at",
        default_order=SortOrder.DESC,
        resource="inventory_transaction",
    )
    rows = session.execute(
        select(
            StockDocumentLine.id.label("line_id"),
            StockDocumentLine.document_id.label("document_id"),
            StockDocumentLine.line_no.label("line_no"),
            StockDocument.doc_no.label("doc_no"),
            StockDocument.doc_type.label("doc_type"),
            StockDocument.warehouse_id.label("warehouse_id"),
            StockDocument.warehouse_code.label("warehouse_code"),
            StockDocument.warehouse_name.label("warehouse_name"),
            StockDocument.department_id.label("department_id"),
            StockDocument.department_code.label("department_code"),
            StockDocument.department_name.label("department_name"),
            StockDocumentLine.material_id.label("material_id"),
            StockDocumentLine.material_code.label("material_code"),
            StockDocumentLine.material_name.label("material_name"),
            StockDocumentLine.unit.label("unit"),
            StockDocumentLine.quantity_milli.label("quantity_milli"),
            StockDocumentLine.project_id.label("project_id"),
            StockDocumentLine.project_code.label("project_code"),
            StockDocumentLine.project_name.label("project_name"),
            StockDocumentLine.manufacturer_id.label("manufacturer_id"),
            StockDocumentLine.manufacturer_code.label("manufacturer_code"),
            StockDocumentLine.manufacturer_name.label("manufacturer_name"),
            StockDocument.submitted_at.label("submitted_at"),
        )
        .select_from(joined)
        .where(*conditions)
        .order_by(
            order_by(column, direction),
            StockDocument.doc_no.desc(),
            StockDocumentLine.line_no,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        TransactionRow(
            line_id=str(row.line_id),
            document_id=str(row.document_id),
            line_no=int(row.line_no),
            doc_no=str(row.doc_no),
            doc_type=str(row.doc_type),
            warehouse_id=str(row.warehouse_id),
            warehouse_code=str(row.warehouse_code),
            warehouse_name=str(row.warehouse_name),
            department_id=str(row.department_id),
            department_code=str(row.department_code),
            department_name=str(row.department_name),
            material_id=str(row.material_id),
            material_code=str(row.material_code),
            material_name=str(row.material_name),
            unit=str(row.unit),
            quantity_milli=int(row.quantity_milli),
            project_id=None if row.project_id is None else str(row.project_id),
            project_code=None if row.project_code is None else str(row.project_code),
            project_name=None if row.project_name is None else str(row.project_name),
            manufacturer_id=None if row.manufacturer_id is None else str(row.manufacturer_id),
            manufacturer_code=(
                None if row.manufacturer_code is None else str(row.manufacturer_code)
            ),
            manufacturer_name=(
                None if row.manufacturer_name is None else str(row.manufacturer_name)
            ),
            submitted_at=row.submitted_at,
        )
        for row in rows
    ]
    return items, total


def _balance_limit_error(items: Sequence[Mapping[str, object]]) -> AppError:
    return AppError(
        "BALANCE_LIMIT_EXCEEDED",
        "库存余额超过允许上限，操作未执行",
        status_code=409,
        resource="inventory",
        items=[dict(item) for item in items],
    )
