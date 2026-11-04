"""单据列表、详情、库存与流水查询测试。

依据 docs/11-单据与库存业务API.md 8.5、docs/06-库存计算与单据业务流程.md 7.1、
docs/16-验收标准.md ACC-FLOW-011、ACC-FLOW-013。
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from tests.conftest import MasterDataFixture
from tests.support import (
    create_document,
    csrf,
    inbound_payload,
    line,
    outbound_payload,
    submit_document,
)


def _create_inbound(
    client: TestClient,
    identity: dict[str, Any],
    master: MasterDataFixture,
    *,
    lines: list[dict[str, Any]],
    warehouse: Any = None,
) -> dict[str, Any]:
    return create_document(
        client,
        identity,
        inbound_payload(master, lines=lines, warehouse=warehouse),
    )


def test_document_list_filters_projects_and_detail(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    settings: Settings,
) -> None:
    client, identity = logged_in_admin
    first = _create_inbound(
        client,
        identity,
        master_data,
        lines=[
            line(master_data, quantity="2", project=master_data.project),
            line(
                master_data,
                material=master_data.material_second,
                quantity="1",
                project=None,
            ),
        ],
    )
    submit_document(client, identity, first["id"], version=1)

    second = _create_inbound(
        client,
        identity,
        master_data,
        lines=[line(master_data, quantity="3", project=master_data.project_second)],
        warehouse=master_data.warehouse_second,
    )
    submit_document(client, identity, second["id"], version=1)

    outbound = create_document(
        client,
        identity,
        outbound_payload(
            master_data,
            lines=[
                line(master_data, quantity="1", project=master_data.project),
                line(master_data, quantity="0.5", project=None),
            ],
        ),
    )
    submit_document(client, identity, outbound["id"], version=1)

    all_documents = client.get("/api/v1/documents", params={"page_size": 100}).json()
    assert all_documents["total"] == 3
    summary = next(item for item in all_documents["items"] if item["id"] == first["id"])
    assert "lines" not in summary
    assert {item["project_id"] for item in summary["projects"]} == {str(master_data.project.id)}
    assert all(item["project_name"] for item in summary["projects"])

    inbound = client.get(
        "/api/v1/documents", params={"doc_type": "INBOUND", "page_size": 100}
    ).json()
    assert inbound["total"] == 2
    outbound_only = client.get(
        "/api/v1/documents", params={"doc_type": "OUTBOUND", "page_size": 100}
    ).json()
    assert outbound_only["total"] == 1

    assert client.get("/api/v1/documents", params={"status": "DRAFT"}).json()["total"] == 0
    assert client.get("/api/v1/documents", params={"status": "POSTED"}).json()["total"] == 3
    assert (
        client.get(
            "/api/v1/documents",
            params={"warehouse_id": str(master_data.warehouse.id)},
        ).json()["total"]
        == 2
    )
    assert (
        client.get(
            "/api/v1/documents",
            params={"department_id": str(master_data.department.id)},
        ).json()["total"]
        == 3
    )
    by_project = client.get(
        "/api/v1/documents",
        params={"project_id": str(master_data.project.id), "page_size": 100},
    ).json()
    assert {item["id"] for item in by_project["items"]} == {first["id"], outbound["id"]}

    by_manufacturer = client.get(
        "/api/v1/documents",
        params={"manufacturer_id": str(master_data.manufacturer.id), "page_size": 100},
    ).json()
    assert {item["id"] for item in by_manufacturer["items"]} == {first["id"], second["id"]}

    by_doc_no = client.get("/api/v1/documents", params={"doc_no": first["doc_no"]}).json()
    assert by_doc_no["total"] == 1
    assert by_doc_no["items"][0]["id"] == first["id"]

    today = datetime.now(settings.timezone).date()
    filtered = client.get(
        "/api/v1/documents",
        params={
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
            "page_size": 100,
        },
    ).json()
    assert filtered["total"] == 3
    future = client.get(
        "/api/v1/documents",
        params={"start_date": (today + timedelta(days=2)).isoformat()},
    ).json()
    assert future["total"] == 0

    detail = client.get(f"/api/v1/documents/{first['id']}").json()
    assert detail["doc_no"] == first["doc_no"]
    assert [item["line_no"] for item in detail["lines"]] == [1, 2]
    assert detail["lines"][0]["project_id"] == str(master_data.project.id)
    assert detail["lines"][0]["project_name"] == master_data.project.name
    assert detail["lines"][0]["category_name"] == master_data.category_child.name
    assert detail["lines"][0]["parent_category_name"] == master_data.category.name
    assert detail["lines"][1]["project_id"] is None
    assert detail["lines"][1]["manufacturer_name"] == master_data.manufacturer.name
    assert detail["request_id"]

    deleted = client.post(f"/api/v1/documents/{outbound['id']}/delete", headers=csrf(identity))
    assert deleted.status_code == 200
    assert client.get("/api/v1/documents", params={"deleted": "true"}).json()["total"] == 1
    assert client.get("/api/v1/documents", params={"deleted": "false"}).json()["total"] == 2
    deleted_detail = client.get(f"/api/v1/documents/{outbound['id']}").json()
    assert deleted_detail["deleted"] is True
    assert deleted_detail["lines"]


def test_inventory_zero_fill_and_filters(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin

    empty = client.get(
        "/api/v1/inventory",
        params={"warehouse_id": str(master_data.warehouse.id)},
    ).json()
    assert empty["total"] == 0
    zero_filled = client.get(
        "/api/v1/inventory",
        params={
            "warehouse_id": str(master_data.warehouse.id),
            "include_zero": "true",
            "page_size": 100,
        },
    ).json()
    assert zero_filled["total"] == 3
    assert all(item["balance"] == "0.000" for item in zero_filled["items"])

    first = _create_inbound(
        client,
        identity,
        master_data,
        lines=[line(master_data, quantity="1.250")],
    )
    submit_document(client, identity, first["id"], version=1)
    second = _create_inbound(
        client,
        identity,
        master_data,
        lines=[line(master_data, material=master_data.material_second, quantity="2")],
        warehouse=master_data.warehouse_second,
    )
    submit_document(client, identity, second["id"], version=1)

    all_stock = client.get("/api/v1/inventory", params={"page_size": 100}).json()
    assert all_stock["total"] == 2
    by_code = client.get("/api/v1/inventory", params={"code": master_data.material.code}).json()
    assert by_code["total"] == 1
    assert by_code["items"][0]["code"] == master_data.material.code
    assert by_code["items"][0]["balance"] == "1.250"
    by_name = client.get(
        "/api/v1/inventory", params={"name": master_data.material_second.name}
    ).json()
    assert by_name["total"] == 1
    by_material = client.get(
        "/api/v1/inventory",
        params={"material_id": str(master_data.material.id)},
    ).json()
    assert by_material["total"] == 1
    assert by_material["items"][0]["warehouse_name"] == master_data.warehouse.name
    assert by_material["items"][0]["unit"] == master_data.material.unit
    assert by_material["items"][0]["quantity_type"] == "DECIMAL"

    missing = client.get("/api/v1/inventory", params={"code": "WZ-99999"}).json()
    assert missing["total"] == 0


def test_inventory_transactions_per_line_filters_and_soft_delete(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    settings: Settings,
) -> None:
    client, identity = logged_in_admin
    inbound = _create_inbound(
        client,
        identity,
        master_data,
        lines=[
            line(master_data, quantity="2", project=master_data.project),
            line(master_data, quantity="3", project=None),
        ],
    )
    submit_document(client, identity, inbound["id"], version=1)
    outbound = create_document(
        client,
        identity,
        outbound_payload(
            master_data,
            lines=[
                line(master_data, quantity="1", project=master_data.project),
                line(master_data, quantity="0.5", project=None),
            ],
        ),
    )
    submit_document(client, identity, outbound["id"], version=1)

    rows = client.get(
        "/api/v1/inventory/transactions",
        params={"warehouse_id": str(master_data.warehouse.id), "page_size": 100},
    ).json()
    assert rows["total"] == 4
    inbound_rows = [row for row in rows["items"] if row["doc_no"] == inbound["doc_no"]]
    assert {row["quantity"] for row in inbound_rows} == {"2.000", "3.000"}
    project_row = next(
        row for row in inbound_rows if row["project_id"] == str(master_data.project.id)
    )
    assert project_row["project_name"] == master_data.project.name
    assert project_row["direction"] == "INBOUND"

    by_project = client.get(
        "/api/v1/inventory/transactions",
        params={"project_id": str(master_data.project.id), "page_size": 100},
    ).json()
    assert by_project["total"] == 2
    assert {row["quantity"] for row in by_project["items"]} == {"2.000", "1.000"}

    by_material = client.get(
        "/api/v1/inventory/transactions",
        params={"material_id": str(master_data.material.id), "page_size": 100},
    ).json()
    assert by_material["total"] == 4
    by_department = client.get(
        "/api/v1/inventory/transactions",
        params={"department_id": str(master_data.department.id), "page_size": 100},
    ).json()
    assert by_department["total"] == 4
    future = client.get(
        "/api/v1/inventory/transactions",
        params={
            "start_date": (datetime.now(settings.timezone).date() + timedelta(days=2)).isoformat()
        },
    ).json()
    assert future["total"] == 0

    deleted = client.post(f"/api/v1/documents/{outbound['id']}/delete", headers=csrf(identity))
    assert deleted.status_code == 200
    after = client.get(
        "/api/v1/inventory/transactions",
        params={"warehouse_id": str(master_data.warehouse.id), "page_size": 100},
    ).json()
    assert after["total"] == 2
    assert all(row["doc_no"] == inbound["doc_no"] for row in after["items"])


def test_query_plans_use_inventory_and_document_indexes(
    session_factory: sessionmaker[Session],
) -> None:
    queries = {
        "view_warehouse": (
            "SELECT material_id, balance_milli FROM v_inventory_balance "
            "WHERE warehouse_id = '00000000-0000-0000-0000-000000000000'",
            ("ix_stock_documents_status_warehouse", "ix_stock_document_lines_document_id"),
        ),
        "view_material": (
            "SELECT material_id, balance_milli FROM v_inventory_balance "
            "WHERE material_id = '00000000-0000-0000-0000-000000000000'",
            ("ix_stock_document_lines_material_id",),
        ),
        "lines_document": (
            "SELECT id FROM stock_document_lines WHERE document_id = 'x'",
            ("ix_stock_document_lines_document_id",),
        ),
        "lines_material": (
            "SELECT id FROM stock_document_lines WHERE material_id = 'x'",
            ("ix_stock_document_lines_material_id",),
        ),
        "lines_project": (
            "SELECT id FROM stock_document_lines WHERE project_id = 'x'",
            ("ix_stock_document_lines_project_id",),
        ),
        "documents_status_warehouse": (
            "SELECT id FROM stock_documents WHERE status = 'POSTED' "
            "AND warehouse_id = 'x' AND deleted_at IS NULL",
            ("ix_stock_documents_status_warehouse",),
        ),
    }
    with session_factory() as session:
        for name, (query, expected_indexes) in queries.items():
            plan = [
                str(row[-1]) for row in session.execute(text(f"EXPLAIN QUERY PLAN {query}")).all()
            ]
            joined = "\n".join(plan)
            assert any("SEARCH" in line for line in plan), (name, joined)
            for index in expected_indexes:
                assert index in joined, (name, index, joined)
            assert "SCAN stock_document_lines" not in joined, (name, joined)
