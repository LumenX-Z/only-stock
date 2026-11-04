"""MCP 工具契约、错误结构与输出脱敏单元测试。

依据 docs/08-MCP服务与工具.md 8.6.2、8.6.3、8.6.5 与 docs/16-验收标准.md ACC-MCP-013。
"""

import json
from uuid import uuid4

import pytest

from app.errors import AppError
from app.mcp import errors, schemas, tools, validation

CA_TOOLS = {
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
}
OP_TOOLS = CA_TOOLS - {
    "warehouse_create",
    "department_create",
    "manufacturer_create",
    "category_create",
    "material_create",
}


def test_tool_catalog_has_exactly_twelve_tools() -> None:
    assert set(tools.TOOL_BY_NAME) == CA_TOOLS
    assert {tool.name for tool in tools.visible_definitions("COMPANY_ADMIN")} == CA_TOOLS
    assert {tool.name for tool in tools.visible_definitions("OPERATOR")} == OP_TOOLS


def test_tool_schemas_only_declare_business_fields() -> None:
    for definition in tools.TOOL_DEFINITIONS:
        assert definition.input_schema["type"] == "object"
        assert definition.input_schema["additionalProperties"] is False
        assert definition.output_schema["type"] == "object"
        assert definition.output_schema["additionalProperties"] is False
        for schema in (definition.input_schema, definition.output_schema):
            for name in schema["properties"]:
                assert name != "id"
                assert name != "request_id"
                assert not name.endswith("_id")


def test_document_create_description_documents_dedup() -> None:
    description = tools.TOOL_BY_NAME["document_create"].description

    assert "300 秒" in description
    assert "deduplicated" in description
    assert "request_id" not in description


def test_document_create_input_requires_business_codes() -> None:
    properties = schemas.DOCUMENT_CREATE_INPUT["properties"]

    assert properties["lines"]["items"]["properties"]["material_code"]["type"] == "string"
    assert "quantity" in properties["lines"]["items"]["required"]
    assert "manufacturer_code" in properties["lines"]["items"]["properties"]
    assert set(properties["lines"]["items"]["properties"]) == {
        "material_code",
        "quantity",
        "project_code",
        "manufacturer_code",
        "remark",
    }


def test_sanitize_items_removes_internal_ids_and_uuids() -> None:
    internal_uuid = str(uuid4())
    items = [
        {
            "id": internal_uuid,
            "material_id": internal_uuid,
            "code": "WZ-00001",
            "name": "示例物资",
            "deleted_by": internal_uuid,
            "related": internal_uuid,
            "nested": [{"project_id": internal_uuid, "project_code": "XM-00001"}],
        }
    ]

    cleaned = errors.sanitize_items(items)

    assert cleaned == [
        {
            "code": "WZ-00001",
            "name": "示例物资",
            "nested": [{"project_code": "XM-00001"}],
        }
    ]


def test_tool_error_has_structured_envelope_with_request_id() -> None:
    request_id = str(uuid4())

    result = errors.from_app_error(
        AppError(
            "NAME_CONFLICT",
            "仓库名称已存在",
            status_code=409,
            resource="warehouse",
            items=[{"id": str(uuid4()), "code": "CK-00001", "name": "主仓"}],
        ),
        request_id,
    )

    assert result.is_error is True
    assert result.structured_content is not None
    error = result.structured_content["error"]
    assert error["code"] == "NAME_CONFLICT"
    assert error["request_id"] == request_id
    assert error["details"] == {
        "resource": "warehouse",
        "items": [{"code": "CK-00001", "name": "主仓"}],
    }
    text = result.content[0].text
    assert json.loads(text)["error"] == error


def test_success_result_carries_structured_and_text_payload() -> None:
    result = errors.success({"code": "CK-00001", "items": [], "total": 0})

    assert result.is_error is False
    assert result.structured_content == {"code": "CK-00001", "items": [], "total": 0}
    assert json.loads(result.content[0].text) == result.structured_content


@pytest.mark.parametrize("key", ["id", "request_id", "warehouse_id", "deleted_by"])
def test_reject_unknown_reports_internal_arguments(key: str) -> None:
    with pytest.raises(AppError) as error:
        validation.reject_unknown({key: str(uuid4())}, {"code"})

    assert error.value.code == "VALIDATION_ERROR"
    assert error.value.items[0]["field"] == key


def test_business_code_rejects_internal_uuid() -> None:
    with pytest.raises(AppError) as error:
        validation.required_code({"code": str(uuid4())}, "code")

    assert error.value.code == "VALIDATION_ERROR"
    assert "UUID" in error.value.items[0]["reason"]


def test_page_parameters_have_documented_defaults_and_bounds() -> None:
    assert validation.page_parameters({}) == (1, 20)
    assert validation.page_parameters({"page": 3, "page_size": 100}) == (3, 100)
    for bad in ({"page": 0}, {"page_size": 101}, {"page": "1"}, {"page": True}):
        with pytest.raises(AppError):
            validation.page_parameters(bad)
