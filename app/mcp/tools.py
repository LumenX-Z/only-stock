"""MCP 工具声明、动态权限过滤与调用分发。

依据 docs/08-MCP服务与工具.md 8.6.2、8.6.3、8.6.5 与 docs/04-认证、会话与凭证安全.md 5.5：
- 12 个工具：六类资源各 ``list/create``；``tools/list`` 每次按当前账号过滤，
  ``tools/call`` 不信任先前清单并按数据库当前角色、账户与 Key 状态重新校验；
- OP 恰为六个 ``list`` 与 ``document_create``；CA 为全部 12 个；
- 查询工具不写业务审计；新增工具复用普通服务层并标记 ``source=MCP`` 与 Key 标识。
"""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.models import ApiKey
from app.config import Settings
from app.errors import AppError
from app.mcp import documents, errors, presenter, references, schemas, validation
from app.mcp.identity import MCPIdentity, current_identity
from app.modules.categories.models import Category
from app.modules.departments.models import Department
from app.modules.documents.models import DocStatus, DocType, StockDocument
from app.modules.documents.service import (
    DocumentFilters,
    get_document,
    list_documents,
)
from app.modules.inventory.service import local_day_bounds
from app.modules.manufacturers.models import Manufacturer
from app.modules.masterdata.base import NAME_MAX_LENGTH, UNIT_MAX_LENGTH
from app.modules.masterdata.service import (
    EntityFilters,
    category_map_for_materials,
    create_entity,
    list_entities,
)
from app.modules.materials.models import Material, QuantityType
from app.modules.projects.models import Project
from app.modules.users.models import Role, User, UserStatus
from app.modules.warehouses.models import Warehouse
from mcp import types

logger = logging.getLogger(__name__)

ALL_ROLES = frozenset({Role.COMPANY_ADMIN.value, Role.OPERATOR.value})
ADMIN_ONLY = frozenset({Role.COMPANY_ADMIN.value})
MCP_SOURCE = "MCP"


@dataclass(frozen=True, slots=True)
class Invocation:
    """一次已鉴权工具调用的上下文。"""

    session_factory: sessionmaker[Session]
    settings: Settings
    user: User
    request_id: str
    api_key_id: str


def _missing(resource: str, field: str, value: str) -> AppError:
    return AppError(
        "RESOURCE_NOT_FOUND",
        f"{resource}编码不存在",
        status_code=404,
        resource=resource,
        items=[{"field": field, "value": value}],
    )


def _list_payload(
    items: Sequence[dict[str, Any]], page: int, page_size: int, total: int
) -> dict[str, Any]:
    return {"items": list(items), "page": page, "page_size": page_size, "total": total}


def _optional_flag(arguments: dict[str, Any], name: str) -> bool | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise validation.invalid(name, "必须是布尔值")
    return value


def _enum_argument(arguments: dict[str, Any], name: str, allowed: Sequence[str]) -> str | None:
    value = validation.optional_string(arguments, name)
    if value is None:
        return None
    if value not in allowed:
        raise validation.invalid(name, f"必须是 {' 或 '.join(allowed)}")
    return value


def _date_argument(arguments: dict[str, Any], name: str) -> date | None:
    value = validation.optional_string(arguments, name, max_length=10)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise validation.invalid(name, "必须使用 YYYY-MM-DD 格式") from exc


def _parent_by_code(session: Session, rows: Sequence[Category]) -> dict[str, Category]:
    parent_ids = list(dict.fromkeys(row.parent_id for row in rows if row.parent_id))
    if not parent_ids:
        return {}
    parents = session.scalars(select(Category).where(Category.id.in_(parent_ids))).all()
    return {row.id: row for row in parents}


def _category_parent(session: Session, row: Category) -> Category | None:
    return session.get(Category, row.parent_id) if row.parent_id else None


def _parent_category(session: Session, category: Category | None) -> Category | None:
    return (
        session.get(Category, category.parent_id)
        if category is not None and category.parent_id
        else None
    )


# --------------------------------------------------------------------------- 查询工具


