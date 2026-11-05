"""XLSX 模板结构单元测试。

依据 docs/07-XLSX模板、快速录入与导出.md 7.8.1、7.8.5、
docs/16-验收标准.md ACC-XLSX-001、ACC-XLSX-010。
"""

from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.modules.xlsx.constants import (
    COLUMN_TITLE_ROW,
    FIRST_DATA_ROW,
    INBOUND_COLUMNS,
    MAX_DATA_ROW,
    OUTBOUND_COLUMNS,
    SHEET_DATA,
    SHEET_HELP,
    SHEET_OPTIONS,
    TEMPLATE_VERSIONS,
)
from app.modules.xlsx.templates import (
    ExportRow,
    TemplateOptions,
    build_document_export,
    build_template,
    content_disposition,
    export_filenames,
    template_filenames,
)


def _options() -> TemplateOptions:
    return TemplateOptions(
        project_names=("一号项目", "二号项目"),
        level1_names=("原材料", "设备"),
        level2_names=("钢材", "电缆"),
        material_names=("螺纹钢", "10kV电缆"),
        manufacturer_names=("钢厂", "电缆厂"),
        warehouse_names=("主仓", "二号仓"),
        department_names=("运检", "检修"),
    )


def _workbook(doc_type: str, *, with_options: bool = False):
    data = build_template(doc_type, options=_options() if with_options else None)
    return load_workbook(BytesIO(data))


def test_xlsx_template_title_header_sheets_and_merge() -> None:
    workbook = _workbook("INBOUND")
    assert workbook.sheetnames == [SHEET_DATA, SHEET_HELP, SHEET_OPTIONS]
    assert workbook[SHEET_OPTIONS].sheet_state == "hidden"
    sheet = workbook[SHEET_DATA]
    assert sheet["A1"].value == "入库单"
    assert "（" not in sheet["A1"].value
    assert [str(item) for item in sheet.merged_cells.ranges] == ["A1:G1"]
    assert sheet["A2"].value == "仓库名称"
    assert sheet["B2"].value is None
    assert sheet["E2"].value == "管理部门名称"
    assert sheet["F2"].value is None
    assert sheet.row_dimensions[1].height == 38
    assert sheet.row_dimensions[2].height == 20
    assert sheet.row_dimensions[3].height == 24
    assert sheet["A1"].font.name == "宋体"
    assert sheet["A1"].font.sz == 18
    assert sheet["A1"].font.b is True
    assert sheet["A1"].alignment.horizontal == "center"

    outbound = _workbook("OUTBOUND")[SHEET_DATA]
    assert outbound["A1"].value == "出库单"
    assert [str(item) for item in outbound.merged_cells.ranges] == ["A1:F1"]


def test_xlsx_template_inbound_columns_and_versions() -> None:
    workbook = _workbook("INBOUND")
    sheet = workbook[SHEET_DATA]
    titles = [sheet.cell(row=COLUMN_TITLE_ROW, column=index).value for index in range(1, 8)]
    assert tuple(titles) == INBOUND_COLUMNS
    assert "厂家名称" in titles
    assert titles[-1] == "备注"

    help_values = {
        row[0].value: row[1].value
        for row in workbook[SHEET_HELP].iter_rows(min_row=1, max_row=30, max_col=2)
    }
    assert help_values["模板标识"] == "ONLY-STOCK"
    assert help_values["template_version"] == TEMPLATE_VERSIONS["INBOUND"]
    assert "不含责任栏" in help_values["责任栏"]


def test_xlsx_template_outbound_has_no_manufacturer_column() -> None:
    workbook = _workbook("OUTBOUND")
    sheet = workbook[SHEET_DATA]
    titles = [sheet.cell(row=COLUMN_TITLE_ROW, column=index).value for index in range(1, 7)]
    assert tuple(titles) == OUTBOUND_COLUMNS
    assert "厂家名称" not in titles
    assert titles[-1] == "备注"
    assert sheet.cell(row=COLUMN_TITLE_ROW, column=7).value is None


def test_xlsx_template_quantity_column_is_text_format() -> None:
    inbound = _workbook("INBOUND")[SHEET_DATA]
    assert inbound.cell(row=FIRST_DATA_ROW, column=4).number_format == "@"
    assert inbound.cell(row=MAX_DATA_ROW, column=4).number_format == "@"
    outbound = _workbook("OUTBOUND")[SHEET_DATA]
    assert outbound.cell(row=FIRST_DATA_ROW, column=4).number_format == "@"
    assert outbound.cell(row=FIRST_DATA_ROW, column=6).value is None


def test_xlsx_template_styles_match_uploaded_files() -> None:
    inbound = _workbook("INBOUND")[SHEET_DATA]
    assert inbound.column_dimensions["A"].width == pytest.approx(17.6363636363636)
    assert inbound.column_dimensions["D"].width == pytest.approx(13.4545454545455)
    assert inbound.column_dimensions["F"].width == pytest.approx(29.9090909090909)
    assert inbound.column_dimensions["G"].width == pytest.approx(17.4545454545455)

    outbound = _workbook("OUTBOUND")[SHEET_DATA]
    assert outbound.column_dimensions["E"].width == pytest.approx(25.0909090909091)
    assert outbound.column_dimensions["F"].width == pytest.approx(32.6363636363636)

    for coordinate in ("A3", "G3", "A4", "G2003"):
        cell = inbound[coordinate]
        assert cell.border.left.style == "thin"
        assert cell.border.right.style == "thin"
        assert cell.border.top.style == "thin"
        assert cell.border.bottom.style == "thin"
        assert cell.fill.fill_type is None
    assert inbound["A4"].font.name == "宋体"
    assert inbound["A4"].alignment.horizontal == "center"
    assert inbound["A4"].alignment.vertical == "center"


