"""API 契约收口测试：OpenAPI、分页、排序白名单、错误结构与请求标识。

依据 docs/09-HTTP-API通用约定与健康检查.md 8.1、docs/10 8.3/8.4、docs/11 8.5，
以及 docs/16-验收标准.md ACC-API-001/002/009。
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.audit.models import AuditLog
from tests.conftest import MasterDataFixture
from tests.support import create_document, csrf, inbound_payload, line, submit_document

# docs/10 8.3、8.4 与 docs/11 8.5 声明的全部操作；`/backups` 随 T8.7/T9.4
# 的备份实现加入。路径参数名不参与 URL 契约比较。
DOCUMENTED_OPERATIONS: dict[str, set[str]] = {
    "/auth/login": {"post"},
    "/auth/logout": {"post"},
    "/auth/me": {"get"},
    "/auth/change-password": {"post"},
    "/api/v1/api-keys": {"get", "post"},
    "/api/v1/api-keys/{}/revoke": {"post"},
    "/api/v1/users": {"get", "post"},
    "/api/v1/users/{}": {"patch"},
    "/api/v1/users/{}/disable": {"post"},
    "/api/v1/users/{}/reset-password": {"post"},
    "/api/v1/warehouses": {"get", "post"},
    "/api/v1/warehouses/{}": {"delete"},
    "/api/v1/warehouses/{}/disable": {"post"},
    "/api/v1/warehouses/{}/enable": {"post"},
    "/api/v1/categories": {"get", "post"},
    "/api/v1/categories/{}": {"delete"},
    "/api/v1/categories/{}/disable": {"post"},
    "/api/v1/categories/{}/enable": {"post"},
    "/api/v1/materials": {"get", "post"},
    "/api/v1/materials/{}": {"delete"},
    "/api/v1/materials/{}/disable": {"post"},
    "/api/v1/materials/{}/enable": {"post"},
    "/api/v1/departments": {"get", "post"},
    "/api/v1/departments/{}": {"delete"},
    "/api/v1/departments/{}/disable": {"post"},
    "/api/v1/departments/{}/enable": {"post"},
    "/api/v1/projects": {"get", "post"},
    "/api/v1/projects/{}": {"delete"},
    "/api/v1/projects/{}/disable": {"post"},
    "/api/v1/projects/{}/enable": {"post"},
    "/api/v1/manufacturers": {"get", "post"},
    "/api/v1/manufacturers/{}": {"delete"},
    "/api/v1/manufacturers/{}/disable": {"post"},
    "/api/v1/manufacturers/{}/enable": {"post"},
    "/api/v1/documents": {"get", "post"},
    "/api/v1/documents/{}": {"get", "patch", "delete"},
    "/api/v1/documents/{}/submit": {"post"},
    "/api/v1/documents/{}/delete": {"post"},
    "/api/v1/documents/{}/export": {"get"},
    "/api/v1/document-templates/{}": {"get"},
    "/api/v1/document-entry/parse": {"post"},
    "/api/v1/inventory": {"get"},
    "/api/v1/inventory/transactions": {"get"},
    "/api/v1/audit-logs": {"get"},
    "/health/live": {"get"},
    "/health/ready": {"get"},
}

LIST_PATHS = [
    "/api/v1/users",
    "/api/v1/api-keys",
    "/api/v1/warehouses",
    "/api/v1/categories",
    "/api/v1/materials",
    "/api/v1/departments",
    "/api/v1/projects",
    "/api/v1/manufacturers",
    "/api/v1/documents",
    "/api/v1/inventory",
    "/api/v1/inventory/transactions",
    "/api/v1/audit-logs",
]

PUBLIC_OPERATIONS = {"/auth/login", "/health/live", "/health/ready"}

SORT_RESOURCES = {
    "/api/v1/users": "user",
    "/api/v1/api-keys": "api_key",
    "/api/v1/warehouses": "warehouse",
    "/api/v1/categories": "category",
    "/api/v1/materials": "material",
    "/api/v1/departments": "department",
    "/api/v1/projects": "project",
    "/api/v1/manufacturers": "manufacturer",
    "/api/v1/documents": "document",
    "/api/v1/inventory": "inventory",
    "/api/v1/inventory/transactions": "inventory_transaction",
    "/api/v1/audit-logs": "audit_log",
}


def _normalize_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def _schema(client: TestClient) -> dict[str, Any]:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def _operations(schema: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for path, item in schema["paths"].items():
        methods = {
            method
            for method in item
            if method in {"get", "post", "put", "patch", "delete", "options", "head"}
        }
        if methods:
            result[_normalize_path(path)] = methods
    return result


def _error(response_json: dict[str, Any]) -> dict[str, Any]:
    return response_json["error"]


def _assert_unified_error(
    response_json: dict[str, Any], *, code: str | None = None
) -> dict[str, Any]:
    error = _error(response_json)
    assert set(error) == {"code", "message", "request_id", "details"}
    if code is not None:
        assert error["code"] == code
    assert isinstance(error["details"], dict)
    assert "resource" in error["details"]
    assert isinstance(error["details"]["items"], list)
    assert UUID(error["request_id"])
    return error


def test_openapi_matches_documented_paths_and_methods(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin
    schema = _schema(client)

    assert _operations(schema) == DOCUMENTED_OPERATIONS
    assert "/mcp" not in schema["paths"]
    # 不存在 XLSX 直接建单、解析批次查询或错误报告接口。
    assert not any(
        "import" in path or "batch" in path or "parse-results" in path for path in schema["paths"]
    )


def test_openapi_declares_unified_errors_security_and_request_id(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin
    schema = _schema(client)

    components = schema["components"]
    assert components["securitySchemes"]["sessionCookie"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "only_stock_session",
        "description": "登录成功后由 /auth/login 设置的 HttpOnly 会话 Cookie",
    }
    error_response = components["schemas"]["ErrorResponse"]
    assert error_response["required"] == ["error"]
    error_body = components["schemas"]["ErrorBody"]
    assert set(error_body["required"]) == {"code", "message", "request_id", "details"}
    details = components["schemas"]["ErrorDetails"]
    assert set(details["required"]) == {"resource", "items"}

    for path, item in schema["paths"].items():
        normalized = _normalize_path(path)
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if normalized.startswith("/api/v1") or normalized.startswith("/auth"):
                for status in ("400", "401", "403", "404", "409", "422", "500"):
                    content = operation["responses"][status]["content"]["application/json"]
                    assert content["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
            if normalized in PUBLIC_OPERATIONS:
                assert "security" not in operation
            else:
                assert operation["security"] == [{"sessionCookie": []}]
            for response in operation["responses"].values():
                assert "X-Request-ID" in response["headers"]

            headers = {
                parameter["name"]: parameter
                for parameter in operation.get("parameters", [])
                if parameter["in"] == "header"
            }
            csrf_required = method in {"post", "put", "patch", "delete"} and normalized != (
                "/auth/login"
            )
            if csrf_required:
                assert headers["X-CSRF-Token"]["required"] is True
            else:
                assert "X-CSRF-Token" not in headers

    declared_tags = {tag["name"] for tag in schema["tags"]}
    assert declared_tags == {
        "health",
        "auth",
        "api-keys",
        "users",
        "master-data",
        "documents",
        "xlsx",
        "inventory",
        "audit",
    }
    for tag in schema["tags"]:
        assert tag["description"]


def test_openapi_list_operations_share_pagination_and_sort_contract(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin
    schema = _schema(client)

    for path in LIST_PATHS:
        parameters = {
            parameter["name"]: parameter for parameter in schema["paths"][path]["get"]["parameters"]
        }
        assert {"page", "page_size", "sort", "order"} <= set(parameters)
        page = parameters["page"]["schema"]
        page_size = parameters["page_size"]["schema"]
        assert page["default"] == 1
        assert page["minimum"] == 1
        assert page_size["default"] == 20
        assert page_size["minimum"] == 1
        assert page_size["maximum"] == 100
        assert parameters["sort"]["schema"]["anyOf"][0]["type"] == "string"
        order_schema = parameters["order"]["schema"]["anyOf"][0]
        assert order_schema == {"$ref": "#/components/schemas/SortOrder"}

    assert schema["components"]["schemas"]["SortOrder"]["enum"] == ["asc", "desc"]


def test_pagination_defaults_bounds_and_page_metadata(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin

    default = client.get("/api/v1/warehouses")
    assert default.status_code == 200
    assert default.json()["page"] == 1
    assert default.json()["page_size"] == 20

    maximum = client.get("/api/v1/warehouses", params={"page_size": 100})
    assert maximum.status_code == 200
    assert maximum.json()["page_size"] == 100

    for params in ({"page_size": 101}, {"page": 0}, {"page_size": 0}):
        response = client.get("/api/v1/warehouses", params=params)
        assert response.status_code == 422
        error = _assert_unified_error(response.json(), code="VALIDATION_ERROR")
        assert error["details"]["items"][0]["field"].startswith("query.")


@pytest.mark.parametrize("path", LIST_PATHS)
def test_invalid_sort_returns_validation_error_for_every_list(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    path: str,
) -> None:
    client, _ = logged_in_admin

    response = client.get(path, params={"sort": "password_hash"})

    assert response.status_code == 422
    error = _assert_unified_error(response.json(), code="VALIDATION_ERROR")
    assert error["details"]["resource"] == SORT_RESOURCES[path]
    item = error["details"]["items"][0]
    assert item["field"] == "sort"
    assert item["value"] == "password_hash"
    assert item["allowed"]
    assert "password_hash" not in item["allowed"]


def test_invalid_order_value_returns_unified_error(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin

    response = client.get("/api/v1/warehouses", params={"sort": "code", "order": "sideways"})

    assert response.status_code == 422
    _assert_unified_error(response.json(), code="VALIDATION_ERROR")


def test_master_data_sorting_respects_whitelist_and_direction(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    # 补足每条列表至少两条记录，确保排序断言有区分度。
    for path, payload in (
        ("/api/v1/departments", {"name": "质检部"}),
        ("/api/v1/manufacturers", {"name": "铝厂"}),
        ("/api/v1/categories", {"name": "半成品"}),
    ):
        assert client.post(path, json=payload, headers=csrf(identity)).status_code == 201

    for path, field in (
        ("/api/v1/warehouses", "code"),
        ("/api/v1/departments", "code"),
        ("/api/v1/projects", "code"),
        ("/api/v1/manufacturers", "code"),
        ("/api/v1/categories", "code"),
        ("/api/v1/materials", "code"),
    ):
        ascending = client.get(path, params={"sort": field, "order": "asc", "page_size": 100})
        descending = client.get(path, params={"sort": field, "order": "desc", "page_size": 100})
        assert ascending.status_code == 200, ascending.text
        assert descending.status_code == 200, descending.text
        values = [item[field] for item in ascending.json()["items"]]
        reverse_values = [item[field] for item in descending.json()["items"]]
        assert values == sorted(values)
        assert reverse_values == sorted(reverse_values, reverse=True)
        assert len(values) >= 2

    default_warehouses = client.get("/api/v1/warehouses", params={"page_size": 100})
    default_codes = [item["code"] for item in default_warehouses.json()["items"]]
    assert default_codes == sorted(default_codes)


def test_documents_inventory_transactions_and_audit_sorting(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin

    for remark in ("排序 A", "排序 B", "排序 C"):
        create_document(
            client,
            identity,
            inbound_payload(
                master_data,
                lines=[line(master_data, quantity="1.000", remark=remark)],
            ),
        )
    document = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[
                line(master_data, material=master_data.material, quantity="2.000"),
                line(master_data, material=master_data.material_second, quantity="3.000"),
            ],
        ),
    )
    submit_document(client, identity, document["id"], document["version"])

    documents = client.get(
        "/api/v1/documents", params={"sort": "doc_no", "order": "asc", "page_size": 100}
    )
    assert documents.status_code == 200
    doc_nos = [item["doc_no"] for item in documents.json()["items"]]
    assert doc_nos == sorted(doc_nos)
    assert len(doc_nos) >= 4
    documents_desc = client.get(
        "/api/v1/documents", params={"sort": "doc_no", "order": "desc", "page_size": 100}
    )
    assert [item["doc_no"] for item in documents_desc.json()["items"]] == sorted(
        doc_nos, reverse=True
    )

    inventory = client.get(
        "/api/v1/inventory", params={"sort": "material_code", "order": "asc", "page_size": 100}
    )
    assert inventory.status_code == 200
    material_codes = [item["code"] for item in inventory.json()["items"]]
    assert material_codes == sorted(material_codes)
    assert len(material_codes) >= 2
    inventory_desc = client.get(
        "/api/v1/inventory", params={"sort": "material_code", "order": "desc", "page_size": 100}
    )
    assert [item["code"] for item in inventory_desc.json()["items"]] == sorted(
        material_codes, reverse=True
    )
    by_balance = client.get(
        "/api/v1/inventory", params={"sort": "balance", "order": "desc", "page_size": 100}
    )
    balances = [Decimal(item["balance"]) for item in by_balance.json()["items"]]
    assert balances == sorted(balances, reverse=True)
    assert len(balances) >= 2

    transactions = client.get(
        "/api/v1/inventory/transactions",
        params={"sort": "line_no", "order": "desc", "page_size": 100},
    )
    assert transactions.status_code == 200
    line_nos = [item["line_no"] for item in transactions.json()["items"]]
    assert line_nos == sorted(line_nos, reverse=True)
    assert line_nos.count(1) >= 1 and line_nos.count(2) >= 1

    audit_asc = client.get(
        "/api/v1/audit-logs", params={"sort": "created_at", "order": "asc", "page_size": 100}
    )
    audit_desc = client.get(
        "/api/v1/audit-logs", params={"sort": "created_at", "order": "desc", "page_size": 100}
    )
    asc_times = [datetime.fromisoformat(item["created_at"]) for item in audit_asc.json()["items"]]
    desc_times = [datetime.fromisoformat(item["created_at"]) for item in audit_desc.json()["items"]]
    assert asc_times == sorted(asc_times)
    assert desc_times == sorted(desc_times, reverse=True)
    assert len(asc_times) >= 2

    default_documents = client.get("/api/v1/documents", params={"page_size": 100})
    assert [item["doc_no"] for item in default_documents.json()["items"]] == sorted(
        doc_nos, reverse=True
    )
    default_audit = client.get("/api/v1/audit-logs", params={"page_size": 100})
    default_times = [
        datetime.fromisoformat(item["created_at"]) for item in default_audit.json()["items"]
    ]
    assert default_times == sorted(default_times, reverse=True)


def test_users_and_api_keys_sorting(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin

    users = client.get(
        "/api/v1/users", params={"sort": "login_name", "order": "asc", "page_size": 100}
    )
    assert users.status_code == 200
    login_names = [item["login_name"] for item in users.json()["items"]]
    assert login_names == sorted(login_names)
    assert len(login_names) >= 2

    for name in ("beta-client", "alpha-client"):
        assert (
            client.post("/api/v1/api-keys", json={"name": name}, headers=csrf(identity)).status_code
            == 201
        )
    keys = client.get("/api/v1/api-keys", params={"sort": "name", "order": "asc", "page_size": 100})
    assert keys.status_code == 200
    names = [item["name"] for item in keys.json()["items"]]
    assert names == sorted(names) == ["alpha-client", "beta-client"]


def test_error_details_are_objects_across_status_codes(
    admin_app: Any,
    admin_client: TestClient,
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin

    unauthenticated = TestClient(admin_app).get("/api/v1/warehouses")
    assert unauthenticated.status_code == 401
    _assert_unified_error(unauthenticated.json(), code="UNAUTHORIZED")

    not_found = client.get(f"/api/v1/documents/{uuid4()}")
    assert not_found.status_code == 404
    error = _assert_unified_error(not_found.json(), code="DOCUMENT_NOT_FOUND")
    assert error["details"]["resource"] == "document"

    conflict = client.post(
        "/api/v1/warehouses",
        json={"name": master_data.warehouse.name},
        headers=csrf(identity),
    )
    assert conflict.status_code == 409
    error = _assert_unified_error(conflict.json(), code="NAME_CONFLICT")
    assert error["details"]["resource"] == "warehouse"
    assert error["details"]["items"]

    invalid = client.get("/api/v1/warehouses", params={"sort": "nope"})
    assert invalid.status_code == 422
    _assert_unified_error(invalid.json(), code="VALIDATION_ERROR")

    method = client.put("/api/v1/warehouses")
    assert method.status_code == 405
    _assert_unified_error(method.json(), code="METHOD_NOT_ALLOWED")


def test_request_id_semantics_for_web_api_and_server_generated_requests(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin

    listing = client.get("/api/v1/warehouses")
    assert listing.status_code == 200
    server_request_id = listing.headers["X-Request-ID"]
    assert UUID(server_request_id)

    request_id = str(uuid4())
    created = client.post(
        "/api/v1/documents",
        json=inbound_payload(
            master_data,
            request_id=request_id,
            lines=[line(master_data, quantity="1.000")],
        ),
        headers=csrf(identity),
    )
    assert created.status_code == 201, created.text
    assert created.headers["X-Request-ID"] == request_id
    assert created.json()["request_id"] == request_id

    replay = client.post(
        "/api/v1/documents",
        json=inbound_payload(
            master_data,
            request_id=request_id,
            lines=[line(master_data, quantity="1.000")],
        ),
        headers=csrf(identity),
    )
    assert replay.status_code == 200
    assert replay.headers["X-Request-ID"] == request_id
    assert replay.json()["doc_no"] == created.json()["doc_no"]

    conflicting = client.post(
        "/api/v1/documents",
        json=inbound_payload(
            master_data,
            request_id=request_id,
            lines=[line(master_data, quantity="2.000")],
        ),
        headers=csrf(identity),
    )
    assert conflicting.status_code == 409
    error = _assert_unified_error(conflicting.json(), code="REQUEST_ID_CONFLICT")
    assert error["request_id"] == request_id
    assert conflicting.headers["X-Request-ID"] == request_id

    warehouse_name = "请求标识仓库"
    created_warehouse = client.post(
        "/api/v1/warehouses", json={"name": warehouse_name}, headers=csrf(identity)
    )
    assert created_warehouse.status_code == 201
    warehouse_request_id = created_warehouse.headers["X-Request-ID"]
    assert UUID(warehouse_request_id)
    assert warehouse_request_id != request_id

    with session_factory() as session:
        audit = session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "WAREHOUSE_CREATED")
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        ).one()
        assert audit.request_id == warehouse_request_id

    inventory = client.get("/api/v1/inventory")
    assert inventory.status_code == 200
    assert UUID(inventory.headers["X-Request-ID"])
