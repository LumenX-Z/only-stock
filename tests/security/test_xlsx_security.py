"""XLSX 模板、解析与导出安全测试。

依据 docs/16-验收标准.md 13.8、docs/14-安全、可靠性与性能要求.md 11.1：
- 5 MiB/2,000 行、损坏 ZIP、压缩炸弹、外部链接、宏、公式和表头区异常均被安全拒绝；
- 解析前后单据、审计和基础资料数量不变；
- 导出中的用户文本不会生成公式。
"""

from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.sqlite import begin_immediate
from app.modules.audit.models import AuditLog
from app.modules.categories.models import Category
from app.modules.documents.inputs import DocumentInput, DocumentLineInput
from app.modules.documents.models import StockDocument, StockDocumentLine
from app.modules.documents.service import create_draft, submit_document
from app.modules.masterdata.service import create_entity
from app.modules.materials.models import Material
from app.modules.xlsx.parser import parse_document_entry
from tests.conftest import MasterDataFixture
from tests.support import csrf
from tests.xlsx_support import (
    edit_workbook,
    filled_workbook,
    rewrite_zip,
    upload_files,
)


def _counts(session_factory: sessionmaker[Session]) -> dict[str, int]:
    with session_factory() as session:
        return {
            "documents": int(session.scalar(select(func.count()).select_from(StockDocument)) or 0),
            "lines": int(session.scalar(select(func.count()).select_from(StockDocumentLine)) or 0),
            "audit": int(session.scalar(select(func.count()).select_from(AuditLog)) or 0),
            "categories": int(session.scalar(select(func.count()).select_from(Category)) or 0),
            "materials": int(session.scalar(select(func.count()).select_from(Material)) or 0),
        }


def _parse(
    client: TestClient,
    identity: dict[str, Any],
    data: bytes,
    *,
    doc_type: str = "INBOUND",
    csrf_token: bool = True,
):
    headers = csrf(identity) if csrf_token else {}
    return client.post(
        "/api/v1/document-entry/parse",
        data={"doc_type": doc_type},
        files=upload_files(data),
        headers=headers,
    )


def _valid_rows(master: MasterDataFixture) -> list[list[Any]]:
    return [
        [
            master.project.name,
            master.category.name,
            master.category_child.name,
            master.material.name,
            "1.250",
            master.manufacturer.name,
            "采购批次 A",
        ]
    ]


def _valid_file(master: MasterDataFixture, doc_type: str = "INBOUND") -> bytes:
    if doc_type == "INBOUND":
        rows = _valid_rows(master)
    else:
        rows = [
            [
                master.project.name,
                master.category.name,
                master.category_child.name,
                master.material.name,
                "1.250",
                "出库备注",
            ]
        ]
    return filled_workbook(
        doc_type,
        warehouse_name=master.warehouse.name,
        department_name=master.department.name,
        rows=rows,
    )


