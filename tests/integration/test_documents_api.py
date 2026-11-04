"""单据草稿、出入库提交、权限与软删除 API 集成测试。

依据 docs/16-验收标准.md 13.3、docs/06-库存计算与单据业务流程.md 7.2–7.5。
"""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MasterDataFixture
from tests.support import (
    OPERATOR_PASSWORD,
    change_password,
    create_document,
    csrf,
    inbound_payload,
    line,
    login,
    operator_session,
    outbound_payload,
    patch_document,
    submit_document,
)


def _inventory(
    client: TestClient,
    identity: dict[str, Any],
    master: MasterDataFixture,
    *,
    include_zero: bool = False,
) -> dict[str, Any]:
    response = client.get(
        "/api/v1/inventory",
        params={
            "warehouse_id": str(master.warehouse.id),
            "include_zero": str(include_zero).lower(),
            "page_size": 100,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _balance(
    client: TestClient,
    identity: dict[str, Any],
    master: MasterDataFixture,
    material_id: str,
) -> str:
    response = client.get(
        "/api/v1/inventory",
        params={
            "warehouse_id": str(master.warehouse.id),
            "material_id": material_id,
            "include_zero": "true",
        },
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    return str(items[0]["balance"])


def test_create_draft_does_not_change_stock_and_replays_request_id(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    request_id = str(uuid4())
    payload = inbound_payload(
        master_data,
        request_id=request_id,
        lines=[line(master_data, quantity="1.250", remark="采购入库")],
    )

    response = client.post("/api/v1/documents", json=payload, headers=csrf(identity))
    assert response.status_code == 201, response.text
    assert response.headers["X-Request-ID"] == request_id
    created = response.json()

    assert created["status"] == "DRAFT"
    assert created["doc_no"].startswith("RK")
    assert created["version"] == 1
    assert created["request_id"] == request_id
    assert created["created_source"] == "API"
    assert created["lines"][0]["quantity"] == "1.250"
    assert created["lines"][0]["manufacturer_code"] == master_data.manufacturer.code
    assert _inventory(client, identity, master_data)["total"] == 0

    replay = create_document(client, identity, payload, expected=200)
    assert replay["id"] == created["id"]
    assert replay["doc_no"] == created["doc_no"]
    assert replay["version"] == 1

    conflict_payload = dict(payload)
    conflict_payload["lines"] = [line(master_data, quantity="1.250", remark="不同内容")]
    response = client.post("/api/v1/documents", json=conflict_payload, headers=csrf(identity))
    assert response.status_code == 409, response.text
    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["error"]["code"] == "REQUEST_ID_CONFLICT"
    assert response.json()["error"]["request_id"] == request_id
    assert response.json()["error"]["details"]["resource"] == "document"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", str(uuid4())),
        ("remark", "单据头备注"),
        ("unknown_field", "x"),
    ],
)
def test_document_header_rejects_project_remark_and_unknown_fields(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    field: str,
    value: Any,
) -> None:
    client, identity = logged_in_admin
    payload = inbound_payload(master_data, lines=[line(master_data)])
    payload[field] = value

    response = client.post("/api/v1/documents", json=payload, headers=csrf(identity))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_line_remark_is_stored_per_line_and_trimmed(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    created = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[
                line(master_data, remark="  第一行备注  "),
                line(master_data, material=master_data.material_second, remark=""),
            ],
        ),
    )

    assert created["lines"][0]["remark"] == "第一行备注"
    assert created["lines"][1]["remark"] is None


def test_line_remark_over_500_characters_is_rejected(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    payload = inbound_payload(
        master_data,
        lines=[line(master_data, remark="备" * 501)],
    )

    response = client.post("/api/v1/documents", json=payload, headers=csrf(identity))

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "lines.0.remark" in error["details"]["items"][0]["field"]


def test_outbound_line_with_manufacturer_is_rejected(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    payload = outbound_payload(master_data, lines=[line(master_data)])
    payload["lines"][0]["manufacturer_id"] = str(master_data.manufacturer.id)

    response = client.post("/api/v1/documents", json=payload, headers=csrf(identity))

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["items"][0]["line_no"] == 1


def test_inactive_project_blocks_save_and_submit_with_line_numbers(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    created = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[
                line(master_data, project=master_data.project),
                line(master_data, material=master_data.material_second, project=None),
            ],
        ),
    )
    disabled = client.post(
        f"/api/v1/projects/{master_data.project.id}/disable",
        headers=csrf(identity),
    )
    assert disabled.status_code == 200, disabled.text

    update = client.patch(
        f"/api/v1/documents/{created['id']}",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert update.status_code == 409, update.text
    assert update.json()["error"]["code"] == "PROJECT_INACTIVE"
    assert update.json()["error"]["details"]["items"][0]["line_no"] == 1

    submit = client.post(
        f"/api/v1/documents/{created['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert submit.status_code == 409, submit.text
    assert submit.json()["error"]["code"] == "PROJECT_INACTIVE"

    detail = client.get(f"/api/v1/documents/{created['id']}").json()
    assert detail["status"] == "DRAFT"
    assert detail["lines"][0]["project_name"] == master_data.project.name

    enabled = client.post(
        f"/api/v1/projects/{master_data.project.id}/enable",
        headers=csrf(identity),
    )
    assert enabled.status_code == 200, enabled.text
    submit_document(client, identity, created["id"], version=1)


def test_inactive_master_data_rules_for_inbound_and_outbound(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin

    enabled_inbound = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="3")]),
    )
    submit_document(client, identity, enabled_inbound["id"], version=1)

    material_disable = client.post(
        f"/api/v1/materials/{master_data.material.id}/disable",
        headers=csrf(identity),
    )
    assert material_disable.status_code == 200, material_disable.text

    inactive_material = client.post(
        "/api/v1/documents",
        json=inbound_payload(master_data, lines=[line(master_data)]),
        headers=csrf(identity),
    )
    assert inactive_material.status_code == 409, inactive_material.text
    assert inactive_material.json()["error"]["code"] == "MATERIAL_INACTIVE"

    outbound = create_document(
        client, identity, outbound_payload(master_data, lines=[line(master_data, quantity="1")])
    )
    submit_document(client, identity, outbound["id"], version=1)
    assert _balance(client, identity, master_data, master_data.material.id) == "2.000"

    manufacturer_disable = client.post(
        f"/api/v1/manufacturers/{master_data.manufacturer.id}/disable",
        headers=csrf(identity),
    )
    assert manufacturer_disable.status_code == 200
    inactive_manufacturer = client.post(
        "/api/v1/documents",
        json=inbound_payload(master_data, lines=[line(master_data)]),
        headers=csrf(identity),
    )
    assert inactive_manufacturer.status_code == 409
    assert inactive_manufacturer.json()["error"]["code"] == "MANUFACTURER_INACTIVE"

    warehouse_disable = client.post(
        f"/api/v1/warehouses/{master_data.warehouse_second.id}/disable",
        headers=csrf(identity),
    )
    assert warehouse_disable.status_code == 200, warehouse_disable.text
    inactive_warehouse = client.post(
        "/api/v1/documents",
        json=inbound_payload(
            master_data,
            warehouse=master_data.warehouse_second,
            lines=[line(master_data)],
        ),
        headers=csrf(identity),
    )
    assert inactive_warehouse.status_code == 409
    assert inactive_warehouse.json()["error"]["code"] == "WAREHOUSE_INACTIVE"

    department_disable = client.post(
        f"/api/v1/departments/{master_data.department.id}/disable",
        headers=csrf(identity),
    )
    assert department_disable.status_code == 200
    inactive_department = client.post(
        "/api/v1/documents",
        json=inbound_payload(master_data, lines=[line(master_data)]),
        headers=csrf(identity),
    )
    assert inactive_department.status_code == 409
    assert inactive_department.json()["error"]["code"] == "DEPARTMENT_INACTIVE"

    stock_warehouse_disable = client.post(
        f"/api/v1/warehouses/{master_data.warehouse.id}/disable",
        headers=csrf(identity),
    )
    assert stock_warehouse_disable.status_code == 409
    assert stock_warehouse_disable.json()["error"]["code"] == "WAREHOUSE_HAS_STOCK"


def test_inbound_submit_increases_stock_and_freezes_document(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    created = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[
                line(master_data, quantity="1.250", remark="多行入库"),
                line(master_data, material=master_data.material_second, quantity="2"),
            ],
        ),
    )

    posted = submit_document(client, identity, created["id"], version=1)

    assert posted["status"] == "POSTED"
    assert posted["version"] == 2
    assert posted["submitted_at"] is not None
    assert posted["submitted_by_name"] == identity["display_name"]
    assert _balance(client, identity, master_data, master_data.material.id) == "1.250"
    assert _balance(client, identity, master_data, master_data.material_second.id) == "2.000"

    audit = client.get(
        "/api/v1/audit-logs",
        params={
            "entity_type": "document",
            "entity_id": created["id"],
            "page_size": 100,
        },
    ).json()
    by_action = {item["action"]: item for item in audit["items"]}
    assert by_action["DOCUMENT_CREATED"]["request_id"] == created["request_id"]
    assert by_action["DOCUMENT_SUBMITTED"]["request_id"] is not None
    assert len(by_action["DOCUMENT_SUBMITTED"]["request_id"]) == 36

    transactions = client.get(
        "/api/v1/inventory/transactions",
        params={"warehouse_id": str(master_data.warehouse.id)},
    ).json()
    assert transactions["total"] == 2
    assert all(item["direction"] == "INBOUND" for item in transactions["items"])
    assert {item["quantity"] for item in transactions["items"]} == {"1.250", "2.000"}

    modify = client.patch(
        f"/api/v1/documents/{created['id']}",
        json={"version": 2},
        headers=csrf(identity),
    )
    assert modify.status_code == 409
    assert modify.json()["error"]["code"] == "DOCUMENT_NOT_DRAFT"

    duplicate = client.post(
        f"/api/v1/documents/{created['id']}/submit",
        json={"version": 2},
        headers=csrf(identity),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DOCUMENT_ALREADY_POSTED"

    delete = client.request(
        "DELETE",
        f"/api/v1/documents/{created['id']}",
        json={"version": 2},
        headers=csrf(identity),
    )
    assert delete.status_code == 409
    assert delete.json()["error"]["code"] == "DOCUMENT_NOT_DRAFT"


def test_outbound_submit_rejects_insufficient_stock_without_partial_changes(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    inbound = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[
                line(master_data, material=master_data.material, quantity="5"),
                line(master_data, material=master_data.material_second, quantity="3"),
            ],
        ),
    )
    submit_document(client, identity, inbound["id"], version=1)

    short = create_document(
        client,
        identity,
        outbound_payload(
            master_data,
            lines=[
                line(master_data, material=master_data.material, quantity="5"),
                line(master_data, material=master_data.material_second, quantity="4"),
            ],
        ),
    )
    response = client.post(
        f"/api/v1/documents/{short['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "INSUFFICIENT_STOCK"
    assert error["details"]["resource"] == "inventory"
    item = error["details"]["items"][0]
    assert item["code"] == master_data.material_second.code
    assert item["available"] == "3.000"
    assert item["requested"] == "4.000"

    assert _balance(client, identity, master_data, master_data.material.id) == "5.000"
    assert _balance(client, identity, master_data, master_data.material_second.id) == "3.000"
    detail = client.get(f"/api/v1/documents/{short['id']}").json()
    assert detail["status"] == "DRAFT"

    exact = create_document(
        client,
        identity,
        outbound_payload(
            master_data,
            lines=[line(master_data, material=master_data.material, quantity="5")],
        ),
    )
    submit_document(client, identity, exact["id"], version=1)
    assert _balance(client, identity, master_data, master_data.material.id) == "0.000"

    tiny = create_document(
        client,
        identity,
        outbound_payload(
            master_data,
            lines=[line(master_data, material=master_data.material, quantity="0.001")],
        ),
    )
    overdraw = client.post(
        f"/api/v1/documents/{tiny['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert overdraw.status_code == 409
    assert overdraw.json()["error"]["code"] == "INSUFFICIENT_STOCK"


def test_integer_material_rejects_fraction_and_accepts_integer(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    invalid = client.post(
        "/api/v1/documents",
        json=inbound_payload(
            master_data,
            lines=[
                line(
                    master_data,
                    material=master_data.material_integer,
                    quantity="1.5",
                )
            ],
        ),
        headers=csrf(identity),
    )
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"

    valid = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[
                line(
                    master_data,
                    material=master_data.material_integer,
                    quantity="2",
                )
            ],
        ),
    )
    submit_document(client, identity, valid["id"], version=1)
    assert _balance(client, identity, master_data, master_data.material_integer.id) == "2.000"


def test_balance_limit_exceeded_rolls_back_whole_document(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    projects = [master_data.project, master_data.project_second]
    for index in range(7):
        response = client.post(
            "/api/v1/projects",
            json={"name": f"边界项目 {index}"},
            headers=csrf(identity),
        )
        assert response.status_code == 201, response.text
        projects.append(SimpleNamespace(id=response.json()["id"]))

    maximum = "999999999999999.999"
    boundary = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[
                line(master_data, quantity=maximum, project=project) for project in projects[:9]
            ],
        ),
    )
    submit_document(client, identity, boundary["id"], version=1)
    assert (
        _balance(client, identity, master_data, master_data.material.id) == "8999999999999999.991"
    )

    over = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[line(master_data, quantity="0.010")],
        ),
    )
    response = client.post(
        f"/api/v1/documents/{over['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "BALANCE_LIMIT_EXCEEDED"
    assert error["details"]["items"][0]["limit"] == "9000000000000000.000"
    assert (
        _balance(client, identity, master_data, master_data.material.id) == "8999999999999999.991"
    )


def test_draft_update_delete_version_and_operator_permissions(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    admin_app: Any,
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    created = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
    )

    stale = client.patch(
        f"/api/v1/documents/{created['id']}",
        json={"version": 2},
        headers=csrf(identity),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    updated = patch_document(
        client,
        identity,
        created["id"],
        {
            "version": 1,
            "lines": [
                line(
                    master_data,
                    quantity="2.500",
                    project=master_data.project,
                    remark="  更新备注  ",
                ),
                line(master_data, material=master_data.material_second, quantity="1"),
            ],
        },
    )
    assert updated["version"] == 2
    assert updated["lines"][0]["remark"] == "更新备注"
    assert len(updated["lines"]) == 2
    assert updated["projects"][0]["project_id"] == str(master_data.project.id)

    stale_delete = client.request(
        "DELETE",
        f"/api/v1/documents/{created['id']}",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert stale_delete.status_code == 409
    assert stale_delete.json()["error"]["code"] == "VERSION_CONFLICT"

    with operator_session(admin_app, client, identity, login_name="op-perm") as (
        operator_client,
        operator_identity,
    ):
        forbidden = operator_client.patch(
            f"/api/v1/documents/{created['id']}",
            json={"version": 2},
            headers=csrf(operator_identity),
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "FORBIDDEN"

        forbidden_delete = operator_client.request(
            "DELETE",
            f"/api/v1/documents/{created['id']}",
            json={"version": 2},
            headers=csrf(operator_identity),
        )
        assert forbidden_delete.status_code == 403

        # 操作员可提交任意普通草稿。
        posted = submit_document(operator_client, operator_identity, created["id"], version=2)
        assert posted["status"] == "POSTED"

        own = create_document(
            operator_client,
            operator_identity,
            inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
        )
        own_updated = patch_document(
            operator_client,
            operator_identity,
            own["id"],
            {"version": 1},
        )
        assert own_updated["version"] == 2
        own_delete = operator_client.request(
            "DELETE",
            f"/api/v1/documents/{own['id']}",
            json={"version": 2},
            headers=csrf(operator_identity),
        )
        assert own_delete.status_code == 204
        assert operator_client.get(f"/api/v1/documents/{own['id']}").status_code == 404
        assert operator_client.get("/api/v1/inventory").status_code == 200
        assert operator_client.get("/api/v1/documents").status_code == 200
        assert operator_client.get("/api/v1/inventory/transactions").status_code == 200

        own_posted = create_document(
            operator_client,
            operator_identity,
            inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
        )
        submit_document(operator_client, operator_identity, own_posted["id"], version=1)
        own_soft_delete = operator_client.post(
            f"/api/v1/documents/{own_posted['id']}/delete",
            headers=csrf(operator_identity),
        )
        assert own_soft_delete.status_code == 200, own_soft_delete.text
        assert own_soft_delete.json()["deleted"] is True


def test_request_id_is_scoped_to_creator(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    admin_app: Any,
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    request_id = str(uuid4())
    payload = inbound_payload(
        master_data, request_id=request_id, lines=[line(master_data, quantity="2")]
    )
    created = create_document(client, identity, payload)

    with operator_session(admin_app, client, identity, login_name="op-replay") as (
        operator_client,
        operator_identity,
    ):
        # request_id 与创建者组成唯一约束，其他用户使用相同值不会命中该幂等键。
        other = create_document(operator_client, operator_identity, payload, expected=201)
        assert other["id"] != created["id"]
        assert other["request_id"] == request_id
        replay = create_document(operator_client, operator_identity, payload, expected=200)
        assert replay["id"] == other["id"]


def test_soft_delete_removes_only_posted_documents_from_stock(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    admin_app: Any,
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    inbound = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="10")]),
    )
    submit_document(client, identity, inbound["id"], version=1)
    outbound = create_document(
        client,
        identity,
        outbound_payload(master_data, lines=[line(master_data, quantity="3")]),
    )
    submit_document(client, identity, outbound["id"], version=1)
    assert _balance(client, identity, master_data, master_data.material.id) == "7.000"

    negative = client.post(
        f"/api/v1/documents/{inbound['id']}/delete",
        headers=csrf(identity),
    )
    assert negative.status_code == 409, negative.text
    assert negative.json()["error"]["code"] == "NEGATIVE_STOCK"
    assert client.get(f"/api/v1/documents/{inbound['id']}").json()["deleted"] is False

    deleted_outbound = client.post(
        f"/api/v1/documents/{outbound['id']}/delete",
        headers=csrf(identity),
    )
    assert deleted_outbound.status_code == 200, deleted_outbound.text
    body = deleted_outbound.json()
    assert body["deleted"] is True
    assert body["deleted_at"] is not None
    assert body["lines"]
    assert _balance(client, identity, master_data, master_data.material.id) == "10.000"

    repeated = client.post(
        f"/api/v1/documents/{outbound['id']}/delete",
        headers=csrf(identity),
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "DOCUMENT_ALREADY_DELETED"

    deleted_inbound = client.post(
        f"/api/v1/documents/{inbound['id']}/delete",
        headers=csrf(identity),
    )
    assert deleted_inbound.status_code == 200, deleted_inbound.text
    assert _balance(client, identity, master_data, master_data.material.id) == "0.000"
    assert _inventory(client, identity, master_data)["total"] == 0

    draft = create_document(
        client, identity, inbound_payload(master_data, lines=[line(master_data)])
    )
    not_posted = client.post(
        f"/api/v1/documents/{draft['id']}/delete",
        headers=csrf(identity),
    )
    assert not_posted.status_code == 409
    assert not_posted.json()["error"]["code"] == "DOCUMENT_NOT_POSTED"

    with operator_session(admin_app, client, identity, login_name="op-delete") as (
        operator_client,
        operator_identity,
    ):
        # 管理员创建的单据，操作员不能软删除。
        foreign = create_document(
            client,
            identity,
            inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
        )
        submit_document(client, identity, foreign["id"], version=1)
        forbidden = operator_client.post(
            f"/api/v1/documents/{foreign['id']}/delete",
            headers=csrf(operator_identity),
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "FORBIDDEN"


def test_unauthenticated_requests_are_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/documents").status_code == 401
    assert client.get("/api/v1/inventory").status_code == 401


def test_document_write_requests_require_csrf(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, _identity = logged_in_admin
    payload = inbound_payload(master_data, lines=[line(master_data)])

    response = client.post("/api/v1/documents", json=payload)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_password_change_required_blocks_business_api(
    admin_client: TestClient,
    master_data: MasterDataFixture,
) -> None:
    identity = login(admin_client, "admin", "initial-password-123")
    assert identity["force_password_change"] is True
    response = admin_client.post(
        "/api/v1/documents",
        json=inbound_payload(master_data, lines=[line(master_data)]),
        headers=csrf(identity),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    changed = change_password(admin_client, identity, "initial-password-123", OPERATOR_PASSWORD)
    assert changed["force_password_change"] is False
