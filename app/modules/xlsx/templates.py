"""XLSX 模板与导出工作簿生成。

依据 docs/07-XLSX模板、快速录入与导出.md 7.8.1、7.8.5：
- “录入数据”第 1 行为单据标题，第 2 行为仓库、管理部门表头区，
  第 3 行为明细列标题，第 4 行起为明细；
- 入库含厂家列，出库无厂家列，备注逐行且为最后一列；
- 模板填写区除数量、备注外提供下拉选项；模板不含责任栏；
- 导出单据标题附带正式单号，明细下方保留 4 个责任人栏且姓名留空；
- 导出的数量写入文本单元格并保留三位小数；
- 所有用户可控文本强制按字符串单元格写入，避免 ``=``、``+``、``-``、``@`` 被当作公式。
"""

import re
from dataclasses import dataclass
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from app.modules.documents.models import DocType
from app.modules.xlsx.constants import (
    COLUMN_TITLE_ROW,
    COLUMN_TITLES,
    DOCUMENT_TITLE_LABELS,
    DOCUMENT_TITLE_ROW,
    FIRST_DATA_ROW,
    HEADER_DEPARTMENT_LABEL,
    HEADER_DEPARTMENT_LABEL_COLUMN,
    HEADER_DEPARTMENT_VALUE_COLUMN,
    HEADER_ROW,
    HEADER_WAREHOUSE_LABEL,
    HEADER_WAREHOUSE_LABEL_COLUMN,
    HEADER_WAREHOUSE_VALUE_COLUMN,
    LEVEL1_COLUMN,
    LEVEL2_COLUMN,
    MANUFACTURER_COLUMN,
    MATERIAL_COLUMN,
    MAX_DATA_ROW,
    MAX_DATA_ROWS,
    OPTION_FIELDS,
    PROJECT_COLUMN,
    QUANTITY_COLUMN,
    REMARK_COLUMN,
    RESPONSIBLE_LABELS,
    SHEET_DATA,
    SHEET_HELP,
    SHEET_INFO,
    SHEET_OPTIONS,
    TEMPLATE_IDENTIFIER,
    TEMPLATE_IDENTIFIER_LABEL,
    TEMPLATE_VERSION_LABEL,
    TEMPLATE_VERSIONS,
)

_TITLE_FONT = Font(name="宋体", size=18, bold=True)
_BOLD = Font(name="宋体", size=11, bold=True)
_NORMAL = Font(name="宋体", size=11)
_HEADER_FILL = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
_TITLE_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
_WRAP = Alignment(wrap_text=True, vertical="top")
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
_DATA_CENTER = Alignment(horizontal="center", vertical="center")
_TITLE_ALIGNMENT = Alignment(horizontal="center", vertical="center")
_THIN = Side(style="thin", color="000000")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

# 与上传模板一致的列宽（Excel 字符宽度）。
_COLUMN_WIDTHS: dict[str, dict[str, float]] = {
    DocType.INBOUND.value: {
        LEVEL1_COLUMN: 17.6363636363636,
        LEVEL2_COLUMN: 15.3636363636364,
        MATERIAL_COLUMN: 14.3636363636364,
        QUANTITY_COLUMN: 13.4545454545455,
        MANUFACTURER_COLUMN: 20.6363636363636,
        PROJECT_COLUMN: 29.9090909090909,
        REMARK_COLUMN: 17.4545454545455,
    },
    DocType.OUTBOUND.value: {
        LEVEL1_COLUMN: 17.6363636363636,
        LEVEL2_COLUMN: 15.3636363636364,
        MATERIAL_COLUMN: 14.3636363636364,
        QUANTITY_COLUMN: 13.4545454545455,
        PROJECT_COLUMN: 25.0909090909091,
        REMARK_COLUMN: 32.6363636363636,
    },
}

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


@dataclass(frozen=True, slots=True)
class TemplateOptions:
    """模板填写区下拉选项；由调用方按当前基础资料加载。"""

    project_names: tuple[str, ...] = ()
    level1_names: tuple[str, ...] = ()
    level2_names: tuple[str, ...] = ()
    material_names: tuple[str, ...] = ()
    manufacturer_names: tuple[str, ...] = ()
    warehouse_names: tuple[str, ...] = ()
    department_names: tuple[str, ...] = ()


def build_template(doc_type: str, *, options: TemplateOptions | None = None) -> bytes:
    """动态生成入库或出库模板；B2/F2 留空供用户选择。"""
    columns = COLUMN_TITLES[doc_type]
    workbook = Workbook()
    data_sheet = workbook.active
    data_sheet.title = SHEET_DATA
    _write_document_title(data_sheet, columns, DOCUMENT_TITLE_LABELS[doc_type])
    _write_header_area(data_sheet, None, None)
    _write_column_titles(data_sheet, columns)
    _format_template_rows(data_sheet, columns)
    _set_data_widths(data_sheet, doc_type, columns)
    _write_help_sheet(workbook, doc_type, exported=False, oversized=False)
    _add_option_sheet(
        workbook,
        data_sheet,
        doc_type,
        columns,
        options if options is not None else TemplateOptions(),
    )
    return _save(workbook)


