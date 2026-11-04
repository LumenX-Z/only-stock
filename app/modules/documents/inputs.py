"""单据输入规范化。

依据 docs/06-库存计算与单据业务流程.md 7.2–7.4 与
docs/08-MCP服务与工具.md 7.9：
- 数量按字符串解析为千分比整数，禁止浮点与科学计数法；
- 逐行 ``remark`` 省略、``null`` 与空字符串统一为 ``None``，最多 500 字符；
- 出库明细不得出现厂家字段；
- 规范化结果用于请求指纹计算和后续数据库校验。
"""

from dataclasses import dataclass

from app.errors import AppError
from app.modules.documents.models import REMARK_MAX_LENGTH
from app.modules.inventory.quantity import parse_quantity, quantity_to_milli


@dataclass(frozen=True, slots=True)
class DocumentLineInput:
    material_id: str
    quantity: str
    manufacturer_id: str | None = None
    project_id: str | None = None
    remark: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentInput:
    doc_type: str
    warehouse_id: str
    department_id: str
    lines: tuple[DocumentLineInput, ...]


@dataclass(frozen=True, slots=True)
class PreparedLine:
    material_id: str
    manufacturer_id: str | None
    project_id: str | None
    quantity_milli: int
    remark: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    doc_type: str
    warehouse_id: str
    department_id: str
    lines: tuple[PreparedLine, ...]


def normalize_remark(remark: str | None) -> str | None:
    """空字符串与仅空白统一为 ``None``；其余保留原文。"""
    if remark is None:
        return None
    normalized = remark.strip()
    return normalized or None


def prepare_document(data: DocumentInput) -> PreparedDocument:
    """在访问数据库前完成结构、数量和厂家字段位置校验。"""
    doc_type = data.doc_type.strip().upper()
    if doc_type not in {"INBOUND", "OUTBOUND"}:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            resource="document",
            items=[{"field": "doc_type", "reason": "单据类型必须是 INBOUND 或 OUTBOUND"}],
        )

    prepared_lines = prepare_lines(doc_type, data.lines)
    return PreparedDocument(
        doc_type=doc_type,
        warehouse_id=data.warehouse_id,
        department_id=data.department_id,
        lines=prepared_lines,
    )


def prepare_lines(
    doc_type: str,
    lines: tuple[DocumentLineInput, ...] | list[DocumentLineInput],
) -> tuple[PreparedLine, ...]:
    """解析数量、备注并拒绝出库明细中的厂家字段；结构校验先于数据库访问。"""
    prepared_lines: list[PreparedLine] = []
    manufacturer_items: list[dict[str, object]] = []
    remark_items: list[dict[str, object]] = []
    for index, line in enumerate(lines, start=1):
        quantity = parse_quantity(line.quantity, field=f"lines[{index}].quantity")
        milli = quantity_to_milli(quantity, field=f"lines[{index}].quantity")
        remark = normalize_remark(line.remark)
        if remark is not None and len(remark) > REMARK_MAX_LENGTH:
            remark_items.append(
                {
                    "line_no": index,
                    "field": f"lines[{index}].remark",
                    "reason": f"备注最多 {REMARK_MAX_LENGTH} 字符",
                }
            )
        if doc_type == "OUTBOUND" and line.manufacturer_id is not None:
            manufacturer_items.append(
                {
                    "line_no": index,
                    "field": f"lines[{index}].manufacturer_id",
                    "reason": "出库单明细不能提供厂家",
                }
            )
        prepared_lines.append(
            PreparedLine(
                material_id=line.material_id,
                manufacturer_id=line.manufacturer_id,
                project_id=line.project_id,
                quantity_milli=milli,
                remark=remark,
            )
        )

    if manufacturer_items:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            resource="document",
            items=manufacturer_items,
        )
    if remark_items:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            resource="document",
            items=remark_items,
        )
    if not prepared_lines:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            resource="document",
            items=[{"field": "lines", "reason": "单据至少需要一条明细"}],
        )
    return tuple(prepared_lines)
