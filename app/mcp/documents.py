"""MCP 单据创建适配。

依据 docs/08-MCP服务与工具.md 7.9 与 8.6.3：
- 工具参数只接受关联 ``*_code``，不接受 ``id``、``*_id``、``request_id`` 或 ``doc_no``；
- 编码在同一 ``BEGIN IMMEDIATE`` 写事务内解析为内部引用，不存在返回
  ``RESOURCE_NOT_FOUND``，不自动创建关联资料；
- 入库明细必须提供厂家编码，出库明细不得出现厂家编码；
- 同一 ``material_code`` 与 ``project_code`` 组合不得重复（项目为空视为同一取值）。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from app.errors import AppError
from app.mcp import references, validation
from app.modules.departments.models import Department
from app.modules.documents.inputs import (
    DocumentInput,
    DocumentLineInput,
    PreparedDocument,
    prepare_document,
)
from app.modules.documents.models import DocType
from app.modules.documents.service import DocumentCreation, create_mcp_document
from app.modules.manufacturers.models import Manufacturer
from app.modules.materials.models import Material
from app.modules.projects.models import Project
from app.modules.users.models import User
from app.modules.warehouses.models import Warehouse

MAX_DOCUMENT_LINES = 2000
_LINE_FIELDS = frozenset(
    {"material_code", "quantity", "project_code", "manufacturer_code", "remark"}
)
_REQUEST_FIELDS = frozenset({"doc_type", "submit", "warehouse_code", "department_code", "lines"})


@dataclass(frozen=True, slots=True)
class MCPDocumentLine:
    material_code: str
    quantity: str
    project_code: str | None = None
    manufacturer_code: str | None = None
    remark: str | None = None


@dataclass(frozen=True, slots=True)
class MCPDocumentRequest:
    doc_type: str
    submit: bool
    warehouse_code: str
    department_code: str
    lines: tuple[MCPDocumentLine, ...]


def parse_document_request(arguments: object) -> MCPDocumentRequest:
    """校验工具参数结构；不访问数据库、不解析配额。"""
    args = validation.require_object(arguments)
    validation.reject_unknown(args, _REQUEST_FIELDS)

    doc_type = validation.required_string(args, "doc_type")
    if doc_type not in {kind.value for kind in DocType}:
        raise validation.invalid("doc_type", "单据类型必须是 INBOUND 或 OUTBOUND")
    submit = validation.optional_bool(args, "submit")
    warehouse_code = validation.required_code(args, "warehouse_code")
    department_code = validation.required_code(args, "department_code")

    raw_lines = args.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise validation.invalid("lines", "至少需要一行明细")
    if len(raw_lines) > MAX_DOCUMENT_LINES:
        raise validation.invalid("lines", f"明细不得超过 {MAX_DOCUMENT_LINES} 行")

    lines: list[MCPDocumentLine] = []
    for line_no, raw in enumerate(raw_lines, start=1):
        lines.append(_parse_line(raw, line_no, doc_type))

    validation.reject_duplicate_line_keys(
        [(line.material_code, line.project_code) for line in lines]
    )
    return MCPDocumentRequest(
        doc_type=doc_type,
        submit=submit,
        warehouse_code=warehouse_code,
        department_code=department_code,
        lines=tuple(lines),
    )


def create_document(
    session_factory: sessionmaker[Session],
    request: MCPDocumentRequest,
    *,
    actor: User,
    request_id: str,
    api_key_id: str,
    timezone: ZoneInfo,
) -> DocumentCreation:
    """在写事务内解析业务编码并创建（可选提交）单据。"""

    def prepare(session: Session) -> PreparedDocument:
        warehouse = references.require_by_code(
            session,
            Warehouse,
            request.warehouse_code,
            resource="warehouse",
            field="warehouse_code",
        )
        department = references.require_by_code(
            session,
            Department,
            request.department_code,
            resource="department",
            field="department_code",
        )
        line_inputs: list[DocumentLineInput] = []
        for line_no, line in enumerate(request.lines, start=1):
            material = references.require_by_code(
                session,
                Material,
                line.material_code,
                resource="material",
                field=f"lines[{line_no}].material_code",
                line_no=line_no,
            )
            manufacturer = (
                None
                if line.manufacturer_code is None
                else references.require_by_code(
                    session,
                    Manufacturer,
                    line.manufacturer_code,
                    resource="manufacturer",
                    field=f"lines[{line_no}].manufacturer_code",
                    line_no=line_no,
                )
            )
            project = (
                None
                if line.project_code is None
                else references.require_by_code(
                    session,
                    Project,
                    line.project_code,
                    resource="project",
                    field=f"lines[{line_no}].project_code",
                    line_no=line_no,
                )
            )
            line_inputs.append(
                DocumentLineInput(
                    material_id=material.id,
                    quantity=line.quantity,
                    manufacturer_id=None if manufacturer is None else manufacturer.id,
                    project_id=None if project is None else project.id,
                    remark=line.remark,
                )
            )
        return prepare_document(
            DocumentInput(
                doc_type=request.doc_type,
                warehouse_id=warehouse.id,
                department_id=department.id,
                lines=tuple(line_inputs),
            )
        )

    return create_mcp_document(
        session_factory,
        prepare,
        submit=request.submit,
        actor=actor,
        request_id=request_id,
        api_key_id=api_key_id,
        timezone=timezone,
    )


def _parse_line(raw: object, line_no: int, doc_type: str) -> MCPDocumentLine:
    prefix = f"lines[{line_no}]"
    if not isinstance(raw, Mapping):
        raise validation.invalid(prefix, "明细必须是 JSON 对象")
    try:
        validation.reject_unknown(raw, _LINE_FIELDS)
        material_code = validation.required_code(raw, "material_code")
        quantity = raw.get("quantity")
        if not isinstance(quantity, str) or not quantity:
            raise validation.invalid("quantity", "数量必须是非空的十进制字符串")
        project_code = validation.optional_code(raw, "project_code")
        manufacturer_code = validation.optional_code(raw, "manufacturer_code")
        remark = validation.optional_string(raw, "remark")
    except AppError as exc:
        raise _prefix_error(exc, prefix) from exc

    if project_code == "":
        raise validation.invalid(f"{prefix}.project_code", "提供时不能为空字符串")
    if manufacturer_code == "":
        raise validation.invalid(f"{prefix}.manufacturer_code", "提供时不能为空字符串")
    if doc_type == DocType.INBOUND.value and manufacturer_code is None:
        raise validation.invalid(f"{prefix}.manufacturer_code", "入库明细必须填写厂家编码")
    if doc_type == DocType.OUTBOUND.value and manufacturer_code is not None:
        raise validation.invalid(f"{prefix}.manufacturer_code", "出库单明细不能提供厂家编码")
    return MCPDocumentLine(
        material_code=material_code,
        quantity=quantity,
        project_code=project_code,
        manufacturer_code=manufacturer_code,
        remark=remark,
    )


def _prefix_error(exc: AppError, prefix: str) -> AppError:
    items: list[dict[str, Any]] = []
    for item in exc.items:
        field = str(item.get("field", ""))
        items.append({**item, "field": f"{prefix}.{field}" if field else prefix})
    return AppError(
        exc.code,
        exc.message,
        status_code=exc.status_code,
        resource=exc.resource,
        items=items,
    )
