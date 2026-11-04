"""XLSX 只读解析与名称解析。

依据 docs/07-XLSX模板、快速录入与导出.md 7.8.1–7.8.4：
- 文件级错误整份拒绝，不返回单据数据；
- 行级错误跳过该行，其余有效行返回填单数据；
- 表头区只解析一次，明细行项目逐行解析，备注逐行独立；
- 同一物资同一项目重复按行级错误处理，不合并数量；
- 解析不写数据库、不写审计、不保存解析状态。
"""

import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree.ElementTree import ParseError

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.cell.read_only import EMPTY_CELL
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy.orm import Session

from app.errors import AppError
from app.modules.categories.models import Category
from app.modules.departments.models import Department
from app.modules.documents.models import DocType
from app.modules.inventory.quantity import (
    ensure_quantity_matches_type,
    format_quantity_milli,
    parse_quantity,
    quantity_to_milli,
)
from app.modules.manufacturers.models import Manufacturer
from app.modules.materials.models import Material
from app.modules.projects.models import Project
from app.modules.warehouses.models import Warehouse
from app.modules.xlsx.archive import (
    ArchiveTooComplex,
    RowLimitExceeded,
    SheetScan,
    SparseRows,
    scan_data_sheet,
    validate_archive,
)
from app.modules.xlsx.constants import (
    ALLOWED_SHEETS,
    COLUMN_TITLES,
    FIRST_DATA_ROW,
    HEADER_DEPARTMENT_LABEL,
    HEADER_WAREHOUSE_LABEL,
    LEVEL1_COLUMN,
    LEVEL2_COLUMN,
    MANUFACTURER_COLUMN,
    MATERIAL_COLUMN,
    MAX_DATA_ROWS,
    MAX_METADATA_ROWS,
    MAX_SHEET_SCAN_ROW,
    PARTIAL_NOTICE,
    PROJECT_COLUMN,
    QUANTITY_COLUMN,
    REMARK_COLUMN,
    SHEET_DATA,
    SHEET_HELP,
    SUCCESS_NOTICE,
    TEMPLATE_IDENTIFIER,
    TEMPLATE_IDENTIFIER_LABEL,
    TEMPLATE_VERSION_LABEL,
    TEMPLATE_VERSIONS,
    TITLE_ROW,
)
from app.modules.xlsx.errors import file_error
from app.modules.xlsx.names import (
    find_by_name,
    load_level1_categories,
    load_level2_categories,
    load_manufacturers,
    load_materials,
    load_projects,
)


@dataclass(frozen=True, slots=True)
class RowIssue:
    """一条行级错误；``source_row_no`` 为 Excel 原始行号。"""

    source_row_no: int
    error_field: str
    error_code: str
    message: str


@dataclass(frozen=True, slots=True)
class ParsedLine:
    """可填入新建页的一条有效明细。"""

    material_id: str
    material_name: str
    unit: str
    quantity_type: str
    quantity_milli: int
    quantity: str
    manufacturer_id: str | None
    manufacturer_name: str | None
    project_id: str | None
    project_name: str | None
    category_name: str
    parent_category_name: str | None
    remark: str | None


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """一次无副作用解析的结果。"""

    doc_type: str
    warehouse_id: str
    warehouse_name: str
    department_id: str
    department_name: str
    lines: tuple[ParsedLine, ...]
    total_rows: int
    errors: tuple[RowIssue, ...] = field(default_factory=tuple)

    @property
    def status(self) -> str:
        return "PARTIAL" if self.errors else "SUCCESS"

    @property
    def notice(self) -> str:
        return PARTIAL_NOTICE if self.errors else SUCCESS_NOTICE