def _warehouse_list(inv: Invocation, arguments: object) -> dict[str, Any]:
    args = validation.require_object(arguments)
    validation.reject_unknown(args, {"code", "name", "include_inactive", "page", "page_size"})
    code = validation.optional_code(args, "code")
    with inv.session_factory() as session:
        if code is not None:
            row = references.find_by_code(session, Warehouse, code)
            if row is None:
                raise _missing("warehouse", "code", code)
            return presenter.warehouse_payload(row)
        page, page_size = validation.page_parameters(args)
        filters = EntityFilters(
            name=validation.optional_string(args, "name", max_length=NAME_MAX_LENGTH, strip=True),
            include_inactive=validation.optional_bool(args, "include_inactive"),
        )
        rows, total = list_entities(
            session, "warehouse", filters=filters, page=page, page_size=page_size
        )
        return _list_payload(
            [presenter.warehouse_payload(row) for row in rows], page, page_size, total
        )


def _department_list(inv: Invocation, arguments: object) -> dict[str, Any]:
    return _named_list(inv, arguments, key="department", resource="department", model=Department)


def _manufacturer_list(inv: Invocation, arguments: object) -> dict[str, Any]:
    return _named_list(
        inv, arguments, key="manufacturer", resource="manufacturer", model=Manufacturer
    )


def _named_list(
    inv: Invocation, arguments: object, *, key: str, resource: str, model: type
) -> dict[str, Any]:
    args = validation.require_object(arguments)
    validation.reject_unknown(args, {"code", "name", "include_inactive", "page", "page_size"})
    code = validation.optional_code(args, "code")
    with inv.session_factory() as session:
        if code is not None:
            row = references.find_by_code(session, model, code)
            if row is None:
                raise _missing(resource, "code", code)
            return presenter.named_payload(row)
        page, page_size = validation.page_parameters(args)
        filters = EntityFilters(
            name=validation.optional_string(args, "name", max_length=NAME_MAX_LENGTH, strip=True),
            include_inactive=validation.optional_bool(args, "include_inactive"),
        )
        rows, total = list_entities(session, key, filters=filters, page=page, page_size=page_size)
        return _list_payload([presenter.named_payload(row) for row in rows], page, page_size, total)


def _category_list(inv: Invocation, arguments: object) -> dict[str, Any]:
    args = validation.require_object(arguments)
    validation.reject_unknown(
        args, {"code", "name", "parent_code", "include_inactive", "page", "page_size"}
    )
    code = validation.optional_code(args, "code")
    with inv.session_factory() as session:
        if code is not None:
            row = references.find_by_code(session, Category, code)
            if row is None:
                raise _missing("category", "code", code)
            parent = _category_parent(session, row)
            return presenter.category_payload(row, parent.code if parent else None)
        page, page_size = validation.page_parameters(args)
        filters = EntityFilters(
            name=validation.optional_string(args, "name", max_length=NAME_MAX_LENGTH, strip=True),
            parent_code=validation.optional_code(args, "parent_code"),
            include_inactive=validation.optional_bool(args, "include_inactive"),
        )
        rows, total = list_entities(
            session, "category", filters=filters, page=page, page_size=page_size
        )
        categories = [row for row in rows if isinstance(row, Category)]
        parents = _parent_by_code(session, categories)
        items = [
            presenter.category_payload(
                row,
                parents[row.parent_id].code if row.parent_id in parents else None,
            )
            for row in categories
        ]
        return _list_payload(items, page, page_size, total)


def _material_list(inv: Invocation, arguments: object) -> dict[str, Any]:
    args = validation.require_object(arguments)
    validation.reject_unknown(
        args, {"code", "name", "category_code", "include_inactive", "page", "page_size"}
    )
    code = validation.optional_code(args, "code")
    with inv.session_factory() as session:
        if code is not None:
            material = references.find_by_code(session, Material, code)
            if material is None:
                raise _missing("material", "code", code)
            category = session.get(Category, material.category_id)
            return presenter.material_payload(
                material, category, _parent_category(session, category)
            )
        page, page_size = validation.page_parameters(args)
        filters = EntityFilters(
            name=validation.optional_string(args, "name", max_length=NAME_MAX_LENGTH, strip=True),
            category_code=validation.optional_code(args, "category_code"),
            include_inactive=validation.optional_bool(args, "include_inactive"),
        )
        rows, total = list_entities(
            session, "material", filters=filters, page=page, page_size=page_size
        )
        materials = [row for row in rows if isinstance(row, Material)]
        categories = category_map_for_materials(session, materials)
        parents = _parent_by_code(session, list(categories.values()))
        items = []
        for material in materials:
            category = categories.get(material.category_id)
            parent = (
                parents.get(category.parent_id)
                if category is not None and category.parent_id is not None
                else None
            )
            items.append(presenter.material_payload(material, category, parent))
        return _list_payload(items, page, page_size, total)


