"""已生效单据软删除、基础资料停用启用与余额边界集成测试。

依据 docs/06-库存计算与单据业务流程.md 7.5–7.7、docs/05-数据模型与实体约束.md
6.6、6.7 与 docs/16-验收标准.md 13.2、13.3：
- 软删除保留单据头、明细与快照，同一事务写审计，重复删除不重复写入；
- 项目与仓库、部门、厂家、分类、物资停用后不可用于新建或提交，分类和物资只影响入库；
- 提交与软删除共用余额校验，单行/多行最大数量不溢出，超限整单回滚。
"""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.audit.models import AuditLog
from app.modules.inventory.service import current_balances
from tests.conftest import MasterDataFixture
from tests.support import (
    create_document,
    csrf,
    inbound_payload,
    line,
    outbound_payload,
    submit_document,
)

MAXIMUM_QUANTITY = "999999999999999.999"


def _balance(
    client: TestClient,
    master: MasterDataFixture,
    *,
    material: Any = None,
) -> str:
    response = client.get(
        "/api/v1/inventory",
        params={
            "warehouse_id": str(master.warehouse.id),
            "material_id": str((material or master.material).id),
            "include_zero": "true",
        },
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    return str(items[0]["balance"])


def _audit_count(
    session_factory: sessionmaker[Session],
    entity_id: str,
    action: str,
) -> int:
    with session_factory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == entity_id, AuditLog.action == action)
            )
            or 0
        )


def _set_active(
    client: TestClient,
    identity: dict[str, Any],
    resource: str,
    entity_id: str,
    *,
    active: bool,
) -> Any:
    action = "enable" if active else "disable"
    return client.post(
        f"/api/v1/{resource}/{entity_id}/{action}",
        headers=csrf(identity),
    )


def _insert_legacy_posted_inbound(
    session_factory: sessionmaker[Session],
    source_document_id: str,
    *,
    quantity_milli: int,
) -> str:
    """直接写入一张历史遗留的已生效入库单。

    正常服务路径不可能产生超过 ``MAX_BALANCE_MILLI`` 的余额，此夹具仅用于验证
    docs/16 ACC-FLOW-007 要求的防御性上限校验；写入的仍是完整单据头与明细。
    """
    document_id = str(uuid4())
    line_id = str(uuid4())
    doc_no = f"RK19990101-{uuid4().hex[:6]}"
    request_id = str(uuid4())
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO stock_documents (
                    id, doc_no, doc_type, status, warehouse_id, warehouse_code,
                    warehouse_name, department_id, department_code, department_name,
                    created_source, request_id, request_fingerprint, version, created_by,
                    created_by_name, submitted_by, submitted_by_name, created_at, updated_at,
                    submitted_at, deleted_at, deleted_by
                )
                SELECT
                    :document_id, :doc_no, doc_type, status, warehouse_id,
                    warehouse_code, warehouse_name, department_id, department_code,
                    department_name, created_source, :request_id, request_fingerprint,
                    version, created_by, created_by_name, submitted_by, submitted_by_name,
                    created_at, updated_at, submitted_at, deleted_at, deleted_by
                FROM stock_documents
                WHERE id = :source_id
                """
            ),
            {
                "document_id": document_id,
                "doc_no": doc_no,
                "request_id": request_id,
                "source_id": source_document_id,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO stock_document_lines (
                    id, document_id, line_no, material_id, material_code, material_name,
                    unit, quantity_type, category_code, category_name,
                    parent_category_code, parent_category_name, manufacturer_id,
                    manufacturer_code, manufacturer_name, project_id, project_code,
                    project_name, quantity_milli
                )
                SELECT
                    :line_id, :document_id, 1, material_id, material_code, material_name,
                    unit, quantity_type, category_code, category_name,
                    parent_category_code, parent_category_name, manufacturer_id,
                    manufacturer_code, manufacturer_name, NULL, NULL, NULL, :quantity_milli
                FROM stock_document_lines
                WHERE document_id = :source_id AND line_no = 1
                """
            ),
            {
                "line_id": line_id,
                "document_id": document_id,
                "quantity_milli": quantity_milli,
                "source_id": source_document_id,
            },
        )
        session.commit()
    return document_id


