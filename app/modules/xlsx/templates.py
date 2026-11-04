"""XLSX 模板与导出工作簿生成。

依据 docs/07-XLSX模板、快速录入与导出.md 7.8.1、7.8.5：
- “录入数据”固定表头区、列顺序与明细起始行；
- 入库含厂家列，出库无厂家列，备注逐行且为最后一列；
- 导出的数量写入文本单元格并保留三位小数；
- 所有用户可控文本强制按字符串单元格写入，避免 ``=``、``+``、``-``、``@`` 被当作公式。
"""

import re
from dataclasses import dataclass
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from app.modules.documents.models import DocType
from app.modules.xlsx.constants import (
    COLUMN_TITLES,
    FIRST_DATA_ROW,
    HEADER_DEPARTMENT_LABEL,
    HEADER_LAST_ROW,
    HEADER_WAREHOUSE_LABEL,
    MAX_DATA_ROWS,
    QUANTITY_COLUMN,
    SHEET_DATA,
    SHEET_HELP,
    SHEET_INFO,
    TEMPLATE_IDENTIFIER,
    TEMPLATE_IDENTIFIER_LABEL,
    TEMPLATE_VERSION_LABEL,
    TEMPLATE_VERSIONS,
    TITLE_ROW,
)

_HEADER_FILL = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
_TITLE_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
_BOLD = Font(bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")
_SAFE_DOC_NO = re.compile(r"^[A-Za-z0-9-]+$")

DOC_TYPE_LABELS = {
    DocType.INBOUND.value: "入库单（INBOUND）",
    DocType.OUTBOUND.value: "出库单（OUTBOUND）",
}
FILE_TYPE_LABELS = {
    DocType.INBOUND.value: "入库单",
    DocType.OUTBOUND.value: "出库单",
}


@dataclass(frozen=True, slots=True)
class ExportRow:
    """导出的一行明细；已按草稿当前资料或生效单据快照解析完成。"""

    project_name: str | None
    level1_name: str
    level2_name: str | None
    material_name: str
    quantity: str
    manufacturer_name: str | None
    remark: str | None


def build_template(doc_type: str) -> bytes:
    """动态生成入库或出库模板；B1/B2 留空供用户填写。"""
    columns = COLUMN_TITLES[doc_type]
    workbook = Workbook()
    data_sheet = workbook.active
    data_sheet.title = SHEET_DATA
    _write_header_area(data_sheet, None, None)
    _write_column_titles(data_sheet, columns)
    _format_template_rows(data_sheet, columns)
    _set_data_widths(data_sheet, columns)
    _write_help_sheet(workbook, doc_type, exported=False, oversized=False)
    return _save(workbook)


def build_document_export(
    *,
    doc_type: str,
    warehouse_name: str,
    department_name: str,
    rows: list[ExportRow],
    info_rows: list[tuple[str, str]],
    oversized: bool = False,
) -> bytes:
    """生成单张单据导出文件；包含录入数据、填写说明和单据信息。"""
    columns = COLUMN_TITLES[doc_type]
    workbook = Workbook()
    data_sheet = workbook.active
    data_sheet.title = SHEET_DATA
    _write_header_area(data_sheet, warehouse_name, department_name)
    _write_column_titles(data_sheet, columns)
    _set_data_widths(data_sheet, columns)

    for index, row in enumerate(rows):
        row_no = FIRST_DATA_ROW + index
        values: dict[str, str | None] = {
            "项目名称": row.project_name,
            "一级分类名称": row.level1_name,
            "二级分类名称": row.level2_name,
            "物资名称": row.material_name,
            "数量": row.quantity,
            "厂家名称": row.manufacturer_name,
            "备注": row.remark,
        }
        for column_index, title in enumerate(columns, start=1):
            _write_text(data_sheet, row_no, column_index, values[title])
        _set_text_format(data_sheet, row_no, columns.index(QUANTITY_COLUMN) + 1)

    _write_help_sheet(workbook, doc_type, exported=True, oversized=oversized)
    info_sheet = workbook.create_sheet(SHEET_INFO)
    _write_info_sheet(info_sheet, info_rows)
    return _save(workbook)


def template_filenames(doc_type: str) -> tuple[str, str]:
    """返回模板的 ASCII 回退名与中文文件名。"""
    if doc_type == DocType.INBOUND.value:
        return (
            "INBOUND_V1_template.xlsx",
            "入库单快速录入模板_INBOUND_V1.xlsx",
        )
    return (
        "OUTBOUND_V1_template.xlsx",
        "出库单快速录入模板_OUTBOUND_V1.xlsx",
    )


def export_filenames(doc_type: str, doc_no: str) -> tuple[str, str]:
    """返回导出的 ASCII 回退名与中文文件名；单号只保留安全字符。"""
    safe_doc_no = (
        doc_no if _SAFE_DOC_NO.fullmatch(doc_no) else re.sub(r"[^A-Za-z0-9-]+", "_", doc_no)
    )
    safe_doc_no = safe_doc_no.strip("_") or "document"
    ascii_name = f"{doc_type}_{safe_doc_no}.xlsx"
    chinese_name = f"{FILE_TYPE_LABELS[doc_type]}_{safe_doc_no}.xlsx"
    return ascii_name, chinese_name


def content_disposition(ascii_name: str, chinese_name: str) -> str:
    """RFC 5987 文件名头；ASCII 回退名不允许出现引号或控制字符。"""
    from urllib.parse import quote

    safe_ascii = _SAFE_FILENAME.sub("_", ascii_name)
    return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{quote(chinese_name, safe='')}"


def _write_header_area(
    sheet: Worksheet,
    warehouse_name: str | None,
    department_name: str | None,
) -> None:
    _write_text(sheet, 1, 1, HEADER_WAREHOUSE_LABEL)
    _write_text(sheet, 2, 1, HEADER_DEPARTMENT_LABEL)
    if warehouse_name is not None:
        _write_text(sheet, 1, 2, warehouse_name)
    if department_name is not None:
        _write_text(sheet, 2, 2, department_name)
    for row in range(1, HEADER_LAST_ROW + 1):
        sheet.cell(row=row, column=1).font = _BOLD
        sheet.cell(row=row, column=1).fill = _HEADER_FILL
    sheet.row_dimensions[1].height = 20
    sheet.row_dimensions[2].height = 20


def _write_column_titles(sheet: Worksheet, columns: tuple[str, ...]) -> None:
    for index, title in enumerate(columns, start=1):
        cell = _write_text(sheet, TITLE_ROW, index, title)
        cell.font = _BOLD
        cell.fill = _TITLE_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[TITLE_ROW].height = 24


def _format_template_rows(sheet: Worksheet, columns: tuple[str, ...]) -> None:
    quantity_index = columns.index(QUANTITY_COLUMN) + 1
    for row_no in range(FIRST_DATA_ROW, FIRST_DATA_ROW + MAX_DATA_ROWS):
        _set_text_format(sheet, row_no, quantity_index)


def _set_text_format(sheet: Worksheet, row: int, column: int) -> None:
    # 数量列预设文本格式，避免 Excel 把输入值改写为浮点。
    sheet.cell(row=row, column=column).number_format = "@"


def _set_data_widths(sheet: Worksheet, columns: tuple[str, ...]) -> None:
    widths = {
        "项目名称": 22,
        "一级分类名称": 20,
        "二级分类名称": 20,
        "物资名称": 24,
        "数量": 14,
        "厂家名称": 20,
        "备注": 40,
    }
    from openpyxl.utils import get_column_letter

    for index, title in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = widths[title]
    sheet.freeze_panes = "A4"


def _write_help_sheet(
    workbook: Workbook, doc_type: str, *, exported: bool, oversized: bool
) -> None:
    sheet = workbook.create_sheet(SHEET_HELP)
    rows = _help_rows(doc_type, exported=exported, oversized=oversized)
    for index, (label, value) in enumerate(rows, start=1):
        label_cell = _write_text(sheet, index, 1, label)
        label_cell.font = _BOLD
        label_cell.fill = _HEADER_FILL
        _write_text(sheet, index, 2, value).alignment = _WRAP
    sheet.column_dimensions["A"].width = 20
    sheet.column_dimensions["B"].width = 100
    sheet.freeze_panes = "A2"


def _help_rows(doc_type: str, *, exported: bool, oversized: bool) -> list[tuple[str, str]]:
    inbound = doc_type == DocType.INBOUND.value
    if inbound:
        required = (
            "项目名称可空；一级分类名称、物资名称、数量、厂家名称必填；"
            "二级分类名称、备注可空；备注去首尾空白后最多 500 字符。"
        )
        example = (
            "项目名称：一号项目；一级分类名称：原材料；二级分类名称：钢材；"
            "物资名称：螺纹钢；数量：1.250；厂家名称：钢厂；备注：采购批次 A"
        )
    else:
        required = (
            "项目名称可空；一级分类名称、物资名称、数量必填；"
            "二级分类名称、备注可空；备注去首尾空白后最多 500 字符。"
        )
        example = (
            "项目名称：一号项目；一级分类名称：原材料；二级分类名称：钢材；"
            "物资名称：螺纹钢；数量：1.250；备注：领用出库"
        )

    rows: list[tuple[str, str]] = [
        (TEMPLATE_IDENTIFIER_LABEL, TEMPLATE_IDENTIFIER),
        (TEMPLATE_VERSION_LABEL, TEMPLATE_VERSIONS[doc_type]),
        ("适用单据类型", DOC_TYPE_LABELS[doc_type]),
        (
            "使用范围",
            "仅用于填入新建单据页，不自动保存或提交；最大 5 MiB、2,000 非空数据行。",
        ),
        (
            "工作表",
            "“录入数据”为填单数据；“填写说明”仅作说明；导出的文件可能包含"
            "“单据信息”，该表只作展示，不参与解析。",
        ),
        (
            "表头区",
            "A1/B1 为仓库名称，A2/B2 为管理部门名称；B1、B2 只接受文本，"
            "须填写系统内已启用的名称，一个文件只对应一个单据头。",
        ),
        (
            "明细位置",
            "第 3 行为明细列标题，第 4 行起为明细；空行不计数，隐藏行仍校验。",
        ),
        ("字段必填性", required),
        (
            "数量规则",
            "十进制正数，最多三位小数；整数物资必须为整数；禁止科学计数法。示例：1.250。",
        ),
        ("示例", example),
        (
            "安全说明",
            "服务端不执行宏、外部链接或公式；明细行含公式时按行级错误处理。",
        ),
    ]
    if exported:
        rows.append(
            (
                "导出说明",
                "该文件由系统导出，可作留档或快速录入参考；再次解析仍按当前资料"
                "名称匹配和校验，不保证历史文件可原样填入。",
            )
        )
    if oversized:
        rows.append(
            (
                "快速录入提示",
                f"本单明细超过 {MAX_DATA_ROWS:,} 行，不能用于快速录入。",
            )
        )
    return rows


def _write_info_sheet(sheet: Worksheet, info_rows: list[tuple[str, str]]) -> None:
    header = _write_text(sheet, 1, 1, "系统字段")
    header.font = _BOLD
    header.fill = _TITLE_FILL
    value_header = _write_text(sheet, 1, 2, "值")
    value_header.font = _BOLD
    value_header.fill = _TITLE_FILL
    for index, (label, value) in enumerate(info_rows, start=2):
        _write_text(sheet, index, 1, label).font = _BOLD
        _write_text(sheet, index, 2, value).alignment = _WRAP
    sheet.column_dimensions["A"].width = 20
    sheet.column_dimensions["B"].width = 80


def _write_text(sheet: Worksheet, row: int, column: int, value: str | None):
    """写入字符串单元格；显式保持 ``s`` 类型，防止用户文本被解释为公式。"""
    cell = sheet.cell(row=row, column=column)
    if value is None:
        return cell
    cell.value = str(value)
    cell.data_type = "s"
    return cell


def _save(workbook: Workbook) -> bytes:
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