def _document_list(inv: Invocation, arguments: object) -> dict[str, Any]:
    args = validation.require_object(arguments)
    validation.reject_unknown(
        args,
        {
            "doc_no",
            "doc_type",
            "status",
            "warehouse_code",
            "department_code",
            "project_code",
            "manufacturer_code",
            "start_date",
            "end_date",
            "deleted",
            "page",
            "page_size",
        },
    )
    doc_no = validation.optional_code(args, "doc_no")
    doc_type = _enum_argument(args, "doc_type", [kind.value for kind in DocType])
    status = _enum_argument(args, "status", [state.value for state in DocStatus])
    warehouse_code = validation.optional_code(args, "warehouse_code")
    department_code = validation.optional_code(args, "department_code")
    project_code = validation.optional_code(args, "project_code")
    manufacturer_code = validation.optional_code(args, "manufacturer_code")
    deleted = _optional_flag(args, "deleted")
    start_date = _date_argument(args, "start_date")
    end_date = _date_argument(args, "end_date")
    page, page_size = validation.page_parameters(args)

    with inv.session_factory() as session:
        if doc_no is not None:
            row = session.scalar(select(StockDocument).where(StockDocument.doc_no == doc_no))
            if row is None:
                raise _missing("document", "doc_no", doc_no)
            record = get_document(session, row.id)
            return presenter.document_detail(record.document, record.lines)

        conditions: dict[str, Any] = {
            "doc_type": doc_type,
            "status": status,
            "deleted": deleted,
        }
        for field, model, code in (
            ("warehouse", Warehouse, warehouse_code),
            ("department", Department, department_code),
            ("project", None, project_code),
            ("manufacturer", Manufacturer, manufacturer_code),
        ):
            if code is None:
                continue
            if model is not None:
                resolved = references.id_for_code(session, model, code)
                if resolved is None:
                    return _list_payload([], page, page_size, 0)
                conditions[f"{field}_id"] = resolved
            else:
                project = references.find_by_code(session, Project, code)
                if project is None:
                    return _list_payload([], page, page_size, 0)
                conditions["project_id"] = project.id

        start_at, end_before = local_day_bounds(start_date, end_date, inv.settings.timezone)
        rows, projects, total = list_documents(
            session,
            filters=DocumentFilters(start_at=start_at, end_before=end_before, **conditions),
            page=page,
            page_size=page_size,
        )
        items = [presenter.document_summary(row, projects.get(row.id, [])) for row in rows]
        return _list_payload(items, page, page_size, total)


# --------------------------------------------------------------------------- 新增工具


def _warehouse_create(inv: Invocation, arguments: object) -> dict[str, Any]:
    args = validation.require_object(arguments)
    validation.reject_unknown(args, {"name", "location"})
    name = validation.required_string(args, "name", max_length=NAME_MAX_LENGTH)
    location = validation.optional_string(args, "location", max_length=200)
    row = create_entity(
        inv.session_factory,
        "warehouse",
        name=name,
        location=location,
        actor_user_id=inv.user.id,
        request_id=inv.request_id,
        source=MCP_SOURCE,
        api_key_id=inv.api_key_id,
    )
    return presenter.warehouse_payload(row)


def _department_create(inv: Invocation, arguments: object) -> dict[str, Any]:
    return _named_create(inv, arguments, key="department")


def _manufacturer_create(inv: Invocation, arguments: object) -> dict[str, Any]:
    return _named_create(inv, arguments, key="manufacturer")


def _named_create(inv: Invocation, arguments: object, *, key: str) -> dict[str, Any]:
    args = validation.require_object(arguments)
    validation.reject_unknown(args, {"name"})
    name = validation.required_string(args, "name", max_length=NAME_MAX_LENGTH)
    row = create_entity(
        inv.session_factory,
        key,
        name=name,
        actor_user_id=inv.user.id,
        request_id=inv.request_id,
        source=MCP_SOURCE,
        api_key_id=inv.api_key_id,
    )
    return presenter.named_payload(row)


def _category_create(inv: Invocation, arguments: object) -> dict[str, Any]:
    args = validation.require_object(arguments)
    validation.reject_unknown(args, {"name", "parent_code"})
    name = validation.required_string(args, "name", max_length=NAME_MAX_LENGTH)
    parent_code = validation.optional_code(args, "parent_code")
    parent_id: str | None = None
    if parent_code is not None:
        with inv.session_factory() as session:
            parent = references.find_by_code(session, Category, parent_code)
            if parent is None:
                raise _missing("category", "parent_code", parent_code)
            parent_id = parent.id
    row = create_entity(
        inv.session_factory,
        "category",
        name=name,
        parent_id=parent_id,
        actor_user_id=inv.user.id,
        request_id=inv.request_id,
        source=MCP_SOURCE,
        api_key_id=inv.api_key_id,
    )
    assert isinstance(row, Category)
    return presenter.category_payload(row, parent_code)


