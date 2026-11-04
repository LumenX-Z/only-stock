"""页面权限回归：操作员对他人单据、管理页面与 API Key 的边界。"""

import re
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import MasterDataFixture
from tests.page_support import csrf_for, post_page
from tests.support import create_document, inbound_payload, line, operator_session, submit_document


def _new_document_data(
    client: TestClient,
    master: MasterDataFixture,
    *,
    quantity: str = "1.000",
) -> dict[str, Any]:
    page = client.get("/documents/new?doc_type=INBOUND")
    request_id = re.search(r'name="request_id" value="([^"]+)"', page.text).group(1)
    return {
        "_csrf": csrf_for(client, "/documents/new?doc_type=INBOUND"),
        "doc_type": "INBOUND",
        "request_id": request_id,
        "warehouse_id": str(master.warehouse.id),
        "department_id": str(master.department.id),
        "line_material_id": [str(master.material.id)],
        "line_quantity": [quantity],
        "line_manufacturer_id": [str(master.manufacturer.id)],
        "line_project_id": [""],
        "line_remark": [""],
        "action": "save",
    }


def test_operator_cannot_edit_or_delete_others_documents(
    admin_app: Any,
    admin_client: TestClient,
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    _, identity = logged_in_admin
    admin_draft = create_document(
        admin_client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="1.000")]),
    )
    admin_posted = create_document(
        admin_client,
        identity,
        inbound_payload(master_data, lines=[line(master_data, quantity="2.000")]),
    )
    submit_document(admin_client, identity, admin_posted["id"], admin_posted["version"])

    with operator_session(admin_app, admin_client, identity, login_name="doc-op") as (
        operator,
        _,
    ):
        edit_other = operator.get(f"/documents/{admin_draft['id']}/edit")
        assert edit_other.status_code == 403
        assert "只能编辑本人创建的草稿" in edit_other.text

        discard_other = post_page(
            operator,
            f"/documents/{admin_draft['id']}/discard",
            {"version": str(admin_draft["version"])},
            csrf_page="/documents",
        )
        assert discard_other.status_code == 303
        after_discard = operator.get(f"/documents/{admin_draft['id']}")
        assert "删除草稿失败" in after_discard.text
        assert after_discard.status_code == 200

        delete_other = post_page(
            operator,
            f"/documents/{admin_posted['id']}/delete",
            {},
            csrf_page="/documents",
        )
        assert delete_other.status_code == 403
        assert "无权" in delete_other.text or "只能" in delete_other.text

        own = post_page(
            operator,
            "/documents/new",
            _new_document_data(operator, master_data),
        )
        assert own.status_code == 303
        own_id = own.headers["location"].split("/")[-1]
        own_edit = operator.get(f"/documents/{own_id}/edit")
        assert own_edit.status_code == 200
        own_discard = post_page(
            operator,
            f"/documents/{own_id}/discard",
            {"version": "1"},
            csrf_page=f"/documents/{own_id}/edit",
        )
        assert own_discard.status_code == 303


def test_api_key_list_is_scoped_to_current_user(
    admin_app: Any,
    admin_client: TestClient,
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin
    created = post_page(client, "/api-keys", {"name": "管理员专用 Key"}, csrf_page="/api-keys")
    assert created.status_code == 303
    shown = client.get(created.headers["location"])
    secret = re.search(r'id="new-api-key">([^<]+)<', shown.text).group(1)
    suffix = secret[-4:]

    with operator_session(admin_app, admin_client, logged_in_admin[1], login_name="key-op") as (
        operator,
        _,
    ):
        page = operator.get("/api-keys")
        assert page.status_code == 200
        assert "管理员专用 Key" not in page.text
        assert suffix not in page.text