class _RowProblem(Exception):
    """行级校验失败；携带错误字段、错误码和面向页面的原因。"""

    def __init__(self, field: str, code: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.code = code
        self.message = message


@dataclass(slots=True)
class _RawRow:
    row_no: int
    project_name: str = ""
    level1_name: str = ""
    level2_name: str = ""
    material_name: str = ""
    manufacturer_name: str = ""
    quantity_milli: int = 0
    remark: str | None = None
    error: RowIssue | None = None


def parse_document_entry(
    path: str | Path,
    *,
    doc_type: str,
    session: Session,
) -> ParsedDocument:
    """解析上传文件；文件级失败抛 ``AppError``，行级失败只跳过该行。"""
    if doc_type not in COLUMN_TITLES:
        raise file_error(
            "VALIDATION_ERROR",
            "单据类型必须是 INBOUND 或 OUTBOUND",
            resource="document",
            items=[{"field": "doc_type", "reason": "未知单据类型"}],
        )
    expected_columns = COLUMN_TITLES[doc_type]

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise file_error("XLSX_INVALID_FILE", "文件不是有效的 XLSX 工作簿") from exc

    with archive:
        validate_archive(archive)
        workbook = _load_workbook(path)
        try:
            try:
                _validate_sheets(workbook.sheetnames)
                _validate_template_metadata(workbook, doc_type)
                data_sheet = _require_worksheet(workbook, SHEET_DATA)
                _validate_structure(data_sheet, expected_columns, doc_type)
                warehouse = _resolve_header_entity(
                    session,
                    _header_value(data_sheet.cell(row=1, column=2), HEADER_WAREHOUSE_LABEL),
                    Warehouse,
                    resource="warehouse",
                    label=HEADER_WAREHOUSE_LABEL,
                )
                department = _resolve_header_entity(
                    session,
                    _header_value(data_sheet.cell(row=2, column=2), HEADER_DEPARTMENT_LABEL),
                    Department,
                    resource="department",
                    label=HEADER_DEPARTMENT_LABEL,
                )
                scan = _scan_sheet(archive, workbook, expected_columns)
                raw_rows, total_rows = _read_rows(
                    data_sheet,
                    expected_columns,
                    doc_type,
                    scan,
                )
                if total_rows == 0:
                    raise _no_valid_rows(0)
                if total_rows > MAX_DATA_ROWS:
                    raise _row_limit_error(total_rows)
                lines, errors = _resolve_rows(
                    session,
                    doc_type,
                    raw_rows,
                    warehouse=warehouse,
                    department=department,
                )
                if not lines:
                    raise _no_valid_rows(total_rows)
                return ParsedDocument(
                    doc_type=doc_type,
                    warehouse_id=warehouse.id,
                    warehouse_name=warehouse.name,
                    department_id=department.id,
                    department_name=department.name,
                    lines=tuple(lines),
                    total_rows=total_rows,
                    errors=tuple(errors),
                )
            except AppError:
                raise
            except _RowProblem as exc:
                raise file_error("XLSX_INVALID_FILE", "XLSX 明细结构异常") from exc
            except (
                KeyError,
                ValueError,
                IndexError,
                TypeError,
                ParseError,
                OSError,
                RuntimeError,
                zipfile.BadZipFile,
            ) as exc:
                raise file_error("XLSX_INVALID_FILE", "XLSX 无法解析") from exc
        finally:
            workbook.close()


def _load_workbook(path: str | Path):
    try:
        return load_workbook(
            path,
            read_only=True,
            data_only=False,
            keep_links=False,
            keep_vba=False,
        )
    except (
        InvalidFileException,
        ParseError,
        KeyError,
        ValueError,
        TypeError,
        OSError,
        EOFError,
        zipfile.BadZipFile,
    ) as exc:
        raise file_error("XLSX_INVALID_FILE", "文件不是有效的 XLSX 工作簿") from exc


def _require_worksheet(workbook, name: str):
    """只接受普通工作表；图表工作表等非填单结构按文件级错误拒绝。"""
    try:
        sheet = workbook[name]
    except KeyError as exc:
        raise file_error(
            "XLSX_WORKSHEET_INVALID",
            "XLSX 缺少必需的工作表",
            items=[{"field": "工作表", "reason": f"缺少：{name}"}],
        ) from exc
    if not hasattr(sheet, "iter_rows") or not hasattr(sheet, "cell"):
        raise file_error(
            "XLSX_WORKSHEET_INVALID",
            "XLSX 工作表类型不受支持",
            items=[{"field": "工作表", "reason": f"{name} 不是普通工作表"}],
        )
    return sheet


def _validate_sheets(sheetnames: list[str]) -> None:
    missing = {SHEET_DATA, SHEET_HELP} - set(sheetnames)
    if missing:
        raise file_error(
            "XLSX_WORKSHEET_INVALID",
            "XLSX 缺少必需的工作表",
            items=[{"field": "工作表", "reason": f"缺少：{'、'.join(sorted(missing))}"}],
        )
    extras = set(sheetnames) - ALLOWED_SHEETS
    if extras:
        raise file_error(
            "XLSX_WORKSHEET_INVALID",
            "XLSX 包含不允许的额外工作表",
            items=[{"field": "工作表", "reason": f"多余：{'、'.join(sorted(extras))}"}],
        )


def _validate_template_metadata(workbook, doc_type: str) -> None:
    sheet = _require_worksheet(workbook, SHEET_HELP)
    identifier: str | None = None
    version: str | None = None
    for row in sheet.iter_rows(min_row=1, max_row=MAX_METADATA_ROWS, max_col=2, values_only=False):
        label = _metadata_text(row[0])
        value = _metadata_text(row[1])
        if label == TEMPLATE_IDENTIFIER_LABEL:
            identifier = value
        elif label == TEMPLATE_VERSION_LABEL:
            version = value

    if identifier != TEMPLATE_IDENTIFIER or version not in TEMPLATE_VERSIONS.values():
        raise file_error(
            "XLSX_TEMPLATE_UNSUPPORTED",
            "模板标识或版本不受支持",
            items=[{"field": TEMPLATE_VERSION_LABEL, "reason": "请使用系统下载的模板"}],
        )
    if version != TEMPLATE_VERSIONS[doc_type]:
        raise file_error(
            "XLSX_TEMPLATE_MISMATCH",
            "上传模板类型与所选单据类型不一致",
            items=[
                {
                    "field": "doc_type",
                    "template_version": version,
                    "expected_version": TEMPLATE_VERSIONS[doc_type],
                }
            ],
        )


def _validate_structure(sheet, columns: tuple[str, ...], doc_type: str) -> None:
    max_column = max(len(columns) + 16, 32)
    rows = list(
        sheet.iter_rows(
            min_row=1,
            max_row=TITLE_ROW,
            max_col=max_column,
            values_only=False,
        )
    )
    header_rows = [list(row) + [EMPTY_CELL] * (max_column - len(row)) for row in rows]
    while len(header_rows) < TITLE_ROW:
        header_rows.append([EMPTY_CELL] * max_column)
    first_row = header_rows[0]
    second_row = header_rows[1]
    title_row = header_rows[2]

    if _cell_text(first_row[0]) != HEADER_WAREHOUSE_LABEL:
        raise file_error(
            "XLSX_HEADER_INVALID",
            "表头区仓库名称标签缺失或位置不符",
            items=[{"field": "A1", "expected": HEADER_WAREHOUSE_LABEL}],
        )
    if _cell_text(second_row[0]) != HEADER_DEPARTMENT_LABEL:
        raise file_error(
            "XLSX_HEADER_INVALID",
            "表头区管理部门名称标签缺失或位置不符",
            items=[{"field": "A2", "expected": HEADER_DEPARTMENT_LABEL}],
        )

    if doc_type == DocType.OUTBOUND.value:
        title_values = [_cell_text(cell) for cell in title_row]
        if MANUFACTURER_COLUMN in title_values:
            raise file_error(
                "XLSX_MANUFACTURER_COLUMN_FORBIDDEN",
                "出库模板不允许出现厂家列",
                items=[{"field": TITLE_ROW, "column": MANUFACTURER_COLUMN}],
            )

    for index, expected in enumerate(columns):
        actual = _cell_text(title_row[index]) if index < len(title_row) else ""
        if actual != expected:
            raise file_error(
                "XLSX_COLUMNS_INVALID",
                "明细列标题缺失、重复或顺序不符",
                items=[
                    {
                        "field": f"第 {TITLE_ROW} 行第 {index + 1} 列",
                        "expected": expected,
                        "actual": actual,
                    }
                ],
            )

    for index in range(len(columns), max_column):
        if index < len(title_row) and _cell_text(title_row[index]):
            raise file_error(
                "XLSX_COLUMNS_INVALID",
                "明细列之后存在多余列标题",
                items=[{"field": f"第 {TITLE_ROW} 行第 {index + 1} 列"}],
            )


def _scan_sheet(
    archive: zipfile.ZipFile,
    workbook,
    columns: tuple[str, ...],
) -> SheetScan:
    sheet_path = getattr(_require_worksheet(workbook, SHEET_DATA), "_worksheet_path", None)
    if not sheet_path:
        raise file_error("XLSX_WORKSHEET_INVALID", "无法读取录入数据工作表")
    try:
        return scan_data_sheet(archive, str(sheet_path), expected_columns=len(columns))
    except RowLimitExceeded as exc:
        raise _row_limit_error(exc.count) from exc
    except SparseRows as exc:
        raise file_error(
            "XLSX_WORKSHEET_INVALID",
            "工作表明细行位置异常，已拒绝",
            items=[{"field": "明细", "reason": "非空数据行位置超出允许范围"}],
        ) from exc
    except ArchiveTooComplex as exc:
        raise file_error(
            "XLSX_WORKSHEET_INVALID",
            "工作表数据过于复杂，已拒绝",
            items=[{"field": "明细", "reason": "数值单元格数量超过安全上限"}],
        ) from exc


def _read_rows(
    sheet,
    columns: tuple[str, ...],
    doc_type: str,
    scan: SheetScan,
) -> tuple[list[_RawRow], int]:
    raw_rows: list[_RawRow] = []
    total_rows = 0
    inbound = doc_type == DocType.INBOUND.value

    for offset, cells in enumerate(
        sheet.iter_rows(
            min_row=FIRST_DATA_ROW,
            max_row=MAX_SHEET_SCAN_ROW,
            max_col=len(columns),
            values_only=False,
        )
    ):
        row_no = FIRST_DATA_ROW + offset
        values = [cell.value for cell in cells]
        if _blank_row(values):
            continue
        total_rows += 1
        if total_rows > MAX_DATA_ROWS:
            raise _row_limit_error(total_rows)

        raw = _RawRow(row_no=row_no)
        try:
            raw.project_name = _text_cell(_cell_at(cells, PROJECT_COLUMN, columns), PROJECT_COLUMN)
            raw.level1_name = _text_cell(_cell_at(cells, LEVEL1_COLUMN, columns), LEVEL1_COLUMN)
            raw.level2_name = _text_cell(_cell_at(cells, LEVEL2_COLUMN, columns), LEVEL2_COLUMN)
            raw.material_name = _text_cell(
                _cell_at(cells, MATERIAL_COLUMN, columns), MATERIAL_COLUMN
            )
            quantity_cell = _cell_at(cells, QUANTITY_COLUMN, columns)
            raw.quantity_milli = _read_quantity(
                quantity_cell,
                raw_numbers=scan.raw_numbers,
                row_no=row_no,
                column_no=columns.index(QUANTITY_COLUMN) + 1,
            )
            if inbound:
                raw.manufacturer_name = _text_cell(
                    _cell_at(cells, MANUFACTURER_COLUMN, columns), MANUFACTURER_COLUMN
                )
            raw.remark = _read_remark(_cell_at(cells, REMARK_COLUMN, columns), REMARK_COLUMN)
            _require_fields(raw, inbound=inbound)
        except _RowProblem as problem:
            raw.error = RowIssue(
                source_row_no=row_no,
                error_field=problem.field,
                error_code=problem.code,
                message=problem.message,
            )
        raw_rows.append(raw)

    return raw_rows, total_rows


def _resolve_rows(
    session: Session,
    doc_type: str,
    raw_rows: list[_RawRow],
    *,
    warehouse: Warehouse,
    department: Department,
) -> tuple[list[ParsedLine], list[RowIssue]]:
    inbound = doc_type == DocType.INBOUND.value
    pending = [row for row in raw_rows if row.error is None]

    projects = load_projects(session, [row.project_name for row in pending if row.project_name])
    manufacturers = (
        load_manufacturers(
            session, [row.manufacturer_name for row in pending if row.manufacturer_name]
        )
        if inbound
        else {}
    )
    level1_categories = load_level1_categories(
        session, [row.level1_name for row in pending if row.level1_name]
    )

    level2_pairs: list[tuple[str, str]] = []
    for row in pending:
        if not row.level2_name:
            continue
        parent = level1_categories.get(row.level1_name)
        if parent is not None:
            level2_pairs.append((parent.id, row.level2_name))
    level2_categories = load_level2_categories(session, level2_pairs)

    category_ids: list[str] = []
    for row in pending:
        parent = level1_categories.get(row.level1_name)
        if parent is None:
            continue
        if row.level2_name:
            child = level2_categories.get((parent.id, row.level2_name))
            if child is not None:
                category_ids.append(child.id)
        else:
            category_ids.append(parent.id)
    materials = load_materials(
        session,
        [row.material_name for row in pending if row.material_name],
        category_ids,
    )

    lines: list[ParsedLine] = []
    errors: list[RowIssue] = []
    seen: dict[tuple[str, str | None], int] = {}

    for row in raw_rows:
        if row.error is not None:
            errors.append(row.error)
            continue
        try:
            project = _resolve_project(projects, row)
            parent = _resolve_level1(level1_categories, row)
            category = _resolve_category(level2_categories, row, parent)
            material = _resolve_material(materials, row, category)
            if inbound:
                _ensure_inbound_active(material, category, parent, row)
            _ensure_quantity_type(material, row)
            manufacturer = _resolve_manufacturer(manufacturers, row) if inbound else None
            key = (material.id, None if project is None else project.id)
            if key in seen:
                raise _RowProblem(
                    MATERIAL_COLUMN,
                    "ROW_DUPLICATE",
                    f"同一物资同一项目与第 {seen[key]} 行重复，本行未填入",
                )
        except _RowProblem as problem:
            errors.append(
                RowIssue(
                    source_row_no=row.row_no,
                    error_field=problem.field,
                    error_code=problem.code,
                    message=problem.message,
                )
            )
            continue

        seen[key] = row.row_no
        lines.append(
            ParsedLine(
                material_id=material.id,
                material_name=material.name,
                unit=material.unit,
                quantity_type=material.quantity_type,
                quantity_milli=row.quantity_milli,
                quantity=format_quantity_milli(row.quantity_milli),
                manufacturer_id=None if manufacturer is None else manufacturer.id,
                manufacturer_name=None if manufacturer is None else manufacturer.name,
                project_id=None if project is None else project.id,
                project_name=None if project is None else project.name,
                category_name=category.name,
                parent_category_name=(None if category is parent else parent.name),
                remark=row.remark,
            )
        )

    return lines, errors


def _resolve_project(projects: dict[str, Project], row: _RawRow) -> Project | None:
    if not row.project_name:
        return None
    project = projects.get(row.project_name)
    if project is None:
        raise _RowProblem(PROJECT_COLUMN, "PROJECT_NOT_FOUND", "未找到匹配的项目名称")
    if not project.is_active:
        raise _RowProblem(PROJECT_COLUMN, "PROJECT_INACTIVE", "项目已停用，本行未填入")
    return project


def _resolve_level1(categories: dict[str, Category], row: _RawRow) -> Category:
    category = categories.get(row.level1_name)
    if category is None:
        raise _RowProblem(LEVEL1_COLUMN, "CATEGORY_NOT_FOUND", "未找到匹配的一级分类名称")
    return category


def _resolve_category(
    level2_categories: dict[tuple[str, str], Category],
    row: _RawRow,
    parent: Category,
) -> Category:
    if not row.level2_name:
        return parent
    category = level2_categories.get((parent.id, row.level2_name))
    if category is None:
        raise _RowProblem(
            LEVEL2_COLUMN,
            "CATEGORY_NOT_FOUND",
            "未找到该一级分类下的二级分类名称",
        )
    return category


def _resolve_material(
    materials: dict[tuple[str, str], list[Material]],
    row: _RawRow,
    category: Category,
) -> Material:
    candidates = materials.get((category.id, row.material_name), [])
    if not candidates:
        raise _RowProblem(MATERIAL_COLUMN, "MATERIAL_NOT_FOUND", "未找到匹配的物资名称")
    if len(candidates) > 1:
        raise _RowProblem(
            MATERIAL_COLUMN,
            "MATERIAL_NAME_AMBIGUOUS",
            "物资名称在该分类内命中多条记录，本行未填入",
        )
    return candidates[0]


def _ensure_inbound_active(
    material: Material,
    category: Category,
    parent: Category,
    row: _RawRow,
) -> None:
    if not material.is_active:
        raise _RowProblem(MATERIAL_COLUMN, "MATERIAL_INACTIVE", "物资已停用，不能用于入库")
    if not category.is_active:
        field = LEVEL2_COLUMN if row.level2_name else LEVEL1_COLUMN
        raise _RowProblem(field, "CATEGORY_INACTIVE", "分类已停用，不能用于入库")
    if parent is not category and not parent.is_active:
        raise _RowProblem(LEVEL1_COLUMN, "CATEGORY_INACTIVE", "上级分类已停用，不能用于入库")


def _ensure_quantity_type(material: Material, row: _RawRow) -> None:
    try:
        ensure_quantity_matches_type(
            row.quantity_milli, material.quantity_type, field=QUANTITY_COLUMN
        )
    except AppError as exc:
        raise _RowProblem(QUANTITY_COLUMN, "QUANTITY_NOT_INTEGER", _app_error_reason(exc)) from exc


def _resolve_manufacturer(manufacturers: dict[str, Manufacturer], row: _RawRow) -> Manufacturer:
    manufacturer = manufacturers.get(row.manufacturer_name)
    if manufacturer is None:
        raise _RowProblem(MANUFACTURER_COLUMN, "MANUFACTURER_NOT_FOUND", "未找到匹配的厂家名称")
    if not manufacturer.is_active:
        raise _RowProblem(MANUFACTURER_COLUMN, "MANUFACTURER_INACTIVE", "厂家已停用，本行未填入")
    return manufacturer


def _read_quantity(
    cell: Cell,
    *,
    raw_numbers: dict[tuple[int, int], str],
    row_no: int,
    column_no: int,
) -> int:
    if cell.data_type == "f":
        raise _RowProblem(QUANTITY_COLUMN, "FORMULA_NOT_ALLOWED", "数量单元格不能使用公式")
    value = cell.value
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _RowProblem(QUANTITY_COLUMN, "QUANTITY_REQUIRED", "数量不能为空")

    if cell.data_type == "n" or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        raw_value = raw_numbers.get((row_no, column_no))
        if raw_value is None or not raw_value.strip():
            raise _RowProblem(
                QUANTITY_COLUMN,
                "QUANTITY_RAW_MISSING",
                "无法读取数量的原始数值，本行未填入",
            )
        text = raw_value.strip()
        if "e" in text.lower():
            raise _RowProblem(QUANTITY_COLUMN, "QUANTITY_INVALID", "数量不允许科学计数法")
        try:
            decimal_value = Decimal(text)
        except InvalidOperation as exc:
            raise _RowProblem(
                QUANTITY_COLUMN, "QUANTITY_INVALID", "数量不是有效的十进制数"
            ) from exc
        return _parse_quantity(decimal_value)

    if isinstance(value, str):
        return _parse_quantity(value.strip())

    raise _RowProblem(QUANTITY_COLUMN, "QUANTITY_INVALID", "数量必须是十进制数")


def _parse_quantity(value: str | Decimal) -> int:
    try:
        decimal_value = parse_quantity(value, field=QUANTITY_COLUMN)
        return quantity_to_milli(decimal_value, field=QUANTITY_COLUMN)
    except AppError as exc:
        reason = _app_error_reason(exc)
        code = "QUANTITY_PRECISION" if "三位小数" in reason else "QUANTITY_INVALID"
        raise _RowProblem(QUANTITY_COLUMN, code, reason) from exc


def _read_remark(cell: Cell, field: str) -> str | None:
    value = _text_cell(cell, field)
    if not value:
        return None
    if len(value) > 500:
        raise _RowProblem(REMARK_COLUMN, "REMARK_TOO_LONG", "备注最多 500 字符")
    return value


def _text_cell(cell: Cell, field: str) -> str:
    if cell.data_type == "f":
        raise _RowProblem(field, "FORMULA_NOT_ALLOWED", f"{field}单元格不能使用公式")
    value = cell.value
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _RowProblem(field, "FIELD_TYPE_INVALID", f"{field}必须是文本")
    return value.strip()


def _header_value(cell: Cell, field: str) -> str:
    try:
        return _header_text(cell)
    except _RowProblem as problem:
        raise file_error(
            "XLSX_HEADER_INVALID",
            problem.message,
            items=[{"field": field, "reason": problem.message}],
        ) from problem


def _header_text(cell: Cell) -> str:
    if cell.data_type == "f":
        raise _RowProblem("表头区", "FORMULA_NOT_ALLOWED", "表头区不能使用公式")
    value = cell.value
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _RowProblem("表头区", "FIELD_TYPE_INVALID", "表头区只接受文本或空单元格")
    return value.strip()


def _cell_text(cell: Cell) -> str:
    value = cell.value
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    return value.strip()


def _metadata_text(cell: Cell) -> str | None:
    if cell.data_type == "f":
        return None
    value = cell.value
    if not isinstance(value, str):
        return None
    return value.strip()


def _resolve_header_entity(
    session: Session,
    name: str,
    model: type,
    *,
    resource: str,
    label: str,
) -> object:
    if not name:
        raise file_error(
            f"{resource.upper()}_REQUIRED",
            f"表头区{label}不能为空",
            resource=resource,
            items=[{"field": label, "reason": f"{label}不能为空"}],
        )
    row = find_by_name(session, model, name)
    if row is None:
        raise file_error(
            f"{resource.upper()}_NOT_FOUND",
            f"表头区{label}未找到",
            resource=resource,
            items=[{"field": label, "name": name}],
        )
    if not row.is_active:
        raise file_error(
            f"{resource.upper()}_INACTIVE",
            f"表头区{label}已停用",
            resource=resource,
            items=[{"field": label, "name": name}],
        )
    return row


def _cell_at(cells: tuple, title: str, columns: tuple[str, ...]) -> Cell:
    return cells[columns.index(title)]


def _blank_row(values: list[object]) -> bool:
    return all(value is None or (isinstance(value, str) and value == "") for value in values)


def _require_fields(row: _RawRow, *, inbound: bool) -> None:
    if not row.level1_name:
        raise _RowProblem(LEVEL1_COLUMN, "CATEGORY_NAME_REQUIRED", "一级分类名称不能为空")
    if not row.material_name:
        raise _RowProblem(MATERIAL_COLUMN, "MATERIAL_NAME_REQUIRED", "物资名称不能为空")
    if inbound and not row.manufacturer_name:
        raise _RowProblem(
            MANUFACTURER_COLUMN, "MANUFACTURER_NAME_REQUIRED", "入库明细必须填写厂家名称"
        )


def _app_error_reason(exc: AppError) -> str:
    if exc.items:
        reason = exc.items[0].get("reason")
        if isinstance(reason, str):
            return reason
    return exc.message


def _row_limit_error(count: int) -> AppError:
    return file_error(
        "XLSX_ROW_LIMIT_EXCEEDED",
        f"非空数据行超过 {MAX_DATA_ROWS:,} 行，整份文件已拒绝",
        items=[{"field": "明细", "limit": MAX_DATA_ROWS, "actual": count}],
    )


def _no_valid_rows(total_rows: int) -> AppError:
    return file_error(
        "XLSX_NO_VALID_ROWS",
        "没有任何有效明细，整份文件未填入",
        items=[{"field": "明细", "reason": "全部数据行为空或全部行失败"}],
    )
