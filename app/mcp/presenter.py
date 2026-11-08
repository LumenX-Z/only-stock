"""MCP 工具输出字段映射。

依据 docs/08-MCP服务与工具.md 8.6.5：
- 只输出业务编码与名称，绝不输出 ``id``、``*_id`` 或内部 UUID；
- 时间使用 UTC RFC 3339 字符串，数量为不带科学计数法的十进制字符串；
- 单据摘要返回去重项目列表，详情另含全部明细与历史快照。
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from app.modules.categories.models import Category
from app.modules.documents.models import StockDocument, StockDocumentLine
from app.modules.inventory.quantity import format_quantity_milli
from app.modules.inventory.service import InventoryRow
from app.modules.materials.models import Material


def isoformat(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def warehouse_payload(row: object) -> dict[str, object]:
    return {
        "code": row.code,
        "name": row.name,
        "location": row.location,
        "is_active": bool(row.is_active),
        "created_at": isoformat(row.created_at),
        "updated_at": isoformat(row.updated_at),
    }


def named_payload(row: object) -> dict[str, object]:
    return {
        "code": row.code,
        "name": row.name,
        "is_active": bool(row.is_active),
        "created_at": isoformat(row.created_at),
        "updated_at": isoformat(row.updated_at),
    }


def category_payload(row: Category, parent_code: str | None) -> dict[str, object]:
    return {
        "code": row.code,
        "name": row.name,
        "level": int(row.level),
        "parent_code": parent_code,
        "is_active": bool(row.is_active),
        "created_at": isoformat(row.created_at),
        "updated_at": isoformat(row.updated_at),
    }


def material_payload(
    material: Material,
    category: Category | None,
    parent: Category | None,
) -> dict[str, object]:
    return {
        "code": material.code,
        "name": material.name,
        "unit": material.unit,
        "quantity_type": material.quantity_type,
        "category_code": None if category is None else category.code,
        "category_name": None if category is None else category.name,
        "parent_category_code": None if parent is None else parent.code,
        "parent_category_name": None if parent is None else parent.name,
        "is_active": bool(material.is_active),
        "created_at": isoformat(material.created_at),
        "updated_at": isoformat(material.updated_at),
    }


def inventory_payload(row: InventoryRow) -> dict[str, object]:
    """库存列表项只输出仓库、物资与可用量，不含内部 ID。"""
    return {
        "warehouse_code": row.warehouse_code,
        "warehouse_name": row.warehouse_name,
        "material_code": row.material_code,
        "material_name": row.material_name,
        "unit": row.unit,
        "quantity_type": row.quantity_type,
        "balance": format_quantity_milli(row.balance_milli),
    }


def document_summary(
    document: StockDocument,
    projects: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "doc_no": document.doc_no,
        "doc_type": document.doc_type,
        "status": document.status,
        "warehouse_code": document.warehouse_code,
        "warehouse_name": document.warehouse_name,
        "department_code": document.department_code,
        "department_name": document.department_name,
        "projects": [
            {
                "project_code": item.get("project_code"),
                "project_name": item.get("project_name"),
            }
            for item in projects
        ],
        "created_by_name": document.created_by_name,
        "created_at": isoformat(document.created_at),
        "deleted": document.deleted_at is not None,
    }


def document_detail(
    document: StockDocument,
    lines: Sequence[StockDocumentLine],
) -> dict[str, object]:
    payload = document_summary(document, project_summaries(lines))
    payload.update(
        {
            "submitted_at": isoformat(document.submitted_at),
            "submitted_by_name": document.submitted_by_name,
            "deleted_at": isoformat(document.deleted_at),
            "lines": [line_payload(line) for line in lines],
        }
    )
    return payload


def project_summaries(
    lines: Iterable[StockDocumentLine],
) -> list[dict[str, object]]:
    """按明细顺序去重项目，空项目不进入摘要。"""
    projects: list[dict[str, object]] = []
    seen: set[str] = set()
    for line in lines:
        if line.project_code is None or line.project_code in seen:
            continue
        seen.add(line.project_code)
        projects.append({"project_code": line.project_code, "project_name": line.project_name})
    return projects


def line_payload(line: StockDocumentLine) -> dict[str, object]:
    return {
        "line_no": int(line.line_no),
        "material_code": line.material_code,
        "material_name": line.material_name,
        "unit": line.unit,
        "quantity_type": line.quantity_type,
        "category_name": line.category_name,
        "parent_category_name": line.parent_category_name,
        "manufacturer_code": line.manufacturer_code,
        "manufacturer_name": line.manufacturer_name,
        "project_code": line.project_code,
        "project_name": line.project_name,
        "quantity": format_quantity_milli(line.quantity_milli),
        "remark": line.remark,
    }
