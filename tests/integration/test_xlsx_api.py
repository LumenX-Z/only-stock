"""XLSX 模板下载、解析与导出 API 集成测试。

依据 docs/11-单据与库存业务API.md 8.5、docs/07-XLSX模板、快速录入与导出.md 7.8.5
与 docs/16-验收标准.md ACC-XLSX-001、ACC-XLSX-007、ACC-XLSX-010、ACC-XLSX-012。
"""

from io import BytesIO
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy.orm import Session, sessionmaker

from app.modules.documents.inputs import DocumentInput, DocumentLineInput
from app.modules.documents.service import (
    create_draft,
    soft_delete_document,
    submit_document,
)
from app.modules.masterdata.service import create_entity
from app.modules.projects.models import Project
from tests.conftest import MasterDataFixture
from tests.support import csrf, operator_session
from tests.xlsx_support import XLSX_MIME, filled_workbook, upload_files


def _inbound_input(master: MasterDataFixture, *, remark: str | None = "导出备注") -> DocumentInput:
    return DocumentInput(
        doc_type="INBOUND",
        warehouse_id=master.warehouse.id,
        department_id=master.department.id,
        lines=(
            DocumentLineInput(
                material_id=master.material.id,
                quantity="1.250",
                manufacturer_id=master.manufacturer.id,
                project_id=master.project.id,
                remark=remark,
            ),
        ),
    )


def _create_document(
    session_factory: sessionmaker[Session],
    master: MasterDataFixture,
    *,
    data: DocumentInput | None = None,
):
    return create_draft(
        session_factory,
        data or _inbound_input(master),
        actor=master.actor,
        request_id=str(uuid4()),
        timezone=ZoneInfo("Asia/Shanghai"),
        source="API",
    )


def _export(client: TestClient, document_id: str) -> Any:
    response = client.get(f"/api/v1/documents/{document_id}/export")
    assert response.status_code == 200, response.text
    return response


def _sheet_values(workbook, sheet_name: str) -> dict[str, str]:
    sheet = workbook[sheet_name]
    return {
        str(row[0].value): str(row[1].value)
        for row in sheet.iter_rows(min_row=1, max_row=40, max_col=2)
        if row[0].value is not None
    }


def test_xlsx_template_download_both_roles(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    admin_app,
    master_data: MasterDataFixture,
) -> None:
    client, identity = logged_in_admin
    for doc_type, version in (("INBOUND", "INBOUND_V1"), ("OUTBOUND", "OUTBOUND_V1")):
        response = client.get(f"/api/v1/document-templates/{doc_type}")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == XLSX_MIME
        assert "attachment" in response.headers["content-disposition"]
        assert "filename*=UTF-8''" in response.headers["content-disposition"]
        workbook = load_workbook(BytesIO(response.content))
        assert workbook.sheetnames == ["录入数据", "填写说明"]
        help_values = _sheet_values(workbook, "填写说明")
        assert help_values["template_version"] == version

    with operator_session(admin_app, client, identity) as (operator, _operator_identity):
        response = operator.get("/api/v1/document-templates/INBOUND")
        assert response.status_code == 200, response.text

    assert client.get("/api/v1/document-templates/OTHER").status_code == 422


def test_xlsx_operator_can_parse_and_export(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    admin_app,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    record = _create_document(session_factory, master_data)
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [
                master_data.project.name,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "2",
                master_data.manufacturer.name,
                None,
            ]
        ],
    )
    with operator_session(admin_app, client, identity) as (operator, operator_identity):
        parsed = operator.post(
            "/api/v1/document-entry/parse",
            data={"doc_type": "INBOUND"},
            files=upload_files(data),
            headers=csrf(operator_identity),
        )
        assert parsed.status_code == 200, parsed.text
        assert parsed.json()["status"] == "SUCCESS"
        exported = operator.get(f"/api/v1/documents/{record.document.id}/export")
        assert exported.status_code == 200, exported.text