def test_xlsx_endpoints_require_authentication(
    client: TestClient, master_data: MasterDataFixture
) -> None:
    data = _valid_file(master_data)
    response = client.post(
        "/api/v1/document-entry/parse",
        data={"doc_type": "INBOUND"},
        files=upload_files(data),
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert client.get("/api/v1/document-templates/INBOUND").status_code == 401
    assert client.get(f"/api/v1/documents/{uuid4()}/export").status_code == 401


def test_xlsx_parse_requires_csrf(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    response = _parse(client, identity, _valid_file(master_data), csrf_token=False)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_xlsx_parse_rejects_oversized_file(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    oversized = b"x" * (5 * 1024 * 1024 + 1)
    response = _parse(client, identity, oversized)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "XLSX_FILE_TOO_LARGE"


def test_xlsx_parse_rejects_corrupt_zip(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    response = _parse(client, identity, b"not a zip at all")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "XLSX_INVALID_FILE"


def test_xlsx_parse_rejects_zip_bomb(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    bomb = rewrite_zip(
        _valid_file(master_data),
        add={"xl/padding.bin": b"\x00" * (10 * 1024 * 1024)},
    )
    assert len(bomb) < 5 * 1024 * 1024
    response = _parse(client, identity, bomb)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "XLSX_ARCHIVE_UNSAFE"


def test_xlsx_parse_rejects_external_links(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    external = rewrite_zip(
        _valid_file(master_data),
        add={
            "xl/externalLinks/externalLink1.xml": b"<externalLink/>",
        },
    )
    response = _parse(client, identity, external)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "XLSX_ARCHIVE_UNSAFE"


def test_xlsx_parse_rejects_macros(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    macro = rewrite_zip(
        _valid_file(master_data),
        add={"xl/vbaProject.bin": b"macro"},
    )
    response = _parse(client, identity, macro)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "XLSX_ARCHIVE_UNSAFE"


def test_xlsx_parse_rejects_row_limit(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    row = [
        master_data.project.name,
        master_data.category.name,
        master_data.category_child.name,
        master_data.material.name,
        "1",
        master_data.manufacturer.name,
        None,
    ]
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[row for _ in range(2001)],
    )
    response = _parse(client, identity, data)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "XLSX_ROW_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda wb: setattr(wb["录入数据"]["A1"], "value", "错误标签"), "XLSX_HEADER_INVALID"),
        (lambda wb: setattr(wb["录入数据"]["B1"], "value", "=1+1"), "XLSX_HEADER_INVALID"),
        (lambda wb: setattr(wb["录入数据"]["C3"], "value", "错误列"), "XLSX_COLUMNS_INVALID"),
        (lambda wb: setattr(wb["录入数据"]["I3"], "value", "多余列"), "XLSX_COLUMNS_INVALID"),
    ],
)
def test_xlsx_parse_rejects_malformed_structure(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    mutation,
    expected_code: str,
) -> None:
    client, identity = logged_in_admin
    data = edit_workbook(_valid_file(master_data), mutation)
    response = _parse(client, identity, data)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == expected_code


def test_xlsx_parse_rejects_extra_sheet_and_wrong_version(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin

    def add_sheet(workbook) -> None:
        workbook.create_sheet("其他")

    extra = edit_workbook(_valid_file(master_data), add_sheet)
    response = _parse(client, identity, extra)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "XLSX_WORKSHEET_INVALID"

    wrong_version = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        version="INBOUND_V9",
        rows=_valid_rows(master_data),
    )
    response = _parse(client, identity, wrong_version)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "XLSX_TEMPLATE_UNSUPPORTED"


def test_xlsx_parse_has_no_side_effects(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    data = _valid_file(master_data)
    before = _counts(session_factory)
    first = _parse(client, identity, data)
    second = _parse(client, identity, data)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json() == second.json()
    assert _counts(session_factory) == before


def test_xlsx_parse_runs_without_write_lock(
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    data = _valid_file(master_data)
    path = tmp_path / "no-write-lock.xlsx"
    path.write_bytes(data)
    with session_factory() as holder, begin_immediate(holder):
        holder.execute(
            text("UPDATE warehouses SET version = version WHERE id = :id"),
            {"id": master_data.warehouse.id},
        )
        with session_factory() as reader:
            parsed = parse_document_entry(path, doc_type="INBOUND", session=reader)
    assert parsed.status == "SUCCESS"
    assert len(parsed.lines) == 1


def test_xlsx_parse_response_contract(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    response = _parse(client, identity, _valid_file(master_data))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "SUCCESS"
    assert payload["doc_type"] == "INBOUND"
    assert payload["total_rows"] == 1
    assert payload["filled_rows"] == 1
    assert payload["skipped_rows"] == 0
    assert payload["errors"] == []
    assert payload["document"]["warehouse_id"] == master_data.warehouse.id
    assert payload["document"]["department_id"] == master_data.department.id
    assert set(payload) == {
        "doc_type",
        "status",
        "notice",
        "document",
        "total_rows",
        "filled_rows",
        "skipped_rows",
        "errors",
    }
    assert "id" not in payload
    assert "document_id" not in payload
    assert "batch_id" not in payload
    assert "file_hash" not in payload
    line = payload["document"]["lines"][0]
    assert line["quantity"] == "1.250"
    assert line["remark"] == "采购批次 A"
    assert line["manufacturer_id"] == master_data.manufacturer.id


def test_xlsx_partial_response_exposes_errors(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    rows = _valid_rows(master_data) + [
        [
            None,
            master_data.category.name,
            None,
            "不存在的物资",
            "1",
            master_data.manufacturer.name,
            None,
        ]
    ]
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=rows,
    )
    response = _parse(client, identity, data)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "PARTIAL"
    assert payload["notice"] == "部分明细未填入，请核对后再保存或提交"
    assert payload["total_rows"] == 2
    assert payload["filled_rows"] == 1
    assert payload["skipped_rows"] == 1
    error = payload["errors"][0]
    assert error["source_row_no"] == 5
    assert error["error_field"] == "物资名称"
    assert error["error_code"] == "MATERIAL_NOT_FOUND"
    assert error["message"]


def test_xlsx_export_escapes_formula_text(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    project = create_entity(
        session_factory,
        "project",
        name="=项目公式",
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    category = create_entity(
        session_factory,
        "category",
        name="+分类公式",
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    material = create_entity(
        session_factory,
        "material",
        name="@物资公式",
        actor_user_id=master_data.actor.id,
        request_id=None,
        category_id=category.id,
        unit="个",
        quantity_type="DECIMAL",
    )
    manufacturer = create_entity(
        session_factory,
        "manufacturer",
        name="-厂家公式",
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    record = create_draft(
        session_factory,
        DocumentInput(
            doc_type="INBOUND",
            warehouse_id=master_data.warehouse.id,
            department_id=master_data.department.id,
            lines=(
                DocumentLineInput(
                    material_id=material.id,
                    quantity="1.250",
                    manufacturer_id=manufacturer.id,
                    project_id=project.id,
                    remark="=1+1",
                ),
            ),
        ),
        actor=master_data.actor,
        request_id=str(uuid4()),
        timezone=ZoneInfo("Asia/Shanghai"),
        source="API",
    )
    response = client.get(f"/api/v1/documents/{record.document.id}/export")
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook["录入数据"]
    assert workbook.sheetnames == ["录入数据", "填写说明", "单据信息"]
    assert sheet["B1"].value == master_data.warehouse.name
    assert sheet["B2"].value == master_data.department.name
    for coordinate, expected in (
        ("A4", "=项目公式"),
        ("B4", "+分类公式"),
        ("D4", "@物资公式"),
        ("E4", "1.250"),
        ("F4", "-厂家公式"),
        ("G4", "=1+1"),
    ):
        cell = sheet[coordinate]
        assert cell.value == expected
        assert cell.data_type == "s", (coordinate, cell.value, cell.data_type)


def test_xlsx_export_does_not_change_state(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    record = create_draft(
        session_factory,
        DocumentInput(
            doc_type="INBOUND",
            warehouse_id=master_data.warehouse.id,
            department_id=master_data.department.id,
            lines=(
                DocumentLineInput(
                    material_id=master_data.material.id,
                    quantity="1",
                    manufacturer_id=master_data.manufacturer.id,
                    remark="导出测试",
                ),
            ),
        ),
        actor=master_data.actor,
        request_id=str(uuid4()),
        timezone=ZoneInfo("Asia/Shanghai"),
        source="API",
    )
    submitted = submit_document(
        session_factory,
        record.document.id,
        version=1,
        actor=master_data.actor,
        source="API",
    )
    before = (submitted.document.status, submitted.document.version, submitted.document.deleted_at)
    response = client.get(f"/api/v1/documents/{record.document.id}/export")
    assert response.status_code == 200, response.text
    with session_factory() as session:
        document = session.get(StockDocument, record.document.id)
        assert document is not None
        assert (document.status, document.version, document.deleted_at) == before