def test_xlsx_template_has_dropdowns_for_current_names() -> None:
    workbook = _workbook("INBOUND", with_options=True)
    sheet = workbook[SHEET_DATA]
    validations = {
        str(validation.sqref): validation for validation in sheet.data_validations.dataValidation
    }
    assert set(validations) == {
        "B2",
        "F2",
        "A4:A2003",
        "B4:B2003",
        "C4:C2003",
        "E4:E2003",
        "F4:F2003",
    }
    assert validations["B2"].formula1 == "ONLY_STOCK_INBOUND_WAREHOUSE"
    assert validations["F2"].formula1 == "ONLY_STOCK_INBOUND_DEPARTMENT"
    assert validations["A4:A2003"].formula1 == "ONLY_STOCK_INBOUND_LEVEL1"
    assert validations["F4:F2003"].formula1 == "ONLY_STOCK_INBOUND_PROJECT"
    assert validations["B2"].type == "list"
    assert validations["B2"].allow_blank is True
    assert validations["B2"].showDropDown is False

    names = {item.name: item.attr_text for item in workbook.defined_names.values()}
    assert names["ONLY_STOCK_INBOUND_WAREHOUSE"] == "'下拉选项'!$A$1:$A$2"
    assert names["ONLY_STOCK_INBOUND_PROJECT"] == "'下拉选项'!$G$1:$G$2"

    option_sheet = workbook[SHEET_OPTIONS]
    assert [option_sheet.cell(row=1, column=index).value for index in range(1, 8)] == [
        "主仓",
        "运检",
        "原材料",
        "钢材",
        "螺纹钢",
        "钢厂",
        "一号项目",
    ]


def test_xlsx_template_has_no_responsible_row() -> None:
    for doc_type in ("INBOUND", "OUTBOUND"):
        sheet = _workbook(doc_type)[SHEET_DATA]
        labels = {
            "送货人：",
            "接收人：",
            "审核人：",
            "批准人：",
            "领用人：",
            "出库人：",
        }
        found = {
            cell.value
            for row in sheet.iter_rows(
                min_row=FIRST_DATA_ROW,
                max_row=MAX_DATA_ROW,
                max_col=7,
            )
            for cell in row
            if cell.value in labels
        }
        assert found == set()


def test_xlsx_export_title_contains_doc_no_and_keeps_responsible_row() -> None:
    data = build_document_export(
        doc_type="INBOUND",
        doc_no="RK20261105-000001",
        warehouse_name="主仓",
        department_name="运检",
        rows=[
            ExportRow(
                project_name="一号项目",
                level1_name="原材料",
                level2_name="钢材",
                material_name="螺纹钢",
                quantity="1.250",
                manufacturer_name="钢厂",
                remark="采购批次 A",
            )
        ],
        info_rows=[("正式单号", "RK20261105-000001")],
    )
    workbook = load_workbook(BytesIO(data))
    assert workbook.sheetnames == ["录入数据", "填写说明", "单据信息"]
    sheet = workbook["录入数据"]
    assert sheet["A1"].value == "入库单（RK20261105-000001）"
    assert [str(item) for item in sheet.merged_cells.ranges] == ["A1:G1"]
    assert sheet["A2"].value == "仓库名称"
    assert sheet["B2"].value == "主仓"
    assert sheet["E2"].value == "管理部门名称"
    assert sheet["F2"].value == "运检"
    assert [sheet.cell(row=4, column=index).value for index in range(1, 8)] == [
        "原材料",
        "钢材",
        "螺纹钢",
        "1.250",
        "钢厂",
        "一号项目",
        "采购批次 A",
    ]
    responsible_row = 6
    assert sheet.cell(row=5, column=1).value is None
    assert [sheet.cell(row=responsible_row, column=index).value for index in (1, 3, 5, 6)] == [
        "送货人：",
        "接收人：",
        "审核人：",
        "批准人：",
    ]
    assert [sheet.cell(row=responsible_row, column=index).value for index in (2, 4)] == [
        None,
        None,
    ]
    assert not sheet.data_validations.dataValidation


def test_xlsx_export_outbound_responsible_labels() -> None:
    data = build_document_export(
        doc_type="OUTBOUND",
        doc_no="CK20261105-000001",
        warehouse_name="主仓",
        department_name="运检",
        rows=[
            ExportRow(
                project_name=None,
                level1_name="原材料",
                level2_name=None,
                material_name="螺纹钢",
                quantity="2.000",
                manufacturer_name=None,
                remark=None,
            )
        ],
        info_rows=[],
    )
    sheet = load_workbook(BytesIO(data))["录入数据"]
    assert [sheet.cell(row=6, column=index).value for index in (1, 3, 5, 6)] == [
        "领用人：",
        "出库人：",
        "审核人：",
        "批准人：",
    ]


def test_xlsx_template_help_contains_requiredness_and_quantity_rules() -> None:
    workbook = _workbook("INBOUND")
    text = "\n".join(
        str(row[1].value or "")
        for row in workbook[SHEET_HELP].iter_rows(min_row=1, max_row=30, max_col=2)
    )
    assert "项目名称可空" in text
    assert "厂家名称必填" in text
    assert "三位小数" in text
    assert "公式" in text
    assert "下拉" in text


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
    first = build_template(doc_type, options=_options())
    second = build_template(doc_type, options=_options())
    assert first.startswith(b"PK")
    assert second.startswith(b"PK")
    for data in (first, second):
        workbook = load_workbook(BytesIO(data))
        assert workbook.sheetnames == [SHEET_DATA, SHEET_HELP, SHEET_OPTIONS]