def _material_create(inv: Invocation, arguments: object) -> dict[str, Any]:
    args = validation.require_object(arguments)
    validation.reject_unknown(args, {"category_code", "name", "unit", "quantity_type"})
    category_code = validation.required_code(args, "category_code")
    name = validation.required_string(args, "name", max_length=NAME_MAX_LENGTH)
    unit = validation.required_string(args, "unit", max_length=UNIT_MAX_LENGTH)
    quantity_type = _enum_argument(args, "quantity_type", [kind.value for kind in QuantityType])
    if quantity_type is None:
        raise validation.invalid("quantity_type", "数量类型必须是 INTEGER 或 DECIMAL")
    with inv.session_factory() as session:
        category = references.find_by_code(session, Category, category_code)
        if category is None:
            raise _missing("category", "category_code", category_code)
        category_id = category.id
    row = create_entity(
        inv.session_factory,
        "material",
        name=name,
        category_id=category_id,
        unit=unit,
        quantity_type=quantity_type,
        actor_user_id=inv.user.id,
        request_id=inv.request_id,
        source=MCP_SOURCE,
        api_key_id=inv.api_key_id,
    )
    assert isinstance(row, Material)
    with inv.session_factory() as session:
        material = session.get(Material, row.id)
        if material is None:  # pragma: no cover - 刚创建且同库读取
            raise AppError("INTERNAL_ERROR", "读取新建物资失败", status_code=500)
        category = session.get(Category, material.category_id)
        parent = _parent_category(session, category)
        return presenter.material_payload(material, category, parent)


def _document_create(inv: Invocation, arguments: object) -> dict[str, Any]:
    request = documents.parse_document_request(arguments)
    creation = documents.create_document(
        inv.session_factory,
        request,
        actor=inv.user,
        request_id=inv.request_id,
        api_key_id=inv.api_key_id,
        timezone=inv.settings.timezone,
    )
    payload = presenter.document_detail(creation.document, creation.lines)
    payload["deduplicated"] = creation.deduplicated
    return payload


# --------------------------------------------------------------------------- 工具清单