def test_soft_delete_keeps_snapshots_and_writes_audit_once(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    created = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[line(master_data, quantity="10", project=master_data.project)],
        ),
    )
    submit_document(client, identity, created["id"], version=1)
    before = client.get(f"/api/v1/documents/{created['id']}").json()
    assert before["deleted"] is False
    assert before["lines"][0]["project_id"] == str(master_data.project.id)

    response = client.post(
        f"/api/v1/documents/{created['id']}/delete",
        headers=csrf(identity),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] is True
    assert body["deleted_at"] is not None
    assert body["deleted_by"] == identity["id"]

    after = client.get(f"/api/v1/documents/{created['id']}").json()
    assert after["deleted"] is True
    assert after["lines"] == before["lines"]
    assert after["projects"] == before["projects"]
    assert _balance(client, master_data) == "0.000"
    assert _audit_count(session_factory, created["id"], "DOCUMENT_SOFT_DELETED") == 1

    repeated = client.post(
        f"/api/v1/documents/{created['id']}/delete",
        headers=csrf(identity),
    )
    assert repeated.status_code == 409, repeated.text
    assert repeated.json()["error"]["code"] == "DOCUMENT_ALREADY_DELETED"
    assert _audit_count(session_factory, created["id"], "DOCUMENT_SOFT_DELETED") == 1