def build_document_export(
    *,
    doc_type: str,
    doc_no: str,
    warehouse_name: str,
    department_name: str,
    rows: list[ExportRow],
    info_rows: list[tuple[str, str]],
    oversized: bool = False,
) -> bytes:
    """生成单张单据导出文件；标题含单号，明细下方保留空姓名责任栏。"""
    columns = COLUMN_TITLES[doc_type]
    workbook = Workbook()
    data_sheet = workbook.active
    data_sheet.title = SHEET_DATA
    title = f"{DOCUMENT_TITLE_LABELS[doc_type]}（{doc_no}）"
    _write_document_title(data_sheet, columns, title)
    _write_header_area(data_sheet, warehouse_name, department_name)
    _write_column_titles(data_sheet, columns)
    _set_data_widths(data_sheet, doc_type, columns)

    for index, row in enumerate(rows):
        row_no = FIRST_DATA_ROW + index
        values: dict[str, str | None] = {
            PROJECT_COLUMN: row.project_name,
            LEVEL1_COLUMN: row.level1_name,
            LEVEL2_COLUMN: row.level2_name,
            MATERIAL_COLUMN: row.material_name,
            QUANTITY_COLUMN: row.quantity,
            MANUFACTURER_COLUMN: row.manufacturer_name,
            REMARK_COLUMN: row.remark,
        }
        for column_index, title_column in enumerate(columns, start=1):
            cell = _write_text(data_sheet, row_no, column_index, values[title_column])
            cell.border = _BORDER
            cell.alignment = _DATA_CENTER
            cell.font = _NORMAL
        _set_text_format(data_sheet, row_no, _column_number(columns, QUANTITY_COLUMN))

    _write_responsible_row(data_sheet, doc_type, len(rows))
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


def _write_document_title(sheet: Worksheet, columns: tuple[str, ...], title: str) -> None:
    sheet.merge_cells(
        start_row=DOCUMENT_TITLE_ROW,
        start_column=1,
        end_row=DOCUMENT_TITLE_ROW,
        end_column=len(columns),
    )
    cell = _write_text(sheet, DOCUMENT_TITLE_ROW, 1, title)
    cell.font = _TITLE_FONT
    cell.alignment = _TITLE_ALIGNMENT
    sheet.row_dimensions[DOCUMENT_TITLE_ROW].height = 38


def _write_header_area(
    sheet: Worksheet,
    warehouse_name: str | None,
    department_name: str | None,
) -> None:
    warehouse_label = _write_text(
        sheet, HEADER_ROW, HEADER_WAREHOUSE_LABEL_COLUMN, HEADER_WAREHOUSE_LABEL
    )
    warehouse_label.font = _BOLD
    _write_text(sheet, HEADER_ROW, HEADER_WAREHOUSE_VALUE_COLUMN, warehouse_name).font = _NORMAL

    department_label = _write_text(
        sheet, HEADER_ROW, HEADER_DEPARTMENT_LABEL_COLUMN, HEADER_DEPARTMENT_LABEL
    )
    department_label.font = _BOLD
    _write_text(sheet, HEADER_ROW, HEADER_DEPARTMENT_VALUE_COLUMN, department_name).font = _NORMAL
    sheet.row_dimensions[HEADER_ROW].height = 20


def _write_column_titles(sheet: Worksheet, columns: tuple[str, ...]) -> None:
    for index, title in enumerate(columns, start=1):
        cell = _write_text(sheet, COLUMN_TITLE_ROW, index, title)
        cell.font = _BOLD
        cell.alignment = _CENTER
        cell.border = _BORDER
    sheet.row_dimensions[COLUMN_TITLE_ROW].height = 24


def _format_template_rows(sheet: Worksheet, columns: tuple[str, ...]) -> None:
    quantity_index = _column_number(columns, QUANTITY_COLUMN)
    for row_no in range(FIRST_DATA_ROW, MAX_DATA_ROW + 1):
        for column_no in range(1, len(columns) + 1):
            cell = sheet.cell(row=row_no, column=column_no)
            cell.border = _BORDER
            cell.alignment = _DATA_CENTER
            cell.font = _NORMAL
        _set_text_format(sheet, row_no, quantity_index)


def _write_responsible_row(sheet: Worksheet, doc_type: str, data_row_count: int) -> None:
    """在最后一条明细下空一行写 4 个责任栏标签，姓名不填写。"""
    last_data_row = FIRST_DATA_ROW + data_row_count - 1 if data_row_count else COLUMN_TITLE_ROW
    row_no = max(last_data_row, COLUMN_TITLE_ROW) + 2
    for column_no, label in RESPONSIBLE_LABELS[doc_type]:
        _write_text(sheet, row_no, column_no, label).font = _NORMAL