Handler = Callable[[Invocation, object], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    roles: frozenset[str]
    read_only: bool
    destructive: bool
    handler: Handler


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="warehouse_list",
        description=(
            "查询仓库。传 code 精确返回单条完整记录（含停用）；否则按 name 筛选并分页，"
            "默认只列启用仓库，include_inactive=true 时包含停用记录。输出只含业务编码与名称，"
            "不含内部 ID。"
        ),
        input_schema=schemas.WAREHOUSE_LIST_INPUT,
        output_schema=schemas.list_or_single_output(schemas.WAREHOUSE_OUTPUT),
        roles=ALL_ROLES,
        read_only=True,
        destructive=False,
        handler=_warehouse_list,
    ),
    ToolDefinition(
        name="warehouse_create",
        description=(
            "新增仓库（仅公司管理员）。code 由系统自动生成并在结果中返回；不接受 code、id、"
            "内部 UUID 或 request_id。名称与其他普通 API 使用相同的规范化与唯一规则。"
        ),
        input_schema=schemas.WAREHOUSE_CREATE_INPUT,
        output_schema=schemas.WAREHOUSE_OUTPUT,
        roles=ADMIN_ONLY,
        read_only=False,
        destructive=False,
        handler=_warehouse_create,
    ),
    ToolDefinition(
        name="department_list",
        description=(
            "查询部门。传 code 精确返回单条完整记录（含停用）；否则按 name 筛选并分页，"
            "默认只列启用部门。输出只含业务编码与名称，不含内部 ID。"
        ),
        input_schema=schemas.NAMED_LIST_INPUT,
        output_schema=schemas.list_or_single_output(schemas.NAMED_OUTPUT),
        roles=ALL_ROLES,
        read_only=True,
        destructive=False,
        handler=_department_list,
    ),
    ToolDefinition(
        name="department_create",
        description=(
            "新增部门（仅公司管理员）。code 由系统自动生成并在结果中返回；不接受 code、id、"
            "内部 UUID 或 request_id。"
        ),
        input_schema=schemas.DEPARTMENT_CREATE_INPUT,
        output_schema=schemas.NAMED_OUTPUT,
        roles=ADMIN_ONLY,
        read_only=False,
        destructive=False,
        handler=_department_create,
    ),
    ToolDefinition(
        name="manufacturer_list",
        description=(
            "查询厂家。传 code 精确返回单条完整记录（含停用）；否则按 name 筛选并分页，"
            "默认只列启用厂家。输出只含业务编码与名称，不含内部 ID。"
        ),
        input_schema=schemas.NAMED_LIST_INPUT,
        output_schema=schemas.list_or_single_output(schemas.NAMED_OUTPUT),
        roles=ALL_ROLES,
        read_only=True,
        destructive=False,
        handler=_manufacturer_list,
    ),
    ToolDefinition(
        name="manufacturer_create",
        description=(
            "新增厂家（仅公司管理员）。code 由系统自动生成并在结果中返回；不接受 code、id、"
            "内部 UUID 或 request_id。"
        ),
        input_schema=schemas.DEPARTMENT_CREATE_INPUT,
        output_schema=schemas.NAMED_OUTPUT,
        roles=ADMIN_ONLY,
        read_only=False,
        destructive=False,
        handler=_manufacturer_create,
    ),
    ToolDefinition(
        name="category_list",
        description=(
            "查询分类及父子关系。传 code 精确返回单条完整记录（含停用）；否则按 name 或 "
            "parent_code 筛选并分页。分类最多两级，输出自身 code 与父分类 parent_code，"
            "不含内部 ID。"
        ),
        input_schema=schemas.CATEGORY_LIST_INPUT,
        output_schema=schemas.list_or_single_output(schemas.CATEGORY_OUTPUT),
        roles=ALL_ROLES,
        read_only=True,
        destructive=False,
        handler=_category_list,
    ),
    ToolDefinition(
        name="category_create",
        description=(
            "新增一级或二级分类（仅公司管理员）。不带 parent_code 创建一级分类，带 parent_code "
            "创建该一级分类下的二级分类；code 由系统自动生成并在结果中返回。不接受 code、id、"
            "内部 UUID 或 request_id。"
        ),
        input_schema=schemas.CATEGORY_CREATE_INPUT,
        output_schema=schemas.CATEGORY_OUTPUT,
        roles=ADMIN_ONLY,
        read_only=False,
        destructive=False,
        handler=_category_create,
    ),
    ToolDefinition(
        name="material_list",
        description=(
            "查询物资。传 code 精确返回单条完整记录（含停用）；否则按 name 或 category_code "
            "筛选并分页，默认只列启用物资。输出分类与父分类编码名称，不含内部 ID。"
        ),
        input_schema=schemas.MATERIAL_LIST_INPUT,
        output_schema=schemas.list_or_single_output(schemas.MATERIAL_OUTPUT),
        roles=ALL_ROLES,
        read_only=True,
        destructive=False,
        handler=_material_list,
    ),
    ToolDefinition(
        name="material_create",
        description=(
            "新增物资（仅公司管理员）。category_code 必须是已启用的一级或二级分类编码；"
            "code 由系统自动生成并在结果中返回。不接受 code、id、内部 UUID 或 request_id。"
        ),
        input_schema=schemas.MATERIAL_CREATE_INPUT,
        output_schema=schemas.MATERIAL_OUTPUT,
        roles=ADMIN_ONLY,
        read_only=False,
        destructive=False,
        handler=_material_create,
    ),
    ToolDefinition(
        name="document_list",
        description=(
            "查询入库/出库单。传 doc_no 精确返回单条单据，包含全部明细、历史快照与软删除状态；"
            "否则按单据类型、状态、仓库、部门、项目、厂家、创建日期和软删除状态筛选并分页。"
            "项目与厂家按明细行筛选；输出只含业务编码与单号，不含内部 ID。"
        ),
        input_schema=schemas.DOCUMENT_LIST_INPUT,
        output_schema=schemas.list_or_single_output(
            schemas.DOCUMENT_SUMMARY_OUTPUT, schemas.DOCUMENT_DETAIL_OUTPUT
        ),
        roles=ALL_ROLES,
        read_only=True,
        destructive=False,
        handler=_document_list,
    ),
    ToolDefinition(
        name="document_create",
        description=(
            "新增入库或出库单。submit=false 只创建草稿（DRAFT），不改变库存；submit=true 在同一"
            "写事务内创建并直接提交（POSTED），失败不留下草稿。资料一律用关联编码定位："
            "warehouse_code、department_code、lines[].material_code、lines[].project_code、"
            "lines[].manufacturer_code；入库每行必须提供厂家编码，出库不得提供。"
            "去重语义：服务端按创建者与规范化内容在 300 秒窗口内去重，命中时不会新建，而是返回"
            "原单并把 deduplicated 置为 true；窗口外可再次创建；submit=false 与 submit=true "
            "内容相同也互不去重。单号由服务端生成并在结果中返回，不能由客户端指定。"
        ),
        input_schema=schemas.DOCUMENT_CREATE_INPUT,
        output_schema=schemas.DOCUMENT_CREATE_OUTPUT,
        roles=ALL_ROLES,
        read_only=False,
        destructive=False,
        handler=_document_create,
    ),
)

