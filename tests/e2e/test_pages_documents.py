"""单据页面：草稿、提交、库存不足定位、软删除与 XLSX 快速录入。"""

import re
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.documents.models import StockDocument
from tests.conftest import MasterDataFixture
from tests.page_support import csrf_for
from tests.xlsx_support import filled_workbook, upload_files


def _new_document(
    client: TestClient,
    master: MasterDataFixture,
    *,
    doc_type: str,
    material_id: str,
    quantity: str,
    action: str,
    manufacturer_id: str | None = None,
    remark: str = "",
) -> Any:
    page = client.get(f"/documents/new?doc_type={doc_type}")
    assert page.status_code == 200
    request_id = re.search(r'name="request_id" value="([^"]+)"', page.text)
    assert request_id
    data: dict[str, Any] = {
        "_csrf": csrf_for(client, f"/documents/new?doc_type={doc_type}"),
        "doc_type": doc_type,
        "request_id": request_id.group(1),
        "warehouse_id": str(master.warehouse.id),
        "department_id": str(master.department.id),
        "line_material_id": [material_id],
        "line_quantity": [quantity],
        "line_project_id": [""],
        "line_remark": [remark],
        "action": action,
    }
    if doc_type == "INBOUND":
        data["line_manufacturer_id"] = [manufacturer_id or str(master.manufacturer.id)]
    return client.post("/documents/new", data=data, follow_redirects=False)


def _document_id(html: str) -> str:
    match = re.search(r"/documents/([0-9a-f-]{36})/(?:submit|delete|discard)", html)
    assert match, "未找到单据操作链接"
    return match.group(1)


def test_draft_submit_stock_shortage_and_soft_delete(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, _ = logged_in_admin

    draft = _new_document(
        client,
        master_data,
        doc_type="INBOUND",
        material_id=str(master_data.material.id),
        quantity="5.000",
        action="save",
    )
    assert draft.status_code == 303
    draft_detail = client.get(draft.headers["location"])
    assert "不计入库存" in draft_detail.text
    assert "草稿" in draft_detail.text
    assert "下载入库单" in draft_detail.text
    draft_id = _document_id(draft_detail.text)
    assert "XLSX 快速录入" not in client.get(f"/documents/{draft_id}/edit").text

    submit = client.post(
        f"/documents/{draft_id}/submit",
        data={
            "_csrf": csrf_for(client, "/documents"),
            "version": re.search(r'name="version" value="(\d+)"', draft_detail.text).group(1),
        },
        follow_redirects=False,
    )
    assert submit.status_code == 303
    assert "已生效" in client.get(submit.headers["location"]).text

    shortage = _new_document(
        client,
        master_data,
        doc_type="OUTBOUND",
        material_id=str(master_data.material.id),
        quantity="99.000",
        action="submit",
    )
    assert shortage.status_code == 409
    assert "草稿已创建，但提交未成功" in shortage.text
    assert "库存不足" in shortage.text
    assert "第 1 行" in shortage.text
    shortage_id = _document_id(shortage.text)
    version = re.search(r'name="version" value="(\d+)"', shortage.text).group(1)

    reloaded = client.get(f"/documents/{shortage_id}")
    assert reloaded.status_code == 200
    assert "不计入库存" in reloaded.text
    retried = client.post(
        f"/documents/{shortage_id}/submit",
        data={"_csrf": csrf_for(client, "/documents"), "version": version},
        follow_redirects=False,
    )
    assert retried.status_code == 409
    assert "库存不足" in retried.text

    inbound = _new_document(
        client,
        master_data,
        doc_type="INBOUND",
        material_id=str(master_data.material.id),
        quantity="100.000",
        action="submit",
    )
    assert inbound.status_code == 303
    retried = client.post(
        f"/documents/{shortage_id}/submit",
        data={
            "_csrf": csrf_for(client, "/documents"),
            "version": version,
        },
        follow_redirects=False,
    )
    assert retried.status_code == 303
    outbound_detail = client.get(retried.headers["location"])
    assert "已生效" in outbound_detail.text
    assert "下载出库单" in outbound_detail.text

    inventory = client.get("/inventory")
    assert "螺纹钢" in inventory.text
    assert "6.000 千克" in inventory.text

    outbound_page = client.get("/documents/new?doc_type=OUTBOUND")
    assert 'hx-get="/partials/inventory-balance"' in outbound_page.text
    balance = client.get(
        "/partials/inventory-balance",
        params={
            "warehouse_id": str(master_data.warehouse.id),
            "line_material_id": str(master_data.material.id),
        },
    )
    assert balance.status_code == 200
    assert "6.000" in balance.text

    deleted = client.post(
        f"/documents/{shortage_id}/delete",
        data={"_csrf": csrf_for(client, "/documents")},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert "已软删除" in client.get(deleted.headers["location"]).text
    inventory_after_delete = client.get("/inventory")
    assert "105.000 千克" in inventory_after_delete.text


def test_xlsx_parse_fills_form_without_creating_documents(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, _ = logged_in_admin

    def count_documents() -> int:
        with session_factory() as session:
            return int(session.scalar(select(func.count()).select_from(StockDocument)) or 0)

    page = client.get("/documents/new?doc_type=INBOUND")
    assert 'formaction="/documents/parse"' in page.text
    assert "data-confirm-parse" in page.text
    csrf = csrf_for(client, "/documents/new?doc_type=INBOUND")
    request_id = re.search(r'name="request_id" value="([^"]+)"', page.text).group(1)
    base_data = {
        "_csrf": csrf,
        "doc_type": "INBOUND",
        "request_id": request_id,
        "warehouse_id": str(master_data.warehouse.id),
        "department_id": str(master_data.department.id),
        "line_material_id": [""],
        "line_quantity": [""],
        "line_remark": ["保留备注"],
        "action": "save",
    }

    before = count_documents()
    workbook = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[["", "原材料", "钢材", "螺纹钢", "1.250", "钢厂", "XLSX 备注"]],
    )
    parsed = client.post(
        "/documents/parse",
        data=base_data,
        files=upload_files(workbook),
        follow_redirects=False,
    )
    assert parsed.status_code == 200, parsed.text
    assert "全部填入" in parsed.text
    assert "螺纹钢" in parsed.text
    assert "1.250" in parsed.text
    assert "XLSX 备注" in parsed.text
    assert count_documents() == before

    partial_workbook = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            ["", "原材料", "钢材", "螺纹钢", "1.000", "钢厂", "有效行"],
            ["", "原材料", "钢材", "不存在的物资", "1.000", "钢厂", "无效行"],
        ],
    )
    partial = client.post(
        "/documents/parse",
        data=base_data,
        files=upload_files(partial_workbook),
        follow_redirects=False,
    )
    assert partial.status_code == 200, partial.text
    assert "部分填入" in partial.text
    assert "MATERIAL_NOT_FOUND" in partial.text
    assert "有效行" in partial.text
    assert count_documents() == before

    failure = client.post(
        "/documents/parse",
        data=base_data,
        files=upload_files(b"not-a-zip"),
        follow_redirects=False,
    )
    assert failure.status_code == 422
    assert "XLSX_INVALID_FILE" in failure.text
    assert "保留备注" in failure.text
    assert count_documents() == before
