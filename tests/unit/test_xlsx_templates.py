"""XLSX 模板结构单元测试。

依据 docs/07-XLSX模板、快速录入与导出.md 7.8.1、docs/16-验收标准.md ACC-XLSX-001。
"""

from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.modules.xlsx.constants import (
    INBOUND_COLUMNS,
    OUTBOUND_COLUMNS,
    SHEET_DATA,
    SHEET_HELP,
    TEMPLATE_VERSIONS,
)
from app.modules.xlsx.templates import (
    build_template,
    content_disposition,
    export_filenames,
    template_filenames,
)
from tests.xlsx_support import template_bytes


def _workbook(doc_type: str):
    return load_workbook(BytesIO(template_bytes(doc_type)))


def test_xlsx_template_has_required_sheets_and_header_area() -> None:
    workbook = _workbook("INBOUND")
    assert workbook.sheetnames == [SHEET_DATA, SHEET_HELP]
    sheet = workbook[SHEET_DATA]
    assert sheet["A1"].value == "仓库名称"
    assert sheet["B1"].value is None
    assert sheet["A2"].value == "管理部门名称"
    assert sheet["B2"].value is None


def test_xlsx_template_inbound_columns_and_versions() -> None:
    workbook = _workbook("INBOUND")
    sheet = workbook[SHEET_DATA]
    titles = [sheet.cell(row=3, column=index).value for index in range(1, 8)]
    assert tuple(titles) == INBOUND_COLUMNS
    assert "厂家名称" in titles
    assert titles[-1] == "备注"

    help_values = {
        row[0].value: row[1].value
        for row in workbook[SHEET_HELP].iter_rows(min_row=1, max_row=20, max_col=2)
    }
    assert help_values["模板标识"] == "ONLY-STOCK"
    assert help_values["template_version"] == TEMPLATE_VERSIONS["INBOUND"]


def test_xlsx_template_outbound_has_no_manufacturer_column() -> None:
    workbook = _workbook("OUTBOUND")
    sheet = workbook[SHEET_DATA]
    titles = [sheet.cell(row=3, column=index).value for index in range(1, 7)]
    assert tuple(titles) == OUTBOUND_COLUMNS
    assert "厂家名称" not in titles
    assert titles[-1] == "备注"
    assert sheet.cell(row=3, column=7).value is None


def test_xlsx_template_quantity_column_is_text_format() -> None:
    inbound = _workbook("INBOUND")[SHEET_DATA]
    assert inbound.cell(row=4, column=5).number_format == "@"
    assert inbound.cell(row=2003, column=5).number_format == "@"
    outbound = _workbook("OUTBOUND")[SHEET_DATA]
    assert outbound.cell(row=4, column=5).number_format == "@"
    assert outbound.cell(row=4, column=6).value is None


def test_xlsx_template_help_contains_requiredness_and_quantity_rules() -> None:
    workbook = _workbook("INBOUND")
    text = "\n".join(
        str(row[1].value or "")
        for row in workbook[SHEET_HELP].iter_rows(min_row=1, max_row=20, max_col=2)
    )
    assert "项目名称可空" in text
    assert "厂家名称必填" in text
    assert "三位小数" in text
    assert "公式" in text


def test_xlsx_template_filenames_are_safe() -> None:
    ascii_name, chinese_name = template_filenames("INBOUND")
    assert ascii_name == "INBOUND_V1_template.xlsx"
    assert "入库单" in chinese_name
    disposition = content_disposition(ascii_name, chinese_name)
    assert disposition.startswith('attachment; filename="INBOUND_V1_template.xlsx"')
    assert "filename*=UTF-8''" in disposition

    ascii_name, chinese_name = export_filenames("OUTBOUND", "CK20261024-000001")
    assert ascii_name == "OUTBOUND_CK20261024-000001.xlsx"
    assert chinese_name == "出库单_CK20261024-000001.xlsx"


def test_xlsx_export_filenames_sanitize_unsafe_doc_no() -> None:
    ascii_name, chinese_name = export_filenames("INBOUND", 'RK"2026\r\nInjected')
    assert '"' not in ascii_name
    assert "\r" not in ascii_name and "\n" not in ascii_name
    assert chinese_name.endswith(".xlsx")


@pytest.mark.parametrize("doc_type", ["INBOUND", "OUTBOUND"])
def test_xlsx_template_generation_is_repeatable(doc_type: str) -> None:
    first = build_template(doc_type)
    second = build_template(doc_type)
    assert first.startswith(b"PK")
    assert second.startswith(b"PK")
    for data in (first, second):
        workbook = load_workbook(BytesIO(data))
        assert workbook.sheetnames == [SHEET_DATA, SHEET_HELP]