def test_xlsx_export_draft_uses_current_names_and_posted_uses_snapshot(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, _identity = logged_in_admin
    record = _create_document(session_factory, master_data)
    current_name = f"当前项目 {uuid4().hex[:8]}"
    with session_factory() as session:
        project = session.get(Project, master_data.project.id)
        assert project is not None
        project.name = current_name
        session.commit()

    draft_response = _export(client, record.document.id)
    draft_workbook = load_workbook(BytesIO(draft_response.content))
    assert draft_workbook["录入数据"]["A4"].value == current_name
    assert _sheet_values(draft_workbook, "单据信息")["涉及项目"] == current_name

    submitted = submit_document(
        session_factory,
        record.document.id,
        version=1,
        actor=master_data.actor,
        source="API",
    )
    assert submitted.document.status == "POSTED"
    posted_response = _export(client, record.document.id)
    posted_workbook = load_workbook(BytesIO(posted_response.content))
    assert posted_workbook["录入数据"]["A4"].value == master_data.project.name
    assert _sheet_values(posted_workbook, "单据信息")["涉及项目"] == master_data.project.name


def test_xlsx_export_soft_deleted_document_keeps_status(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, _identity = logged_in_admin
    record = _create_document(session_factory, master_data)
    second = _create_document(session_factory, master_data)
    for created in (record, second):
        submit_document(
            session_factory,
            created.document.id,
            version=1,
            actor=master_data.actor,
            source="API",
        )
    soft_delete_document(
        session_factory,
        record.document.id,
        actor=master_data.actor,
        source="API",
    )
    response = _export(client, record.document.id)
    workbook = load_workbook(BytesIO(response.content))
    info = _sheet_values(workbook, "单据信息")
    assert info["状态"] == "已生效（POSTED）"
    assert info["软删除状态"].startswith("是（")
    assert "不能用于快速录入" not in info["快速录入"]


def test_xlsx_export_unknown_document_returns_404(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _identity = logged_in_admin
    response = client.get(f"/api/v1/documents/{uuid4()}/export")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_xlsx_export_over_2000_lines_is_allowed_but_marked(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    client, _identity = logged_in_admin
    materials = [
        create_entity(
            session_factory,
            "material",
            name=f"导出物资{index:03d}",
            actor_user_id=master_data.actor.id,
            request_id=None,
            category_id=master_data.category.id,
            unit="个",
            quantity_type="DECIMAL",
        )
        for index in range(50)
    ]
    projects = [
        create_entity(
            session_factory,
            "project",
            name=f"导出项目{index:03d}",
            actor_user_id=master_data.actor.id,
            request_id=None,
        )
        for index in range(41)
    ]
    lines = tuple(
        DocumentLineInput(
            material_id=materials[index % 50].id,
            quantity="1",
            manufacturer_id=master_data.manufacturer.id,
            project_id=projects[index // 50].id,
        )
        for index in range(2001)
    )
    data = DocumentInput(
        doc_type="INBOUND",
        warehouse_id=master_data.warehouse.id,
        department_id=master_data.department.id,
        lines=lines,
    )
    record = _create_document(session_factory, master_data, data=data)
    response = _export(client, record.document.id)
    assert response.headers["X-XLSX-Quick-Entry"] == "disabled"
    workbook = load_workbook(BytesIO(response.content))
    assert workbook["录入数据"].max_row == 2004
    help_text = "\n".join(
        str(row[1].value or "")
        for row in workbook["填写说明"].iter_rows(min_row=1, max_row=40, max_col=2)
    )
    assert "不能用于快速录入" in help_text
    assert "不能用于快速录入" in _sheet_values(workbook, "单据信息")["快速录入"]


@pytest.mark.parametrize("doc_type", ["INBOUND", "OUTBOUND"])
def test_xlsx_parse_accepts_downloaded_template_after_filling(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    master_data: MasterDataFixture,
    doc_type: str,
) -> None:
    client, identity = logged_in_admin
    if doc_type == "INBOUND":
        rows = [
            [
                master_data.project.name,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "1",
                master_data.manufacturer.name,
                None,
            ]
        ]
    else:
        rows = [
            [
                master_data.project.name,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "1",
                None,
            ]
        ]
    template = client.get(f"/api/v1/document-templates/{doc_type}")
    assert template.status_code == 200
    workbook = load_workbook(BytesIO(template.content))
    data_sheet = workbook["录入数据"]
    data_sheet["B1"] = master_data.warehouse.name
    data_sheet["B2"] = master_data.department.name
    for row_no, values in enumerate(rows, start=4):
        for column, value in enumerate(values, start=1):
            if value is not None:
                data_sheet.cell(row=row_no, column=column, value=value)
    buffer = BytesIO()
    workbook.save(buffer)
    response = client.post(
        "/api/v1/document-entry/parse",
        data={"doc_type": doc_type},
        files=upload_files(buffer.getvalue()),
        headers=csrf(identity),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "SUCCESS"
