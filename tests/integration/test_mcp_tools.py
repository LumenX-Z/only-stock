"""MCP 工具清单、查询、新增与参数边界测试。

依据 docs/08-MCP服务与工具.md 8.6.2–8.6.5、docs/15-测试方案.md 12.1 与
docs/16-验收标准.md ACC-MCP-004–ACC-MCP-008、ACC-MCP-013。
"""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.audit.models import AuditLog
from app.modules.masterdata.service import set_entity_active
from app.modules.warehouses.models import Warehouse
from tests.conftest import MasterDataFixture, MCPAccounts
from tests.mcp_support import (
    assert_no_internal_ids,
    call_tool,
    mcp_sessions,
    structured,
    tool_by_name,
    tool_error,
    tool_names,
)

CA_TOOLS = [
    "warehouse_list",
    "warehouse_create",
    "department_list",
    "department_create",
    "manufacturer_list",
    "manufacturer_create",
    "category_list",
    "category_create",
    "material_list",
    "material_create",
    "document_list",
    "document_create",
]
OP_TOOLS = [
    "warehouse_list",
    "department_list",
    "manufacturer_list",
    "category_list",
    "material_list",
    "document_list",
    "document_create",
]

_MINIMAL_ARGUMENTS: dict[str, dict[str, object]] = {
    "warehouse_list": {},
    "warehouse_create": {"name": "参数拒绝仓"},
    "department_list": {},
    "department_create": {"name": "参数拒绝部"},
    "manufacturer_list": {},
    "manufacturer_create": {"name": "参数拒绝厂"},
    "category_list": {},
    "category_create": {"name": "参数拒绝类"},
    "material_list": {},
    "material_create": {
        "category_code": "FL-00001",
        "name": "参数拒绝料",
        "unit": "个",
        "quantity_type": "DECIMAL",
    },
    "document_list": {},
    "document_create": {
        "doc_type": "INBOUND",
        "warehouse_code": "CK-00001",
        "department_code": "BM-00001",
        "lines": [
            {
                "material_code": "WZ-00001",
                "quantity": "1",
                "manufacturer_code": "CJ-00001",
            }
        ],
    },
}


def test_tool_catalog_is_dynamic_and_annotated() -> None:
    from app.mcp import tools

    ca_tools = tools.visible_definitions("COMPANY_ADMIN")
    op_tools = tools.visible_definitions("OPERATOR")

    assert [tool.name for tool in ca_tools] == CA_TOOLS
    assert [tool.name for tool in op_tools] == OP_TOOLS
    for definition in ca_tools:
        assert definition.input_schema["type"] == "object"
        assert definition.input_schema["additionalProperties"] is False
        assert definition.output_schema["type"] == "object"
        assert (
            set(definition.input_schema["properties"])
            & {
                "id",
                "request_id",
            }
            == set()
        )
        for prop in definition.input_schema["properties"]:
            assert not prop.endswith("_id")
        assert definition.read_only is (definition.name.endswith("_list"))
        assert definition.destructive is False

    document_create = tools.TOOL_BY_NAME["document_create"]
    assert "300 秒" in document_create.description
    assert "deduplicated" in document_create.description
    assert "request_id" not in document_create.description


async def test_ca_and_op_see_different_tool_lists(app: FastAPI, mcp_accounts: MCPAccounts) -> None:
    async with mcp_sessions(
        app, {"ca": mcp_accounts.ca_key, "op": mcp_accounts.op_key}
    ) as sessions:
        ca_tools = await sessions["ca"].list_tools()
        op_tools = await sessions["op"].list_tools()
        assert tool_names(ca_tools.tools) == CA_TOOLS
        assert tool_names(op_tools.tools) == OP_TOOLS
        listed = tool_by_name(op_tools.tools, "warehouse_list")
        assert listed.annotations is not None
        assert listed.annotations.read_only_hint is True
        create = tool_by_name(ca_tools.tools, "warehouse_create")
        assert create.annotations is not None
        assert create.annotations.read_only_hint is False


async def test_operator_cannot_call_create_tools(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    session_factory: sessionmaker[Session],
) -> None:
    async with mcp_sessions(app, {"op": mcp_accounts.op_key}) as sessions:
        result = await call_tool(sessions["op"], "warehouse_create", {"name": "越权仓库"})
        error = tool_error(result)
        assert error["code"] == "FORBIDDEN"
        assert error["details"]["resource"] == "tool"

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Warehouse)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "WAREHOUSE_CREATED")
            )
            == 0
        )


async def test_unknown_tool_returns_tool_not_found(app: FastAPI, mcp_accounts: MCPAccounts) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        result = await call_tool(sessions["ca"], "warehouse_delete", {})
    error = tool_error(result)
    assert error["code"] == "TOOL_NOT_FOUND"
    assert "warehouse_delete" not in str(error)


