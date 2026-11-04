"""页面端到端主路径：建用户、建资料、入库、出库、停用/启用、软删除与 API Key。"""

import re
from typing import Any

from fastapi.testclient import TestClient

from tests.page_support import create_masterdata, csrf_for, post_page


def _id(html: str, resource: str, name: str) -> str:
    pattern = rf"/masterdata/{resource}/([0-9a-f-]{{36}})/(?:disable|delete)"
    for match in re.finditer(pattern, html):
        if name in html[max(0, match.start() - 700) : match.start()]:
            return match.group(1)
    raise AssertionError(f"未找到 {resource} {name}")


def _create_document(
    client: TestClient,
    *,
    doc_type: str,
    warehouse_id: str,
    department_id: str,
    material_id: str,
    quantity: str,
    manufacturer_id: str | None,
    action: str,
) -> Any:
    page = client.get(f"/documents/new?doc_type={doc_type}")
    request_id = re.search(r'name="request_id" value="([^"]+)"', page.text).group(1)
    data: dict[str, Any] = {
        "_csrf": csrf_for(client, f"/documents/new?doc_type={doc_type}"),
        "doc_type": doc_type,
        "request_id": request_id,
        "warehouse_id": warehouse_id,
        "department_id": department_id,
        "line_material_id": [material_id],
        "line_quantity": [quantity],
        "line_project_id": [""],
        "line_remark": [""],
        "action": action,
    }
    if doc_type == "INBOUND":
        assert manufacturer_id
        data["line_manufacturer_id"] = [manufacturer_id]
    return client.post("/documents/new", data=data, follow_redirects=False)


def test_full_page_main_path(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin

    user = post_page(
        client,
        "/users",
        {
            "login_name": "main-path-op",
            "display_name": "主路径操作员",
            "role": "OPERATOR",
            "initial_password": "initial-password-123",
        },
        csrf_page="/users",
    )
    assert user.status_code == 303

    assert create_masterdata(client, "warehouses", {"name": "主路径仓库"}).status_code == 303
    assert create_masterdata(client, "departments", {"name": "主路径部门"}).status_code == 303
    assert create_masterdata(client, "manufacturers", {"name": "主路径厂家"}).status_code == 303
    assert create_masterdata(client, "projects", {"name": "主路径项目"}).status_code == 303
    assert create_masterdata(client, "categories", {"name": "主路径分类"}).status_code == 303

    categories = client.get("/masterdata/categories")
    category_id = _id(categories.text, "categories", "主路径分类")
    assert (
        create_masterdata(
            client,
            "materials",
            {
                "name": "主路径物资",
                "category_id": category_id,
                "unit": "个",
                "quantity_type": "DECIMAL",
            },
        ).status_code
        == 303
    )

    warehouses = client.get("/masterdata/warehouses")
    warehouse_id = _id(warehouses.text, "warehouses", "主路径仓库")
    departments = client.get("/masterdata/departments")
    department_id = _id(departments.text, "departments", "主路径部门")
    manufacturers = client.get("/masterdata/manufacturers")
    manufacturer_id = _id(manufacturers.text, "manufacturers", "主路径厂家")
    materials = client.get("/masterdata/materials")
    material_id = _id(materials.text, "materials", "主路径物资")
    projects = client.get("/masterdata/projects")
    project_id = _id(projects.text, "projects", "主路径项目")

    inbound = _create_document(
        client,
        doc_type="INBOUND",
        warehouse_id=warehouse_id,
        department_id=department_id,
        material_id=material_id,
        quantity="10.000",
        manufacturer_id=manufacturer_id,
        action="submit",
    )
    assert inbound.status_code == 303
    inbound_id = inbound.headers["location"].split("/")[-1]
    assert "已生效" in client.get(f"/documents/{inbound_id}").text

    outbound = _create_document(
        client,
        doc_type="OUTBOUND",
        warehouse_id=warehouse_id,
        department_id=department_id,
        material_id=material_id,
        quantity="4.000",
        manufacturer_id=None,
        action="submit",
    )
    assert outbound.status_code == 303
    outbound_id = outbound.headers["location"].split("/")[-1]
    inventory = client.get("/inventory")
    assert "6.000" in inventory.text

    disable = post_page(
        client,
        f"/masterdata/projects/{project_id}/disable",
        {},
        csrf_page="/masterdata/projects",
    )
    assert disable.status_code == 303
    assert "已停用" in client.get("/masterdata/projects").text
    enable = post_page(
        client,
        f"/masterdata/projects/{project_id}/enable",
        {},
        csrf_page="/masterdata/projects",
    )
    assert enable.status_code == 303
    assert "已启用" in client.get("/masterdata/projects").text

    deleted = post_page(
        client,
        f"/documents/{outbound_id}/delete",
        {},
        csrf_page="/documents",
    )
    assert deleted.status_code == 303
    assert "已软删除" in client.get(f"/documents/{outbound_id}").text
    assert "10.000" in client.get("/inventory").text

    key = post_page(client, "/api-keys", {"name": "主路径 Key"}, csrf_page="/api-keys")
    assert key.status_code == 303
    key_page = client.get(key.headers["location"])
    assert "API Key 创建成功" in key_page.text
    assert re.search(r'id="new-api-key">osk_[A-Za-z0-9_-]+<', key_page.text)

    assert identity["role"] == "COMPANY_ADMIN"
