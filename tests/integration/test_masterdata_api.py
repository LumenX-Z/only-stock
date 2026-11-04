"""六类基础资料 API 集成测试：编码、名称唯一、层级、停用启用、删除与权限。"""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.audit.models import AuditLog
from app.modules.masterdata.base import CodeCounter
from app.modules.materials.models import Material
from app.modules.users.models import Role, User

_CREATE_PATHS = {
    "warehouse": "/api/v1/warehouses",
    "department": "/api/v1/departments",
    "category": "/api/v1/categories",
    "material": "/api/v1/materials",
    "project": "/api/v1/projects",
    "manufacturer": "/api/v1/manufacturers",
}


def _csrf(identity: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": str(identity["csrf_token"])}


def _post(
    client: TestClient,
    identity: dict[str, Any],
    path: str,
    payload: dict[str, Any],
    *,
    expected: int = 201,
) -> dict[str, Any]:
    response = client.post(path, json=payload, headers=_csrf(identity))
    assert response.status_code == expected, response.text
    return response.json()


def _category(
    client: TestClient, identity: dict[str, Any], name: str, parent_id: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    return _post(client, identity, "/api/v1/categories", payload)


def _material(
    client: TestClient,
    identity: dict[str, Any],
    category_id: str,
    name: str = "物料",
    unit: str = "个",
    quantity_type: str = "DECIMAL",
) -> dict[str, Any]:
    return _post(
        client,
        identity,
        "/api/v1/materials",
        {
            "category_id": category_id,
            "name": name,
            "unit": unit,
            "quantity_type": quantity_type,
        },
    )


def test_six_entities_use_documented_code_prefixes(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin

    warehouse = _post(client, identity, "/api/v1/warehouses", {"name": "主仓"})
    department = _post(client, identity, "/api/v1/departments", {"name": "生产部"})
    first_category = _category(client, identity, "原材料")
    second_category = _category(client, identity, "钢材", first_category["id"])
    material = _material(client, identity, second_category["id"], "螺纹钢")
    project = _post(client, identity, "/api/v1/projects", {"name": "一号项目"})
    manufacturer = _post(client, identity, "/api/v1/manufacturers", {"name": "钢厂"})

    assert warehouse["code"] == "CK-00001"
    assert department["code"] == "BM-00001"
    assert first_category["code"] == "FL-00001"
    assert second_category["code"] == "FL-00002"
    assert material["code"] == "WZ-00001"
    assert project["code"] == "XM-00001"
    assert manufacturer["code"] == "CJ-00001"


@pytest.mark.parametrize("entity_key", sorted(_CREATE_PATHS))
def test_create_requests_reject_client_supplied_code(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    entity_key: str,
) -> None:
    client, identity = logged_in_admin
    category = _category(client, identity, "分类")
    payloads: dict[str, dict[str, Any]] = {
        "warehouse": {"name": "仓库"},
        "department": {"name": "部门"},
        "category": {"name": "一级"},
        "material": {
            "category_id": category["id"],
            "name": "物资",
            "unit": "个",
            "quantity_type": "DECIMAL",
        },
        "project": {"name": "项目"},
        "manufacturer": {"name": "厂家"},
    }
    payload = dict(payloads[entity_key])
    payload["code"] = "CUSTOM-1"

    response = client.post(_CREATE_PATHS[entity_key], json=payload, headers=_csrf(identity))

    assert response.status_code == 422, response.text
    created = _post(client, identity, _CREATE_PATHS[entity_key], payloads[entity_key])
    assert created["code"].startswith(
        {
            "warehouse": "CK",
            "department": "BM",
            "category": "FL",
            "material": "WZ",
            "project": "XM",
            "manufacturer": "CJ",
        }[entity_key]
    )


def test_global_names_are_trimmed_case_sensitive_and_unique(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    created = _post(client, identity, "/api/v1/warehouses", {"name": "  主仓  "})
    assert created["name"] == "主仓"

    duplicate = client.post("/api/v1/warehouses", json={"name": "主仓 "}, headers=_csrf(identity))
    assert duplicate.status_code == 409
    error = duplicate.json()["error"]
    assert error["code"] == "NAME_CONFLICT"
    assert error["details"]["resource"] == "warehouse"
    assert error["details"]["items"][0]["name"] == "主仓"
    assert error["details"]["items"][0]["is_active"] is True

    assert (
        client.post(
            "/api/v1/warehouses", json={"name": "alpha"}, headers=_csrf(identity)
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/warehouses", json={"name": "Alpha"}, headers=_csrf(identity)
        ).status_code
        == 201
    )


def test_category_and_material_name_scopes(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    parent_a = _category(client, identity, "一级 A")
    parent_b = _category(client, identity, "一级 B")

    child_a = _category(client, identity, "子类", parent_a["id"])
    child_b = _category(client, identity, "子类", parent_b["id"])
    assert child_a["code"] != child_b["code"]

    duplicate = client.post(
        "/api/v1/categories",
        json={"name": "子类", "parent_id": parent_a["id"]},
        headers=_csrf(identity),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["details"]["items"][0]["scope"] == "parent"

    material_a = _material(client, identity, child_a["id"], "同名物资")
    material_b = _material(client, identity, child_b["id"], "同名物资")
    assert material_a["code"] == "WZ-00001"
    assert material_b["code"] == "WZ-00002"

    duplicate_material = client.post(
        "/api/v1/materials",
        json={
            "category_id": child_a["id"],
            "name": "同名物资",
            "unit": "个",
            "quantity_type": "DECIMAL",
        },
        headers=_csrf(identity),
    )
    assert duplicate_material.status_code == 409
    assert duplicate_material.json()["error"]["code"] == "NAME_CONFLICT"
    assert duplicate_material.json()["error"]["details"]["items"][0]["scope"] == "category"


def test_third_category_level_is_rejected(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    root = _category(client, identity, "一级")
    child = _category(client, identity, "二级", root["id"])

    response = client.post(
        "/api/v1/categories",
        json={"name": "三级", "parent_id": child["id"]},
        headers=_csrf(identity),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CATEGORY_LEVEL_INVALID"


def test_category_tree_and_default_active_filter(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    root_a = _category(client, identity, "一级 A")
    child_a1 = _category(client, identity, "A-1", root_a["id"])
    _category(client, identity, "A-2", root_a["id"])
    _category(client, identity, "一级 B")

    tree = client.get("/api/v1/categories")
    assert tree.status_code == 200
    body = tree.json()
    assert body["total"] == 2
    root_node = next(item for item in body["items"] if item["code"] == root_a["code"])
    assert [child["name"] for child in root_node["children"]] == ["A-1", "A-2"]
    assert all(child["parent_code"] == root_a["code"] for child in root_node["children"])

    disabled = client.post(f"/api/v1/categories/{child_a1['id']}/disable", headers=_csrf(identity))
    assert disabled.status_code == 200

    active_tree = client.get("/api/v1/categories").json()
    active_root = next(item for item in active_tree["items"] if item["code"] == root_a["code"])
    assert [child["name"] for child in active_root["children"]] == ["A-2"]

    all_tree = client.get("/api/v1/categories?include_inactive=true").json()
    all_root = next(item for item in all_tree["items"] if item["code"] == root_a["code"])
    assert {child["name"] for child in all_root["children"]} == {"A-1", "A-2"}


def test_category_disable_requires_bottom_up_and_enable_requires_parent(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    root = _category(client, identity, "一级")
    child = _category(client, identity, "二级", root["id"])
    material = _material(client, identity, child["id"], "物资")

    root_blocked = client.post(f"/api/v1/categories/{root['id']}/disable", headers=_csrf(identity))
    assert root_blocked.status_code == 409
    assert root_blocked.json()["error"]["code"] == "CATEGORY_HAS_ACTIVE_CHILDREN"
    root_items = root_blocked.json()["error"]["details"]["items"]
    assert {item["kind"] for item in root_items} == {"category", "material"}
    assert all(item["count"] == 1 for item in root_items)

    child_blocked = client.post(
        f"/api/v1/categories/{child['id']}/disable", headers=_csrf(identity)
    )
    assert child_blocked.status_code == 409
    assert child_blocked.json()["error"]["code"] == "CATEGORY_HAS_ACTIVE_MATERIALS"

    assert (
        client.post(
            f"/api/v1/materials/{material['id']}/disable", headers=_csrf(identity)
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/categories/{child['id']}/disable", headers=_csrf(identity)
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/categories/{root['id']}/disable", headers=_csrf(identity)).status_code
        == 200
    )

    child_enable = client.post(f"/api/v1/categories/{child['id']}/enable", headers=_csrf(identity))
    assert child_enable.status_code == 409
    assert child_enable.json()["error"]["code"] == "PARENT_INACTIVE"
    assert child_enable.json()["error"]["details"]["items"][0]["name"] == "一级"

    assert (
        client.post(f"/api/v1/categories/{root['id']}/enable", headers=_csrf(identity)).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/categories/{child['id']}/enable", headers=_csrf(identity)).status_code
        == 200
    )
    material_enable = client.post(
        f"/api/v1/materials/{material['id']}/enable", headers=_csrf(identity)
    )
    assert material_enable.status_code == 200
    assert material_enable.json()["is_active"] is True


def test_material_requires_active_category_and_parent(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    root = _category(client, identity, "一级")
    child = _category(client, identity, "二级", root["id"])
    material = _material(client, identity, child["id"], "物资")

    assert (
        client.post(
            f"/api/v1/materials/{material['id']}/disable", headers=_csrf(identity)
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/categories/{child['id']}/disable", headers=_csrf(identity)
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/categories/{root['id']}/disable", headers=_csrf(identity)).status_code
        == 200
    )

    blocked = client.post(f"/api/v1/materials/{material['id']}/enable", headers=_csrf(identity))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PARENT_INACTIVE"

    create_under_inactive = client.post(
        "/api/v1/materials",
        json={
            "category_id": child["id"],
            "name": "新物资",
            "unit": "个",
            "quantity_type": "DECIMAL",
        },
        headers=_csrf(identity),
    )
    assert create_under_inactive.status_code == 409
    assert create_under_inactive.json()["error"]["code"] == "PARENT_INACTIVE"


def test_category_delete_checks_children_materials_and_references(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    empty = _category(client, identity, "空分类")
    assert (
        client.delete(f"/api/v1/categories/{empty['id']}", headers=_csrf(identity)).status_code
        == 204
    )

    root = _category(client, identity, "有子级")
    child = _category(client, identity, "子级", root["id"])
    blocked = client.delete(f"/api/v1/categories/{root['id']}", headers=_csrf(identity))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "CATEGORY_HAS_CHILDREN"

    material = _material(client, identity, child["id"], "物资")
    blocked_child = client.delete(f"/api/v1/categories/{child['id']}", headers=_csrf(identity))
    assert blocked_child.status_code == 409
    assert blocked_child.json()["error"]["code"] == "CATEGORY_HAS_MATERIALS"

    assert (
        client.delete(f"/api/v1/materials/{material['id']}", headers=_csrf(identity)).status_code
        == 204
    )
    assert (
        client.delete(f"/api/v1/categories/{child['id']}", headers=_csrf(identity)).status_code
        == 204
    )


def test_warehouse_disable_enable_is_idempotent_and_audited(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    warehouse = _post(client, identity, "/api/v1/warehouses", {"name": "主仓"})

    disabled = client.post(f"/api/v1/warehouses/{warehouse['id']}/disable", headers=_csrf(identity))
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    assert disabled.json()["version"] == 2
    assert client.get("/api/v1/warehouses").json()["total"] == 0
    assert client.get("/api/v1/warehouses?include_inactive=true").json()["total"] == 1

    repeated = client.post(f"/api/v1/warehouses/{warehouse['id']}/disable", headers=_csrf(identity))
    assert repeated.status_code == 200
    assert repeated.json()["version"] == 2

    enabled = client.post(f"/api/v1/warehouses/{warehouse['id']}/enable", headers=_csrf(identity))
    assert enabled.status_code == 200
    assert enabled.json()["is_active"] is True
    assert enabled.json()["version"] == 3

    with session_factory() as session:
        actions = list(
            session.scalars(
                select(AuditLog.action).where(AuditLog.entity_type == "warehouse")
            ).all()
        )
    assert actions.count("WAREHOUSE_DISABLED") == 1
    assert actions.count("WAREHOUSE_ENABLED") == 1


def test_warehouse_with_nonzero_stock_cannot_be_disabled(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: Any,
) -> None:
    client, identity = logged_in_admin
    warehouse = _post(client, identity, "/api/v1/warehouses", {"name": "库存仓"})
    document = _post(
        client,
        identity,
        "/api/v1/documents",
        {
            "request_id": str(uuid4()),
            "doc_type": "INBOUND",
            "warehouse_id": warehouse["id"],
            "department_id": master_data.department.id,
            "lines": [
                {
                    "material_id": master_data.material.id,
                    "quantity": "1",
                    "manufacturer_id": master_data.manufacturer.id,
                }
            ],
        },
    )
    submitted = client.post(
        f"/api/v1/documents/{document['id']}/submit",
        json={"version": 1},
        headers=_csrf(identity),
    )
    assert submitted.status_code == 200, submitted.text

    response = client.post(f"/api/v1/warehouses/{warehouse['id']}/disable", headers=_csrf(identity))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WAREHOUSE_HAS_STOCK"


def test_codes_are_not_reused_after_delete(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    first = _post(client, identity, "/api/v1/warehouses", {"name": "一号仓"})
    assert first["code"] == "CK-00001"
    assert (
        client.delete(f"/api/v1/warehouses/{first['id']}", headers=_csrf(identity)).status_code
        == 204
    )

    second = _post(client, identity, "/api/v1/warehouses", {"name": "二号仓"})
    assert second["code"] == "CK-00002"


def test_failed_insert_rolls_back_code_counter(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    with session_factory() as session:
        session.execute(
            text(
                "CREATE TRIGGER fail_warehouse_insert BEFORE INSERT ON warehouses "
                "BEGIN SELECT RAISE(ABORT, 'forced warehouse failure'); END"
            )
        )
        session.commit()

    with pytest.raises(IntegrityError, match="forced warehouse failure"):
        client.post("/api/v1/warehouses", json={"name": "失败仓"}, headers=_csrf(identity))

    with session_factory() as session:
        session.execute(text("DROP TRIGGER fail_warehouse_insert"))
        session.commit()
        counter = session.get(CodeCounter, "warehouse")
        assert counter is None or counter.next_value == 1

    created = _post(client, identity, "/api/v1/warehouses", {"name": "成功仓"})
    assert created["code"] == "CK-00001"


def test_material_validation_and_filters(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    root = _category(client, identity, "一级")
    child = _category(client, identity, "二级", root["id"])
    first = _material(client, identity, root["id"], "直接物资", unit="kg")
    second = _material(client, identity, child["id"], "子级物资", unit="吨")

    wrong_type = client.post(
        "/api/v1/materials",
        json={
            "category_id": child["id"],
            "name": "错误类型",
            "unit": "个",
            "quantity_type": "FLOAT",
        },
        headers=_csrf(identity),
    )
    assert wrong_type.status_code == 422

    blank_unit = client.post(
        "/api/v1/materials",
        json={
            "category_id": child["id"],
            "name": "空单位",
            "unit": "   ",
            "quantity_type": "DECIMAL",
        },
        headers=_csrf(identity),
    )
    assert blank_unit.status_code == 422

    by_category = client.get(f"/api/v1/materials?category_code={root['code']}")
    assert [item["code"] for item in by_category.json()["items"]] == [first["code"]]
    by_child = client.get(f"/api/v1/materials?category_id={child['id']}")
    assert [item["code"] for item in by_child.json()["items"]] == [second["code"]]
    assert by_child.json()["items"][0]["category_code"] == child["code"]
    assert by_child.json()["items"][0]["parent_category_code"] == root["code"]
    assert by_child.json()["items"][0]["unit"] == "吨"


def test_operator_can_read_but_cannot_manage(
    admin_client: TestClient,
    user_factory: Callable[..., User],
) -> None:
    user_factory("operator", role=Role.OPERATOR)
    login = admin_client.post(
        "/auth/login", json={"login_name": "operator", "password": "operator-password-123"}
    )
    assert login.status_code == 200
    identity = login.json()

    assert admin_client.get("/api/v1/warehouses").status_code == 200
    assert (
        admin_client.post(
            "/api/v1/warehouses", json={"name": "越权仓"}, headers=_csrf(identity)
        ).status_code
        == 403
    )
    assert (
        admin_client.post(
            "/api/v1/warehouses/00000000-0000-0000-0000-000000000000/disable",
            headers=_csrf(identity),
        ).status_code
        == 403
    )
    assert (
        admin_client.post(
            "/api/v1/categories", json={"name": "越权分类"}, headers=_csrf(identity)
        ).status_code
        == 403
    )
    assert admin_client.get("/api/v1/users").status_code == 403
    assert admin_client.get("/api/v1/audit-logs").status_code == 403


def test_materials_endpoint_only_returns_materials(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    root = _category(client, identity, "一级")
    _material(client, identity, root["id"], "物资")
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Material)) == 1
    assert client.get("/api/v1/materials").json()["total"] == 1


def test_disabled_records_still_occupy_names(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    warehouse = _post(client, identity, "/api/v1/warehouses", {"name": "停用仓"})
    assert (
        client.post(
            f"/api/v1/warehouses/{warehouse['id']}/disable", headers=_csrf(identity)
        ).status_code
        == 200
    )
    duplicate_warehouse = client.post(
        "/api/v1/warehouses", json={"name": "停用仓"}, headers=_csrf(identity)
    )
    assert duplicate_warehouse.status_code == 409
    assert duplicate_warehouse.json()["error"]["details"]["items"][0]["is_active"] is False

    root = _category(client, identity, "一级")
    child = _category(client, identity, "停用子类", root["id"])
    assert (
        client.post(
            f"/api/v1/categories/{child['id']}/disable", headers=_csrf(identity)
        ).status_code
        == 200
    )
    duplicate_category = client.post(
        "/api/v1/categories",
        json={"name": "停用子类", "parent_id": root["id"]},
        headers=_csrf(identity),
    )
    assert duplicate_category.status_code == 409

    material = _material(client, identity, root["id"], "停用物资")
    assert (
        client.post(
            f"/api/v1/materials/{material['id']}/disable", headers=_csrf(identity)
        ).status_code
        == 200
    )
    duplicate_material = client.post(
        "/api/v1/materials",
        json={
            "category_id": root["id"],
            "name": "停用物资",
            "unit": "个",
            "quantity_type": "DECIMAL",
        },
        headers=_csrf(identity),
    )
    assert duplicate_material.status_code == 409


def test_referenced_entities_can_only_be_disabled(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    warehouse = _post(client, identity, "/api/v1/warehouses", {"name": "引用仓"})
    department = _post(client, identity, "/api/v1/departments", {"name": "引用部"})
    category = _category(client, identity, "引用分类")
    material = _material(client, identity, category["id"], "引用物资")
    project = _post(client, identity, "/api/v1/projects", {"name": "引用项目"})
    manufacturer = _post(client, identity, "/api/v1/manufacturers", {"name": "引用厂家"})

    _post(
        client,
        identity,
        "/api/v1/documents",
        {
            "request_id": str(uuid4()),
            "doc_type": "INBOUND",
            "warehouse_id": warehouse["id"],
            "department_id": department["id"],
            "lines": [
                {
                    "material_id": material["id"],
                    "quantity": "1",
                    "manufacturer_id": manufacturer["id"],
                    "project_id": project["id"],
                }
            ],
        },
    )

    for path, entity_id, expected in (
        ("warehouses", warehouse["id"], "WAREHOUSE_IN_USE"),
        ("departments", department["id"], "DEPARTMENT_IN_USE"),
        ("projects", project["id"], "PROJECT_IN_USE"),
        ("manufacturers", manufacturer["id"], "MANUFACTURER_IN_USE"),
        ("materials", material["id"], "MATERIAL_IN_USE"),
    ):
        response = client.delete(f"/api/v1/{path}/{entity_id}", headers=_csrf(identity))
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == expected

    assert (
        client.post(
            f"/api/v1/warehouses/{warehouse['id']}/disable", headers=_csrf(identity)
        ).status_code
        == 200
    )