@pytest.mark.parametrize("bad_key", ["id", "request_id", "warehouse_id"])
async def test_internal_arguments_are_rejected(
    bad_key: str,
    app: FastAPI,
    mcp_accounts: MCPAccounts,
) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        for name, minimal in _MINIMAL_ARGUMENTS.items():
            result = await call_tool(sessions["ca"], name, {**minimal, bad_key: str(uuid4())})
            error = tool_error(result)
            assert error["code"] == "VALIDATION_ERROR", (name, bad_key)


async def test_uuid_as_business_code_is_rejected(app: FastAPI, mcp_accounts: MCPAccounts) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        for name in ("warehouse_list", "department_list", "manufacturer_list", "category_list"):
            result = await call_tool(sessions["ca"], name, {"code": str(uuid4())})
            error = tool_error(result)
            assert error["code"] == "VALIDATION_ERROR", name

        result = await call_tool(sessions["ca"], "document_list", {"doc_no": str(uuid4())})
        error = tool_error(result)
        assert error["code"] == "VALIDATION_ERROR"


async def test_base_data_create_returns_generated_code_and_hides_ids(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    session_factory: sessionmaker[Session],
) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        created = await call_tool(sessions["ca"], "warehouse_create", {"name": "MCP 新仓"})
        payload = structured(created)
        assert payload["code"].startswith("CK-")
        assert payload["name"] == "MCP 新仓"
        assert payload["is_active"] is True
        assert_no_internal_ids(payload)

        duplicate = await call_tool(sessions["ca"], "warehouse_create", {"name": "MCP 新仓"})
        error = tool_error(duplicate)
        assert error["code"] == "NAME_CONFLICT"
        assert_no_internal_ids(error)
        assert error["details"]["items"][0]["name"] == "MCP 新仓"

        with_code = await call_tool(
            sessions["ca"], "warehouse_create", {"name": "带编码仓", "code": "CK-99999"}
        )
        assert tool_error(with_code)["code"] == "VALIDATION_ERROR"

        missing_parent = await call_tool(
            sessions["ca"], "category_create", {"name": "孤立二级", "parent_code": "FL-99999"}
        )
        error = tool_error(missing_parent)
        assert error["code"] == "RESOURCE_NOT_FOUND"
        assert_no_internal_ids(error)

        parent_payload = structured(
            await call_tool(sessions["ca"], "category_create", {"name": "MCP 一级分类"})
        )
        second_level = await call_tool(
            sessions["ca"],
            "category_create",
            {"name": "MCP 二级分类", "parent_code": parent_payload["code"]},
        )
        second_payload = structured(second_level)
        assert second_payload["level"] == 2
        assert second_payload["parent_code"].startswith("FL-")

        material = await call_tool(
            sessions["ca"],
            "material_create",
            {
                "category_code": second_payload["code"],
                "name": "MCP 物资",
                "unit": "千克",
                "quantity_type": "DECIMAL",
            },
        )
        material_payload = structured(material)
        assert material_payload["code"].startswith("WZ-")
        assert material_payload["category_code"] == second_payload["code"]
        assert material_payload["parent_category_code"] == second_payload["parent_code"]
        assert_no_internal_ids(material_payload)

    with session_factory() as session:
        mcp_actions = set(
            session.scalars(select(AuditLog.action).where(AuditLog.source == "MCP")).all()
        )
    assert {"WAREHOUSE_CREATED", "CATEGORY_CREATED", "MATERIAL_CREATED"} <= mcp_actions


