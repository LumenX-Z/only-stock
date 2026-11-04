"""单据输出映射。

依据 docs/11-单据与库存业务API.md 8.5：
- 单据列表与详情返回软删除状态；
- 明细返回逐行项目和快照字段；
- 列表摘要以去重项目列表 ``projects`` 表达，不返回单据头单值项目。
"""

from collections.abc import Iterable, Sequence

from app.modules.documents.models import StockDocument, StockDocumentLine
from app.modules.inventory.quantity import format_quantity_milli


def line_payload(line: StockDocumentLine) -> dict[str, object]:
    return {
        "id": line.id,
        "line_no": line.line_no,
        "material_id": line.material_id,
        "material_code": line.material_code,
        "material_name": line.material_name,
        "unit": line.unit,
        "quantity_type": line.quantity_type,
        "quantity": format_quantity_milli(line.quantity_milli),
        "manufacturer_id": line.manufacturer_id,
        "manufacturer_code": line.manufacturer_code,
        "manufacturer_name": line.manufacturer_name,
        "project_id": line.project_id,
        "project_code": line.project_code,
        "project_name": line.project_name,
        "category_code": line.category_code,
        "category_name": line.category_name,
        "parent_category_code": line.parent_category_code,
        "parent_category_name": line.parent_category_name,
    }


def project_payloads(lines: Iterable[StockDocumentLine]) -> list[dict[str, object]]:
    """按明细顺序去重项目，空项目不进入摘要。"""
    projects: list[dict[str, object]] = []
    seen: set[str] = set()
    for line in lines:
        if line.project_id is None or line.project_id in seen:
            continue
        seen.add(line.project_id)
        projects.append(
            {
                "project_id": line.project_id,
                "project_code": line.project_code,
                "project_name": line.project_name,
            }
        )
    return projects


def summary_payload(
    document: StockDocument,
    projects: Sequence[dict[str, object]],
) -> dict[str, object]:
    return {
        "id": document.id,
        "doc_no": document.doc_no,
        "doc_type": document.doc_type,
        "status": document.status,
        "warehouse_id": document.warehouse_id,
        "warehouse_code": document.warehouse_code,
        "warehouse_name": document.warehouse_name,
        "department_id": document.department_id,
        "department_code": document.department_code,
        "department_name": document.department_name,
        "remark": document.remark,
        "version": document.version,
        "created_source": document.created_source,
        "created_by": document.created_by,
        "created_by_name": document.created_by_name,
        "created_at": document.created_at,
        "submitted_by": document.submitted_by,
        "submitted_by_name": document.submitted_by_name,
        "submitted_at": document.submitted_at,
        "deleted_at": document.deleted_at,
        "deleted_by": document.deleted_by,
        "deleted": document.deleted_at is not None,
        "projects": list(projects),
    }


def detail_payload(
    document: StockDocument,
    lines: Sequence[StockDocumentLine],
) -> dict[str, object]:
    payload = summary_payload(document, project_payloads(lines))
    payload["request_id"] = document.request_id
    payload["lines"] = [line_payload(line) for line in lines]
    return payload
