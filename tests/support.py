"""HTTP 集成测试辅助函数。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from tests.conftest import MasterDataFixture

OPERATOR_PASSWORD = "operator-password-123"
OPERATOR_NEW_PASSWORD = "operator-password-456"


def csrf(identity: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": str(identity["csrf_token"])}


def create_document(
    client: TestClient,
    identity: dict[str, Any],
    payload: dict[str, Any],
    *,
    expected: int = 201,
) -> dict[str, Any]:
    response = client.post("/api/v1/documents", json=payload, headers=csrf(identity))
    assert response.status_code == expected, response.text
    return response.json()


def submit_document(
    client: TestClient,
    identity: dict[str, Any],
    document_id: str,
    version: int,
    *,
    expected: int = 200,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/documents/{document_id}/submit",
        json={"version": version},
        headers=csrf(identity),
    )
    assert response.status_code == expected, response.text
    return response.json()


def patch_document(
    client: TestClient,
    identity: dict[str, Any],
    document_id: str,
    payload: dict[str, Any],
    *,
    expected: int = 200,
) -> dict[str, Any]:
    response = client.patch(
        f"/api/v1/documents/{document_id}", json=payload, headers=csrf(identity)
    )
    assert response.status_code == expected, response.text
    return response.json()


def line(
    master: MasterDataFixture,
    *,
    material: Any = None,
    quantity: str = "1.250",
    manufacturer: Any = None,
    project: Any = None,
    include_manufacturer: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "material_id": str((material or master.material).id),
        "quantity": quantity,
    }
    if include_manufacturer:
        payload["manufacturer_id"] = str((manufacturer or master.manufacturer).id)
    if project is not None:
        payload["project_id"] = str(project.id)
    return payload


def inbound_payload(
    master: MasterDataFixture,
    *,
    lines: list[dict[str, Any]],
    request_id: str | None = None,
    warehouse: Any = None,
    department: Any = None,
    remark: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id or str(uuid4()),
        "doc_type": "INBOUND",
        "warehouse_id": str((warehouse or master.warehouse).id),
        "department_id": str((department or master.department).id),
        "lines": lines,
    }
    if remark is not None:
        payload["remark"] = remark
    return payload


def outbound_payload(
    master: MasterDataFixture,
    *,
    lines: list[dict[str, Any]],
    request_id: str | None = None,
    warehouse: Any = None,
    department: Any = None,
    remark: str | None = None,
) -> dict[str, Any]:
    payload = inbound_payload(
        master,
        lines=lines,
        request_id=request_id,
        warehouse=warehouse,
        department=department,
        remark=remark,
    )
    payload["doc_type"] = "OUTBOUND"
    for item in payload["lines"]:
        item.pop("manufacturer_id", None)
    return payload


def create_operator(
    admin_client: TestClient,
    admin_identity: dict[str, Any],
    *,
    login_name: str = "operator",
) -> dict[str, Any]:
    response = admin_client.post(
        "/api/v1/users",
        json={
            "login_name": login_name,
            "display_name": login_name,
            "role": "OPERATOR",
            "initial_password": OPERATOR_PASSWORD,
        },
        headers=csrf(admin_identity),
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(client: TestClient, login_name: str, password: str) -> dict[str, Any]:
    response = client.post("/auth/login", json={"login_name": login_name, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def change_password(
    client: TestClient,
    identity: dict[str, Any],
    old_password: str,
    new_password: str,
) -> dict[str, Any]:
    response = client.post(
        "/auth/change-password",
        json={"old_password": old_password, "new_password": new_password},
        headers=csrf(identity),
    )
    assert response.status_code == 200, response.text
    return response.json()


@contextmanager
def operator_session(
    admin_app: Any,
    admin_client: TestClient,
    admin_identity: dict[str, Any],
    *,
    login_name: str = "operator",
) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    """创建操作员并完成首次改密，返回独立客户端与身份。"""
    create_operator(admin_client, admin_identity, login_name=login_name)
    client = TestClient(admin_app)
    try:
        identity = login(client, login_name, OPERATOR_PASSWORD)
        identity = change_password(client, identity, OPERATOR_PASSWORD, OPERATOR_NEW_PASSWORD)
        yield client, identity
    finally:
        client.close()