def test_failed_soft_delete_keeps_document_and_writes_no_audit(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
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
    assert _balance(client, master_data) == "7.000"

    response = client.post(
        f"/api/v1/documents/{inbound['id']}/delete",
        headers=csrf(identity),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "NEGATIVE_STOCK"

    detail = client.get(f"/api/v1/documents/{inbound['id']}").json()
    assert detail["deleted"] is False
    assert detail["deleted_at"] is None
    assert detail["deleted_by"] is None
    assert _balance(client, master_data) == "7.000"
    assert _audit_count(session_factory, inbound["id"], "DOCUMENT_SOFT_DELETED") == 0


def test_project_disable_blocks_save_and_submit_and_enable_restores(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    draft = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[line(master_data, quantity="2", project=master_data.project)],
        ),
    )

    disabled = _set_active(client, identity, "projects", str(master_data.project.id), active=False)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["is_active"] is False

    blocked_save = client.patch(
        f"/api/v1/documents/{draft['id']}",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert blocked_save.status_code == 409, blocked_save.text
    error = blocked_save.json()["error"]
    assert error["code"] == "PROJECT_INACTIVE"
    assert error["details"]["resource"] == "project"
    assert error["details"]["items"][0]["line_no"] == 1
    assert error["details"]["items"][0]["code"] == master_data.project.code
    assert error["details"]["items"][0]["name"] == master_data.project.name

    blocked_submit = client.post(
        f"/api/v1/documents/{draft['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert blocked_submit.status_code == 409, blocked_submit.text
    assert blocked_submit.json()["error"]["code"] == "PROJECT_INACTIVE"

    blocked_create = client.post(
        "/api/v1/documents",
        json=inbound_payload(
            master_data,
            lines=[line(master_data, quantity="1", project=master_data.project)],
        ),
        headers=csrf(identity),
    )
    assert blocked_create.status_code == 409, blocked_create.text
    assert blocked_create.json()["error"]["code"] == "PROJECT_INACTIVE"

    listed = client.get("/api/v1/projects", params={"page_size": 100}).json()
    assert str(master_data.project.id) not in {item["id"] for item in listed["items"]}
    listed_all = client.get(
        "/api/v1/projects",
        params={"page_size": 100, "include_inactive": "true"},
    ).json()
    assert str(master_data.project.id) in {item["id"] for item in listed_all["items"]}

    enabled = _set_active(client, identity, "projects", str(master_data.project.id), active=True)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["is_active"] is True

    saved = client.patch(
        f"/api/v1/documents/{draft['id']}",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert saved.status_code == 200, saved.text
    posted = submit_document(client, identity, draft["id"], version=2)
    assert posted["status"] == "POSTED"
    assert posted["lines"][0]["project_id"] == str(master_data.project.id)
    assert posted["lines"][0]["project_name"] == master_data.project.name
    assert _balance(client, master_data) == "2.000"

    # 提交后再次停用项目，历史单据项目和名称快照保持不变。
    assert (
        _set_active(
            client, identity, "projects", str(master_data.project.id), active=False
        ).status_code
        == 200
    )
    historical = client.get(f"/api/v1/documents/{draft['id']}").json()
    assert historical["lines"][0]["project_id"] == str(master_data.project.id)
    assert historical["lines"][0]["project_name"] == master_data.project.name
    assert historical["status"] == "POSTED"
    assert _balance(client, master_data) == "2.000"
    assert _audit_count(session_factory, draft["id"], "DOCUMENT_SUBMITTED") == 1


def test_inactive_material_blocks_inbound_but_outbound_uses_stock(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    seeded = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="10")]),
    )
    submit_document(client, identity, seeded["id"], version=1)
    pending = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
    )

    disabled = _set_active(
        client, identity, "materials", str(master_data.material.id), active=False
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["is_active"] is False

    blocked_new = client.post(
        "/api/v1/documents",
        json=inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
        headers=csrf(identity),
    )
    assert blocked_new.status_code == 409, blocked_new.text
    error = blocked_new.json()["error"]
    assert error["code"] == "MATERIAL_INACTIVE"
    assert error["details"]["items"][0]["line_no"] == 1
    assert error["details"]["items"][0]["code"] == master_data.material.code

    blocked_submit = client.post(
        f"/api/v1/documents/{pending['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert blocked_submit.status_code == 409, blocked_submit.text
    assert blocked_submit.json()["error"]["code"] == "MATERIAL_INACTIVE"

    # 出库不因物资停用而拒绝，仍按库存判定。
    outbound = create_document(
        client,
        identity,
        outbound_payload(master_data, lines=[line(master_data, quantity="4")]),
    )
    submit_document(client, identity, outbound["id"], version=1)
    assert _balance(client, master_data) == "6.000"

    enabled = _set_active(client, identity, "materials", str(master_data.material.id), active=True)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["is_active"] is True
    # 启用只改状态，不触发库存重算。
    assert _balance(client, master_data) == "6.000"

    restored = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
    )
    submit_document(client, identity, restored["id"], version=1)
    assert _balance(client, master_data) == "7.000"
    historical = client.get(f"/api/v1/documents/{seeded['id']}").json()
    assert historical["lines"][0]["material_name"] == master_data.material.name
    assert _audit_count(session_factory, seeded["id"], "DOCUMENT_SUBMITTED") == 1
    assert _audit_count(session_factory, str(master_data.material.id), "MATERIAL_DISABLED") == 1
    assert _audit_count(session_factory, str(master_data.material.id), "MATERIAL_ENABLED") == 1


def test_inactive_warehouse_and_department_block_new_and_submit(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin

    warehouse_draft = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            warehouse=master_data.warehouse_second,
            lines=[line(master_data, quantity="3")],
        ),
    )
    disabled_warehouse = _set_active(
        client,
        identity,
        "warehouses",
        str(master_data.warehouse_second.id),
        active=False,
    )
    assert disabled_warehouse.status_code == 200, disabled_warehouse.text

    blocked_new = client.post(
        "/api/v1/documents",
        json=inbound_payload(
            master_data,
            warehouse=master_data.warehouse_second,
            lines=[line(master_data, quantity="1")],
        ),
        headers=csrf(identity),
    )
    assert blocked_new.status_code == 409, blocked_new.text
    assert blocked_new.json()["error"]["code"] == "WAREHOUSE_INACTIVE"

    blocked_submit = client.post(
        f"/api/v1/documents/{warehouse_draft['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert blocked_submit.status_code == 409, blocked_submit.text
    assert blocked_submit.json()["error"]["code"] == "WAREHOUSE_INACTIVE"

    enabled_warehouse = _set_active(
        client,
        identity,
        "warehouses",
        str(master_data.warehouse_second.id),
        active=True,
    )
    assert enabled_warehouse.status_code == 200, enabled_warehouse.text
    submit_document(client, identity, warehouse_draft["id"], version=1)
    with_stock = _set_active(
        client,
        identity,
        "warehouses",
        str(master_data.warehouse_second.id),
        active=False,
    )
    assert with_stock.status_code == 409, with_stock.text
    assert with_stock.json()["error"]["code"] == "WAREHOUSE_HAS_STOCK"

    department_draft = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
    )
    disabled_department = _set_active(
        client, identity, "departments", str(master_data.department.id), active=False
    )
    assert disabled_department.status_code == 200, disabled_department.text

    blocked_new = client.post(
        "/api/v1/documents",
        json=inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
        headers=csrf(identity),
    )
    assert blocked_new.status_code == 409, blocked_new.text
    assert blocked_new.json()["error"]["code"] == "DEPARTMENT_INACTIVE"

    blocked_submit = client.post(
        f"/api/v1/documents/{department_draft['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert blocked_submit.status_code == 409, blocked_submit.text
    assert blocked_submit.json()["error"]["code"] == "DEPARTMENT_INACTIVE"

    enabled_department = _set_active(
        client, identity, "departments", str(master_data.department.id), active=True
    )
    assert enabled_department.status_code == 200, enabled_department.text
    submit_document(client, identity, department_draft["id"], version=1)


def test_inactive_manufacturer_blocks_inbound_and_enable_restores(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    draft = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
    )

    disabled = _set_active(
        client, identity, "manufacturers", str(master_data.manufacturer.id), active=False
    )
    assert disabled.status_code == 200, disabled.text

    blocked_new = client.post(
        "/api/v1/documents",
        json=inbound_payload(master_data, lines=[line(master_data, quantity="1")]),
        headers=csrf(identity),
    )
    assert blocked_new.status_code == 409, blocked_new.text
    assert blocked_new.json()["error"]["code"] == "MANUFACTURER_INACTIVE"

    blocked_submit = client.post(
        f"/api/v1/documents/{draft['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert blocked_submit.status_code == 409, blocked_submit.text
    assert blocked_submit.json()["error"]["code"] == "MANUFACTURER_INACTIVE"

    enabled = _set_active(
        client, identity, "manufacturers", str(master_data.manufacturer.id), active=True
    )
    assert enabled.status_code == 200, enabled.text
    submit_document(client, identity, draft["id"], version=1)
    assert _balance(client, master_data) == "1.000"
    historical = client.get(f"/api/v1/documents/{draft['id']}").json()
    assert historical["lines"][0]["manufacturer_name"] == master_data.manufacturer.name
    assert (
        _audit_count(session_factory, str(master_data.manufacturer.id), "MANUFACTURER_DISABLED")
        == 1
    )


def test_inactive_category_blocks_inbound_but_not_outbound(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    """分类停用只影响入库；出库仍按库存判定。

    正常管理接口要求先停用分类下全部物资，因此这里直接用数据库构造“分类已停用、
    物资仍启用”的历史遗留状态，验证服务层对分类状态的独立防御校验。
    """
    client, identity = logged_in_admin
    seeded = create_document(
        client,
        identity,
        inbound_payload(
            master_data,
            lines=[line(master_data, material=master_data.material_second, quantity="5")],
        ),
    )
    submit_document(client, identity, seeded["id"], version=1)

    with session_factory() as session:
        session.execute(
            text("UPDATE categories SET is_active = 0 WHERE id = :category_id"),
            {"category_id": master_data.category.id},
        )
        session.commit()

    blocked = client.post(
        "/api/v1/documents",
        json=inbound_payload(
            master_data,
            lines=[line(master_data, material=master_data.material_second, quantity="1")],
        ),
        headers=csrf(identity),
    )
    assert blocked.status_code == 409, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "CATEGORY_INACTIVE"
    assert error["details"]["items"][0]["line_no"] == 1
    assert error["details"]["items"][0]["code"] == master_data.category.code

    outbound = create_document(
        client,
        identity,
        outbound_payload(
            master_data,
            lines=[line(master_data, material=master_data.material_second, quantity="2")],
        ),
    )
    submit_document(client, identity, outbound["id"], version=1)
    with session_factory() as session:
        balances = current_balances(
            session, master_data.warehouse.id, [master_data.material_second.id]
        )
    assert balances[master_data.material_second.id] == 3000


def test_balance_limit_boundaries_and_multiline_overflow(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    projects = [master_data.project, master_data.project_second]
    for index in range(8):
        response = client.post(
            "/api/v1/projects",
            json={"name": f"P5 边界项目 {index}"},
            headers=csrf(identity),
        )
        assert response.status_code == 201, response.text
        projects.append(SimpleNamespace(id=response.json()["id"]))

    exact_lines = [
        line(master_data, quantity=MAXIMUM_QUANTITY, project=project) for project in projects[:9]
    ]
    exact_lines.append(line(master_data, quantity="0.009", project=projects[9]))
    exact = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=exact_lines),
    )
    submit_document(client, identity, exact["id"], version=1)
    assert _balance(client, master_data) == "9000000000000000.000"

    over = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="0.001")]),
    )
    over_response = client.post(
        f"/api/v1/documents/{over['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert over_response.status_code == 409, over_response.text
    assert over_response.json()["error"]["code"] == "BALANCE_LIMIT_EXCEEDED"

    # 10 行合法单行最大数量的合计超过上限，但仍低于 Python 整数范围；
    # 必须整单拒绝且不发生 SQLite 整数溢出或部分写入。
    overflow_lines = [
        line(master_data, quantity=MAXIMUM_QUANTITY, project=project) for project in projects
    ]
    overflow = create_document(
        client,
        identity,
        inbound_payload(master_data, lines=overflow_lines),
    )
    overflow_response = client.post(
        f"/api/v1/documents/{overflow['id']}/submit",
        json={"version": 1},
        headers=csrf(identity),
    )
    assert overflow_response.status_code == 409, overflow_response.text
    assert overflow_response.json()["error"]["code"] == "BALANCE_LIMIT_EXCEEDED"

    assert _balance(client, master_data) == "9000000000000000.000"
    for document in (over, overflow):
        detail = client.get(f"/api/v1/documents/{document['id']}").json()
        assert detail["status"] == "DRAFT"
        assert detail["version"] == 1
        assert _audit_count(session_factory, document["id"], "DOCUMENT_SUBMITTED") == 0
    assert _audit_count(session_factory, exact["id"], "DOCUMENT_SUBMITTED") == 1


def test_soft_delete_rejects_removal_that_would_exceed_balance_limit(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
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
        outbound_payload(master_data, lines=[line(master_data, quantity="1")]),
    )
    submit_document(client, identity, outbound["id"], version=1)
    assert _balance(client, master_data) == "9.000"

    # 历史遗留的已生效入库单使当前余额超过上限；删除出库单会继续增加余额。
    _insert_legacy_posted_inbound(
        session_factory,
        inbound["id"],
        quantity_milli=9_000_000_000_000_000_000,
    )

    response = client.post(
        f"/api/v1/documents/{outbound['id']}/delete",
        headers=csrf(identity),
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "BALANCE_LIMIT_EXCEEDED"
    assert error["details"]["items"][0]["limit"] == "9000000000000000.000"

    detail = client.get(f"/api/v1/documents/{outbound['id']}").json()
    assert detail["deleted"] is False
    assert detail["deleted_at"] is None
    assert detail["deleted_by"] is None
    assert _balance(client, master_data) == "9000000000000009.000"
    assert _audit_count(session_factory, outbound["id"], "DOCUMENT_SOFT_DELETED") == 0
