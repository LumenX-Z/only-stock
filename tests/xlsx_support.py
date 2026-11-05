"""XLSX 测试夹具辅助函数。"""

from __future__ import annotations

import zipfile
from collections.abc import Callable, Sequence
from io import BytesIO
from typing import Any

from openpyxl import load_workbook

from app.modules.xlsx.constants import COLUMN_TITLES
from app.modules.xlsx.templates import build_template

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 测试行统一使用语义字段顺序，由辅助函数写入模板实际列。
_FIELDS = (
    "project_name",
    "level1_name",
    "level2_name",
    "material_name",
    "quantity",
    "manufacturer_name",
    "remark",
)
_OUTBOUND_FIELDS = tuple(field for field in _FIELDS if field != "manufacturer_name")
_FIELD_COLUMNS = {
    "project_name": "项目名称",
    "level1_name": "一级分类名称",
    "level2_name": "二级分类名称",
    "material_name": "物资名称",
    "quantity": "数量",
    "manufacturer_name": "厂家名称",
    "remark": "备注",
}


def template_bytes(doc_type: str = "INBOUND") -> bytes:
    return build_template(doc_type)


def load_template(doc_type: str = "INBOUND"):
    return load_workbook(BytesIO(template_bytes(doc_type)))


def write_rows(sheet, doc_type: str, rows: Sequence[Sequence[Any]]) -> None:
    """按 ``_FIELDS`` 语义顺序把测试数据写入模板实际列。"""
    columns = COLUMN_TITLES[doc_type]
    for row_no, values in enumerate(rows, start=4):
        fields = _OUTBOUND_FIELDS if doc_type == "OUTBOUND" and len(values) == 6 else _FIELDS
        for field, value in zip(fields, values, strict=False):
            title = _FIELD_COLUMNS[field]
            if title not in columns or value is None:
                continue
            sheet.cell(row=row_no, column=columns.index(title) + 1, value=value)


def workbook_bytes(workbook) -> bytes:
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def edit_workbook(data: bytes, editor: Callable[[Any], None]) -> bytes:
    workbook = load_workbook(BytesIO(data))
    editor(workbook)
    return workbook_bytes(workbook)


def filled_workbook(
    doc_type: str = "INBOUND",
    *,
    warehouse_name: str | None = None,
    department_name: str | None = None,
    rows: list[list[Any]] | None = None,
    version: str | None = None,
) -> bytes:
    workbook = load_template(doc_type)
    data_sheet = workbook["录入数据"]
    if warehouse_name is not None:
        data_sheet["B2"] = warehouse_name
    if department_name is not None:
        data_sheet["F2"] = department_name
    if version is not None:
        for row in workbook["填写说明"].iter_rows(min_row=1, max_row=10, max_col=2):
            if row[0].value == "template_version":
                row[1].value = version
                row[1].data_type = "s"
    write_rows(data_sheet, doc_type, rows or [])
    return workbook_bytes(workbook)


def rewrite_zip(
    data: bytes,
    *,
    add: dict[str, bytes] | None = None,
    remove: set[str] | None = None,
) -> bytes:
    source = zipfile.ZipFile(BytesIO(data))
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            if remove and info.filename in remove:
                continue
            target.writestr(info, source.read(info.filename))
        for name, content in (add or {}).items():
            target.writestr(name, content)
    return buffer.getvalue()


def upload_files(data: bytes, filename: str = "entry.xlsx") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (filename, data, XLSX_MIME)}