TOOL_BY_NAME: dict[str, ToolDefinition] = {tool.name: tool for tool in TOOL_DEFINITIONS}


def tool_payload(definition: ToolDefinition) -> types.Tool:
    return types.Tool(
        name=definition.name,
        description=definition.description,
        input_schema=definition.input_schema,
        output_schema=definition.output_schema,
        annotations=types.ToolAnnotations(
            read_only_hint=definition.read_only,
            destructive_hint=definition.destructive,
            open_world_hint=False,
        ),
    )


def visible_definitions(role: str) -> list[ToolDefinition]:
    """按数据库中的当前角色过滤工具；不固化在 Key 中。"""
    return [tool for tool in TOOL_DEFINITIONS if role in tool.roles]


def load_active_user(session_factory: sessionmaker[Session], identity: MCPIdentity) -> User | None:
    """按 Key 与账户当前状态重新校验身份，返回会话外用户对象。"""
    with session_factory() as session:
        key = session.get(ApiKey, identity.api_key_id)
        if key is None or key.revoked_at is not None or key.user_id != identity.user_id:
            return None
        user = session.get(User, identity.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            return None
        session.expunge(user)
        return user


async def list_tools_for_current_identity(
    session_factory: sessionmaker[Session],
) -> list[types.Tool]:
    identity = current_identity()
    if identity is None:
        return []
    user = await asyncio.to_thread(load_active_user, session_factory, identity)
    if user is None:
        return []
    return [tool_payload(definition) for definition in visible_definitions(user.role)]


async def invoke_tool(
    session_factory: sessionmaker[Session],
    settings: Settings,
    name: str,
    arguments: object,
) -> types.CallToolResult:
    """执行一次 ``tools/call``；所有失败返回结构化工具错误。"""
    request_id = str(uuid4())
    started = time.perf_counter()
    result_code = "OK"
    tool = TOOL_BY_NAME.get(name)
    try:
        identity = current_identity()
        if identity is None:
            result_code = "UNAUTHORIZED"
            return errors.tool_error(
                "UNAUTHORIZED", "未认证或认证已失效", request_id, resource="api_key"
            )
        if tool is None:
            result_code = "TOOL_NOT_FOUND"
            return errors.tool_error("TOOL_NOT_FOUND", "工具不存在", request_id, resource="tool")
        user = await asyncio.to_thread(load_active_user, session_factory, identity)
        if user is None:
            result_code = "UNAUTHORIZED"
            return errors.tool_error(
                "UNAUTHORIZED", "未认证或认证已失效", request_id, resource="api_key"
            )
        if user.role not in tool.roles:
            result_code = "FORBIDDEN"
            return errors.tool_error("FORBIDDEN", "无权调用该工具", request_id, resource="tool")

        invocation = Invocation(
            session_factory=session_factory,
            settings=settings,
            user=user,
            request_id=request_id,
            api_key_id=identity.api_key_id,
        )
        payload = await asyncio.to_thread(tool.handler, invocation, arguments)
        return errors.success(payload)
    except AppError as exc:
        result_code = exc.code
        return errors.from_app_error(exc, request_id)
    except Exception:
        result_code = "INTERNAL_ERROR"
        logger.exception(
            "mcp tool failed tool=%s request_id=%s",
            name,
            request_id,
        )
        return errors.internal_error(request_id)
    finally:
        logger.info(
            "mcp tool completed tool=%s request_id=%s api_key_id=%s result=%s duration_ms=%.1f",
            name,
            request_id,
            identity_api_key_id(),
            result_code,
            (time.perf_counter() - started) * 1000,
        )


def identity_api_key_id() -> str | None:
    identity = current_identity()
    return None if identity is None else identity.api_key_id
