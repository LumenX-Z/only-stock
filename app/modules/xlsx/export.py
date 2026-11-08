"""单张单据导出。

依据 docs/07-XLSX模板、快速录入与导出.md 7.8.5：
- 草稿按当前基础资料名称、分类和单位导出，备注取当前明细行文本；
- 已生效及已软删除单按历史快照导出，逐行项目名称、单位和备注均取明细快照；
- 导出属于读取操作，不改变单据状态和库存。
"""

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.categories.models import Category
from app.modules.departments.models import Department
from app.modules.documents.models import DocStatus, DocType, StockDocument, StockDocumentLine
from app.modules.inventory.quantity import format_quantity_milli
from app.modules.manufacturers.models import Manufacturer
from app.modules.materials.models import Material
from app.modules.projects.models import Project
from app.modules.warehouses.models import Warehouse
from app.modules.xlsx.constants import MAX_DATA_ROWS
from app.modules.xlsx.templates import ExportRow, build_document_export

_STATUS_LABELS = {
    DocStatus.DRAFT.value: "草稿（DRAFT）",
    DocStatus.POSTED.value: "已生效（POSTED）",
}
_DOC_TYPE_LABELS = {
    DocType.INBOUND.value: "入库单（INBOUND）",
    DocType.OUTBOUND.value: "出库单（OUTBOUND）",
}


def build_export_bytes(
    session: Session,
    document: StockDocument,
    lines: Sequence[StockDocumentLine],
    *,
    timezone: ZoneInfo,
) -> bytes:
    """按单据状态选择当前资料或历史快照生成导出文件。"""
    rows, project_names, warehouse_name, department_name = _export_rows(session, document, lines)
    oversized = len(rows) > MAX_DATA_ROWS
    info_rows = _info_rows(
        document,
        rows,
        project_names,
        timezone=timezone,
        warehouse_name=warehouse_name,
        department_name=department_name,
        oversized=oversized,
    )
    return build_document_export(
        doc_type=document.doc_type,
        doc_no=document.doc_no,
        warehouse_name=warehouse_name,
        department_name=department_name,
        rows=rows,
        info_rows=info_rows,
        oversized=oversized,
    )


def _export_rows(
    session: Session,
    document: StockDocument,
    lines: Sequence[StockDocumentLine],
) -> tuple[list[ExportRow], list[str], str, str]:
    if document.status != DocStatus.DRAFT.value:
        rows = [_snapshot_row(line) for line in lines]
        project_names = _unique(line.project_name for line in lines if line.project_id is not None)
        return rows, project_names, document.warehouse_name, document.department_name

    warehouse = session.get(Warehouse, document.warehouse_id)
    department = session.get(Department, document.department_id)
    materials = _load_materials(session, lines)
    categories = _load_categories(session, materials)
    manufacturers = _load_manufacturers(session, lines)
    projects = _load_projects(session, lines)

    rows: list[ExportRow] = []
    project_names: list[str] = []
    seen_projects: set[str] = set()
    for line in lines:
        material = materials.get(line.material_id)
        category = categories.get(material.category_id) if material is not None else None
        parent = categories.get(category.parent_id) if category and category.parent_id else None
        manufacturer = (
            manufacturers.get(line.manufacturer_id) if line.manufacturer_id is not None else None
        )
        project = projects.get(line.project_id) if line.project_id is not None else None
        project_name = project.name if project is not None else line.project_name
        if project_name and project_name not in seen_projects:
            seen_projects.add(project_name)
            project_names.append(project_name)
        if category is None:
            level1_name = line.parent_category_name or line.category_name
            level2_name = line.category_name if line.parent_category_name else None
        elif parent is not None:
            level1_name = parent.name
            level2_name = category.name
        else:
            level1_name = category.name
            level2_name = None
        rows.append(
            ExportRow(
                project_name=project_name,
                level1_name=level1_name,
                level2_name=level2_name,
                material_name=material.name if material is not None else line.material_name,
                quantity=format_quantity_milli(line.quantity_milli),
                unit=material.unit if material is not None else line.unit,
                manufacturer_name=(
                    manufacturer.name if manufacturer is not None else line.manufacturer_name
                ),
                remark=line.remark,
            )
        )
    return (
        rows,
        project_names,
        warehouse.name if warehouse is not None else document.warehouse_name,
        department.name if department is not None else document.department_name,
    )


