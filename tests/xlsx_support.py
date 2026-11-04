"""XLSX 测试夹具辅助函数。"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from io import BytesIO
from typing import Any

from openpyxl import load_workbook

from app.modules.xlsx.templates import build_template

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def template_bytes(doc_type: str = "INBOUND") -> bytes:
    return build_template(doc_type)


def load_template(doc_type: str = "INBOUND"):
    return load_workbook(BytesIO(template_bytes(doc_type)))


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
        data_sheet["B1"] = warehouse_name
    if department_name is not None:
        data_sheet["B2"] = department_name
    if version is not None:
        for row in workbook["填写说明"].iter_rows(min_row=1, max_row=10, max_col=2):
            if row[0].value == "template_version":
                row[1].value = version
                row[1].data_type = "s"
    for index, values in enumerate(rows or [], start=4):
        for column, value in enumerate(values, start=1):
            if value is not None:
                data_sheet.cell(row=index, column=column, value=value)
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