def _add_option_sheet(
    workbook: Workbook,
    data_sheet: Worksheet,
    doc_type: str,
    columns: tuple[str, ...],
    options: TemplateOptions,
) -> None:
    """写入隐藏的“下拉选项”表，并把名称列表绑定为填写区数据有效性。"""
    option_sheet = workbook.create_sheet(SHEET_OPTIONS)
    option_sheet.sheet_state = "hidden"

    option_values: dict[str, tuple[str, ...]] = {
        HEADER_WAREHOUSE_LABEL: options.warehouse_names,
        HEADER_DEPARTMENT_LABEL: options.department_names,
        PROJECT_COLUMN: options.project_names,
        LEVEL1_COLUMN: options.level1_names,
        LEVEL2_COLUMN: options.level2_names,
        MATERIAL_COLUMN: options.material_names,
        MANUFACTURER_COLUMN: options.manufacturer_names,
    }

    option_column = 1
    for title in (HEADER_WAREHOUSE_LABEL, HEADER_DEPARTMENT_LABEL, *columns):
        names = tuple(dict.fromkeys(option_values.get(title, ())))
        if not names:
            continue

        option_letter = get_column_letter(option_column)
        for row_no, name in enumerate(names, start=1):
            _write_text(option_sheet, row_no, option_column, name)
        option_sheet.column_dimensions[option_letter].width = 24

        defined_name = f"ONLY_STOCK_{doc_type}_{OPTION_FIELDS[title]}"
        workbook.defined_names.add(
            DefinedName(
                defined_name,
                attr_text=(f"'{SHEET_OPTIONS}'!${option_letter}$1:${option_letter}${len(names)}"),
            )
        )

        validation = DataValidation(
            type="list",
            formula1=defined_name,
            allow_blank=True,
            showDropDown=False,
            showErrorMessage=True,
            errorStyle="stop",
            errorTitle="选项无效",
            error=f"请从下拉列表选择有效的{title}。",
        )
        if title == HEADER_WAREHOUSE_LABEL:
            target = f"{get_column_letter(HEADER_WAREHOUSE_VALUE_COLUMN)}{HEADER_ROW}"
        elif title == HEADER_DEPARTMENT_LABEL:
            target = f"{get_column_letter(HEADER_DEPARTMENT_VALUE_COLUMN)}{HEADER_ROW}"
        else:
            data_letter = get_column_letter(_column_number(columns, title))
            target = f"{data_letter}{FIRST_DATA_ROW}:{data_letter}{MAX_DATA_ROW}"
        validation.add(target)
        data_sheet.add_data_validation(validation)
        option_column += 1


def _set_text_format(sheet: Worksheet, row: int, column: int) -> None:
    # 数量列预设文本格式，避免 Excel 把输入值改写为浮点。
    sheet.cell(row=row, column=column).number_format = "@"


def _set_data_widths(sheet: Worksheet, doc_type: str, columns: tuple[str, ...]) -> None:
    widths = _COLUMN_WIDTHS[doc_type]
    for index, title in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = widths[title]
    if doc_type == DocType.OUTBOUND.value:
        # 与原模板保持一致，保留备注列之后的历史列宽。
        sheet.column_dimensions[get_column_letter(len(columns) + 1)].width = 17.4545454545455
    sheet.freeze_panes = "A4"
    sheet.print_options.gridLines = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = 9


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
            "一级分类名称：原材料；二级分类名称：钢材；物资名称：螺纹钢；"
            "数量：1.250；厂家名称：钢厂；项目名称：一号项目；备注：采购批次 A"
        )
    else:
        required = (
            "项目名称可空；一级分类名称、物资名称、数量必填；"
            "二级分类名称、备注可空；备注去首尾空白后最多 500 字符。"
        )
        example = (
            "一级分类名称：原材料；二级分类名称：钢材；物资名称：螺纹钢；"
            "数量：1.250；项目名称：一号项目；备注：领用出库"
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
            "“录入数据”为填单数据；“填写说明”仅作说明；模板另含隐藏的“下拉选项”表，"
            "仅用于下拉来源；导出的文件可能包含“单据信息”，该表只作展示，不参与解析。",
        ),
        (
            "表头区",
            "第 1 行为单据标题；A2/B2 为仓库名称，E2/F2 为管理部门名称；"
            "模板 B2、F2 留空，须填写系统内已启用的名称，一个文件只对应一个单据头。",
        ),
        (
            "明细位置",
            "第 3 行为明细列标题，第 4 行起为明细；空行不计数，隐藏行仍校验。",
        ),
        (
            "下拉填写",
            "B2、F2 及一级分类名称、二级分类名称、物资名称、项目名称"
            "（入库另含厂家名称）为下拉选项；数量列按文本填写，备注为自由文本。"
            "下拉只作输入提示，服务端仍会重新校验。",
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
                "责任栏",
                "明细下方保留送货人、接收人、审核人、批准人（出库为领用人、出库人、"
                "审核人、批准人）责任栏，姓名留空；再次解析时忽略责任栏及其后内容。",
            )
        )
        rows.append(
            (
                "导出说明",
                "该文件由系统导出，可作留档或快速录入参考；再次解析仍按当前资料"
                "名称匹配和校验，不保证历史文件可原样填入。",
            )
        )
    else:
        rows.append(
            (
                "责任栏",
                "模板不含责任栏；单张单据导出时在明细下方保留 4 个责任人栏。",
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


def _column_number(columns: tuple[str, ...], title: str) -> int:
    return columns.index(title) + 1


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