async def test_lists_support_pagination_filters_and_inactive(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    set_entity_active(
        session_factory,
        "warehouse",
        master_data.warehouse_second.id,
        active=False,
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        active = structured(await call_tool(sessions["ca"], "warehouse_list", {}))
        assert active["total"] == 1
        assert [item["code"] for item in active["items"]] == [master_data.warehouse.code]

        everything = structured(
            await call_tool(sessions["ca"], "warehouse_list", {"include_inactive": True})
        )
        assert everything["total"] == 2

        single = structured(
            await call_tool(
                sessions["ca"], "warehouse_list", {"code": master_data.warehouse_second.code}
            )
        )
        assert single["code"] == master_data.warehouse_second.code
        assert single["is_active"] is False

        page = structured(
            await call_tool(
                sessions["ca"],
                "warehouse_list",
                {"include_inactive": True, "page": 2, "page_size": 1},
            )
        )
        assert page["page"] == 2 and page["page_size"] == 1 and len(page["items"]) == 1

        materials = structured(await call_tool(sessions["ca"], "material_list", {}))
        by_code = {item["code"]: item for item in materials["items"]}
        nested = by_code[master_data.material.code]
        assert nested["category_code"] == master_data.category_child.code
        assert nested["parent_category_code"] == master_data.category.code

        filtered = structured(
            await call_tool(
                sessions["ca"],
                "material_list",
                {"category_code": master_data.category_child.code},
            )
        )
        assert {item["code"] for item in filtered["items"]} == {
            master_data.material.code,
            master_data.material_integer.code,
        }

        children = structured(
            await call_tool(
                sessions["ca"], "category_list", {"parent_code": master_data.category.code}
            )
        )
        assert {item["code"] for item in children["items"]} == {master_data.category_child.code}

        missing = structured(
            await call_tool(sessions["ca"], "material_list", {"name": "不存在的物资"})
        )
        assert missing == {"items": [], "page": 1, "page_size": 20, "total": 0}

        unknown_code = await call_tool(sessions["ca"], "warehouse_list", {"code": "CK-99999"})
        assert tool_error(unknown_code)["code"] == "RESOURCE_NOT_FOUND"


async def test_document_list_and_detail(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        created = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                {
                    "doc_type": "INBOUND",
                    "warehouse_code": master_data.warehouse.code,
                    "department_code": master_data.department.code,
                    "lines": [
                        {
                            "material_code": master_data.material.code,
                            "quantity": "2.500",
                            "manufacturer_code": master_data.manufacturer.code,
                            "project_code": master_data.project.code,
                            "remark": "  第一批  ",
                        },
                        {
                            "material_code": master_data.material_second.code,
                            "quantity": "1",
                            "manufacturer_code": master_data.manufacturer.code,
                        },
                    ],
                },
            )
        )
        assert created["status"] == "DRAFT"
        assert created["deduplicated"] is False
        assert created["projects"] == [
            {
                "project_code": master_data.project.code,
                "project_name": master_data.project.name,
            }
        ]
        assert created["lines"][0]["remark"] == "第一批"
        assert created["lines"][1]["project_code"] is None
        assert_no_internal_ids(created)

        listed = structured(await call_tool(sessions["ca"], "document_list", {}))
        assert listed["total"] == 1
        assert listed["items"][0]["doc_no"] == created["doc_no"]
        assert_no_internal_ids(listed)

        detail = structured(
            await call_tool(sessions["ca"], "document_list", {"doc_no": created["doc_no"]})
        )
        assert detail["lines"][0]["material_code"] == master_data.material.code
        assert detail["lines"][0]["quantity"] == "2.500"
        assert detail["submitted_at"] is None

        by_project = structured(
            await call_tool(
                sessions["ca"], "document_list", {"project_code": master_data.project.code}
            )
        )
        assert by_project["total"] == 1
        by_manufacturer = structured(
            await call_tool(
                sessions["ca"],
                "document_list",
                {"manufacturer_code": master_data.manufacturer.code},
            )
        )
        assert by_manufacturer["total"] == 1
        by_status = structured(
            await call_tool(sessions["ca"], "document_list", {"status": "POSTED"})
        )
        assert by_status["total"] == 0
        by_missing = structured(
            await call_tool(sessions["ca"], "document_list", {"warehouse_code": "CK-99999"})
        )
        assert by_missing["total"] == 0

        missing = await call_tool(sessions["ca"], "document_list", {"doc_no": "RK20260101-999999"})
        assert tool_error(missing)["code"] == "RESOURCE_NOT_FOUND"


async def test_document_create_parameter_rules(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
) -> None:
    base = {
        "doc_type": "INBOUND",
        "warehouse_code": master_data.warehouse.code,
        "department_code": master_data.department.code,
        "lines": [
            {
                "material_code": master_data.material.code,
                "quantity": "1",
                "manufacturer_code": master_data.manufacturer.code,
            }
        ],
    }
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        top_level_project = await call_tool(
            sessions["ca"],
            "document_create",
            {**base, "project_code": master_data.project.code},
        )
        assert tool_error(top_level_project)["code"] == "VALIDATION_ERROR"

        outbound_manufacturer = await call_tool(
            sessions["ca"],
            "document_create",
            {
                **base,
                "doc_type": "OUTBOUND",
            },
        )
        assert tool_error(outbound_manufacturer)["code"] == "VALIDATION_ERROR"

        missing_manufacturer = await call_tool(
            sessions["ca"],
            "document_create",
            {
                **base,
                "lines": [{"material_code": master_data.material.code, "quantity": "1"}],
            },
        )
        error = tool_error(missing_manufacturer)
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"]["items"][0]["field"].startswith("lines[1]")

        duplicate = await call_tool(
            sessions["ca"],
            "document_create",
            {
                **base,
                "lines": [
                    {
                        "material_code": master_data.material.code,
                        "quantity": "1",
                        "manufacturer_code": master_data.manufacturer.code,
                        "project_code": master_data.project.code,
                    },
                    {
                        "material_code": master_data.material.code,
                        "quantity": "2",
                        "manufacturer_code": master_data.manufacturer.code,
                        "project_code": master_data.project.code,
                    },
                ],
            },
        )
        error = tool_error(duplicate)
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"]["items"][0]["first_line_no"] == 1

        missing_code = await call_tool(
            sessions["ca"],
            "document_create",
            {**base, "warehouse_code": "CK-99999"},
        )
        error = tool_error(missing_code)
        assert error["code"] == "RESOURCE_NOT_FOUND"
        assert_no_internal_ids(error)