def _snapshot_row(line: StockDocumentLine) -> ExportRow:
    return ExportRow(
        project_name=line.project_name,
        level1_name=line.parent_category_name or line.category_name,
        level2_name=line.category_name if line.parent_category_name else None,
        material_name=line.material_name,
        quantity=format_quantity_milli(line.quantity_milli),
        unit=line.unit,
        manufacturer_name=line.manufacturer_name,
        remark=line.remark,
    )


def _info_rows(
    document: StockDocument,
    rows: list[ExportRow],
    project_names: list[str],
    *,
    timezone: ZoneInfo,
    warehouse_name: str,
    department_name: str,
    oversized: bool,
) -> list[tuple[str, str]]:
    info: list[tuple[str, str]] = [
        ("正式单号", document.doc_no),
        ("单据类型", _DOC_TYPE_LABELS[document.doc_type]),
        ("状态", _STATUS_LABELS.get(document.status, document.status)),
        ("制单人", document.created_by_name),
        ("生效时间", _format_time(document.submitted_at, timezone)),
        ("涉及项目", "、".join(project_names) if project_names else "无"),
        ("软删除状态", _deleted_label(document.deleted_at, timezone)),
        ("仓库", warehouse_name),
        ("管理部门", department_name),
        ("明细行数", str(len(rows))),
        (
            "快速录入",
            (
                f"本单明细超过 {MAX_DATA_ROWS:,} 行，不能用于快速录入"
                if oversized
                else "可用于本系统模板快速录入"
            ),
        ),
    ]
    return info


def _load_materials(session: Session, lines: Sequence[StockDocumentLine]) -> dict[str, Material]:
    ids = list(dict.fromkeys(line.material_id for line in lines))
    if not ids:
        return {}
    rows = session.scalars(select(Material).where(Material.id.in_(ids))).all()
    return {row.id: row for row in rows}


def _load_categories(session: Session, materials: dict[str, Material]) -> dict[str, Category]:
    direct_ids = list(dict.fromkeys(material.category_id for material in materials.values()))
    if not direct_ids:
        return {}
    direct = session.scalars(select(Category).where(Category.id.in_(direct_ids))).all()
    parent_ids = list(dict.fromkeys(row.parent_id for row in direct if row.parent_id is not None))
    parents = (
        session.scalars(select(Category).where(Category.id.in_(parent_ids))).all()
        if parent_ids
        else []
    )
    return {row.id: row for row in [*direct, *parents]}


def _load_manufacturers(
    session: Session, lines: Sequence[StockDocumentLine]
) -> dict[str, Manufacturer]:
    ids = list(
        dict.fromkeys(line.manufacturer_id for line in lines if line.manufacturer_id is not None)
    )
    if not ids:
        return {}
    rows = session.scalars(select(Manufacturer).where(Manufacturer.id.in_(ids))).all()
    return {row.id: row for row in rows}


def _load_projects(session: Session, lines: Sequence[StockDocumentLine]) -> dict[str, Project]:
    ids = list(dict.fromkeys(line.project_id for line in lines if line.project_id is not None))
    if not ids:
        return {}
    rows = session.scalars(select(Project).where(Project.id.in_(ids))).all()
    return {row.id: row for row in rows}


def _unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _format_time(value: datetime | None, timezone: ZoneInfo) -> str:
    if value is None:
        return "未生效"
    return value.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S")


def _deleted_label(value: datetime | None, timezone: ZoneInfo) -> str:
    if value is None:
        return "否"
    return f"是（{_format_time(value, timezone)}）"
