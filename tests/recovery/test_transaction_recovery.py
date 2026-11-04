"""故障注入、写锁超时与进程终止恢复测试。

依据 docs/06-库存计算与单据业务流程.md 7.7、docs/14-安全、可靠性与性能要求.md
11.2 与 docs/16-验收标准.md 13.3 ACC-FLOW-009：
- 数据库/审计异常、写锁超时与进程终止不得留下半张有效单据；
- 单据头、明细、计数器与审计必须作为整体提交或整体回滚；
- 写锁冲突返回明确的 409 ``WRITE_CONFLICT``，客户端可安全重试。
"""

import multiprocessing
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.database import create_engine_from_settings, create_session_factory
from app.db.migrations import upgrade_to_head
from app.errors import DatabaseBusyError
from app.main import create_app
from app.modules.audit.models import AuditLog
from app.modules.documents.inputs import DocumentInput, DocumentLineInput
from app.modules.documents.models import (
    DocStatus,
    DocumentCounter,
    StockDocument,
    StockDocumentLine,
)
from app.modules.documents.service import create_draft, soft_delete_document, submit_document
from app.modules.inventory.service import current_balances
from tests.conftest import MasterDataFixture
from tests.support import (
    OPERATOR_PASSWORD,
    csrf,
    inbound_payload,
    line,
    login,
)

CREATED_AT = datetime(2026, 1, 2, 3, 0, tzinfo=UTC)


def _inbound(
    session_factory: sessionmaker[Session],
    master: MasterDataFixture,
    settings: Settings,
    quantity: str = "1",
    *,
    request_id: str | None = None,
) -> Any:
    return create_draft(
        session_factory,
        DocumentInput(
            doc_type="INBOUND",
            warehouse_id=master.warehouse.id,
            department_id=master.department.id,
            lines=(
                DocumentLineInput(
                    material_id=master.material.id,
                    quantity=quantity,
                    manufacturer_id=master.manufacturer.id,
                ),
            ),
        ),
        actor=master.actor,
        request_id=request_id or str(uuid4()),
        timezone=settings.timezone,
        now=CREATED_AT,
    )


def _snapshot(session_factory: sessionmaker[Session]) -> dict[str, Any]:
    """记录单据头、明细、审计与计数器的整体状态。"""
    with session_factory() as session:
        counters = [
            (str(row.biz_date), str(row.doc_kind), int(row.next_value))
            for row in session.execute(
                select(
                    DocumentCounter.biz_date,
                    DocumentCounter.doc_kind,
                    DocumentCounter.next_value,
                ).order_by(DocumentCounter.biz_date, DocumentCounter.doc_kind)
            ).all()
        ]
        return {
            "documents": int(session.scalar(select(func.count()).select_from(StockDocument)) or 0),
            "lines": int(session.scalar(select(func.count()).select_from(StockDocumentLine)) or 0),
            "audits": int(session.scalar(select(func.count()).select_from(AuditLog)) or 0),
            "counters": counters,
        }


def _balance(session_factory: sessionmaker[Session], master: MasterDataFixture) -> dict[str, int]:
    with session_factory() as session:
        return current_balances(session, master.warehouse.id, [master.material.id])


def _injected_failure(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("injected audit failure")


def test_audit_failure_rolls_back_draft_creation(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _snapshot(session_factory)
    request_id = str(uuid4())
    monkeypatch.setattr("app.modules.documents.service.record_audit", _injected_failure)

    with pytest.raises(RuntimeError, match="injected audit failure"):
        _inbound(session_factory, master_data, settings, request_id=request_id)

    assert _snapshot(session_factory) == before
    with session_factory() as session:
        assert (
            session.scalar(select(StockDocument).where(StockDocument.request_id == request_id))
            is None
        )

    monkeypatch.undo()
    created = _inbound(session_factory, master_data, settings)
    assert created.document.doc_no.endswith("-000001")


def test_audit_failure_rolls_back_submit(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _inbound(session_factory, master_data, settings, "2")
    before = _snapshot(session_factory)
    monkeypatch.setattr("app.modules.documents.service.record_audit", _injected_failure)

    with pytest.raises(RuntimeError, match="injected audit failure"):
        submit_document(
            session_factory,
            draft.document.id,
            version=1,
            actor=master_data.actor,
            now=CREATED_AT,
        )

    assert _snapshot(session_factory) == before
    assert _balance(session_factory, master_data) == {}
    with session_factory() as session:
        document = session.get(StockDocument, draft.document.id)
        assert document is not None
        assert document.status == DocStatus.DRAFT.value
        assert document.version == 1
        assert document.submitted_at is None
        assert document.submitted_by is None

    monkeypatch.undo()
    posted = submit_document(
        session_factory,
        draft.document.id,
        version=1,
        actor=master_data.actor,
        now=CREATED_AT,
    )
    assert posted.document.status == DocStatus.POSTED.value
    assert _balance(session_factory, master_data)[master_data.material.id] == 2000


def test_audit_failure_rolls_back_soft_delete(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _inbound(session_factory, master_data, settings, "2")
    submit_document(
        session_factory,
        draft.document.id,
        version=1,
        actor=master_data.actor,
        now=CREATED_AT,
    )
    before = _snapshot(session_factory)
    monkeypatch.setattr("app.modules.documents.service.record_audit", _injected_failure)

    with pytest.raises(RuntimeError, match="injected audit failure"):
        soft_delete_document(
            session_factory,
            draft.document.id,
            actor=master_data.actor,
            now=CREATED_AT,
        )

    assert _snapshot(session_factory) == before
    assert _balance(session_factory, master_data)[master_data.material.id] == 2000
    with session_factory() as session:
        document = session.get(StockDocument, draft.document.id)
        assert document is not None
        assert document.deleted_at is None
        assert document.deleted_by is None

    monkeypatch.undo()
    soft_delete_document(
        session_factory,
        draft.document.id,
        actor=master_data.actor,
        now=CREATED_AT,
    )
    assert _balance(session_factory, master_data) == {}


def test_database_error_rolls_back_submit(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _inbound(session_factory, master_data, settings, "2")
    before = _snapshot(session_factory)

    def _database_error(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("injected database failure", None, RuntimeError("boom"))

    monkeypatch.setattr(
        "app.modules.documents.service.ensure_submit_balances",
        _database_error,
    )

    with pytest.raises(OperationalError, match="injected database failure"):
        submit_document(
            session_factory,
            draft.document.id,
            version=1,
            actor=master_data.actor,
            now=CREATED_AT,
        )

    assert _snapshot(session_factory) == before
    assert _balance(session_factory, master_data) == {}
    with session_factory() as session:
        document = session.get(StockDocument, draft.document.id)
        assert document is not None
        assert document.status == DocStatus.DRAFT.value
        assert document.version == 1


def test_write_lock_timeout_rolls_back_and_reports_conflict(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
) -> None:
    before = _snapshot(session_factory)
    lock_engine = create_engine_from_settings(replace(settings, sqlite_busy_timeout_ms=50))
    lock_factory = create_session_factory(lock_engine)
    holder = sqlite3.connect(str(settings.database_path), timeout=5)
    try:
        holder.execute("PRAGMA foreign_keys=ON")
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(DatabaseBusyError) as excinfo:
            _inbound(lock_factory, master_data, settings)
        assert excinfo.value.code == "WRITE_CONFLICT"
        assert excinfo.value.status_code == 409
    finally:
        holder.rollback()
        holder.close()
        lock_engine.dispose()

    assert _snapshot(session_factory) == before
    assert _balance(session_factory, master_data) == {}
    created = _inbound(session_factory, master_data, settings)
    assert created.document.doc_no.endswith("-000001")


def test_api_write_lock_timeout_returns_write_conflict(
    admin_settings: Settings,
    master_data: MasterDataFixture,
) -> None:
    upgrade_to_head(admin_settings)
    low_timeout = replace(admin_settings, sqlite_busy_timeout_ms=100)
    app = create_app(low_timeout)
    holder: sqlite3.Connection | None = None
    with TestClient(app) as client:
        # master_data 已在同一数据库创建管理员 data-admin；启动引导不会重复建号。
        identity = login(client, "data-admin", OPERATOR_PASSWORD)
        payload = inbound_payload(master_data, lines=[line(master_data)])
        request_id = payload["request_id"]
        holder = sqlite3.connect(str(admin_settings.database_path), timeout=5)
        try:
            holder.execute("PRAGMA foreign_keys=ON")
            holder.execute("BEGIN IMMEDIATE")
            response = client.post(
                "/api/v1/documents",
                json=payload,
                headers=csrf(identity),
            )
            assert response.status_code == 409, response.text
            error = response.json()["error"]
            assert error["code"] == "WRITE_CONFLICT"
            assert isinstance(error["details"], dict)
            assert "resource" in error["details"]
            assert "items" in error["details"]
        finally:
            holder.rollback()
            holder.close()
            holder = None

        retried = client.post(
            "/api/v1/documents",
            json=payload,
            headers=csrf(identity),
        )
        assert retried.status_code == 201, retried.text
        assert retried.json()["request_id"] == request_id


def _terminate_mid_transaction(
    database_path: str,
    source_document_id: str,
    *,
    new_document_id: str,
    new_line_id: str,
    doc_no: str,
    request_id: str,
    marker_path: str,
) -> None:
    """在未提交事务中写入单据头、明细和计数器后立即终止进程。"""
    connection = sqlite3.connect(database_path, timeout=10, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO stock_documents (
                id, doc_no, doc_type, status, warehouse_id, warehouse_code,
                warehouse_name, department_id, department_code, department_name,
                created_source, request_id, request_fingerprint, version, created_by,
                created_by_name, submitted_by, submitted_by_name, created_at, updated_at,
                submitted_at, deleted_at, deleted_by
            )
            SELECT
                ?, ?, doc_type, status, warehouse_id, warehouse_code,
                warehouse_name, department_id, department_code, department_name,
                created_source, ?, request_fingerprint, version, created_by,
                created_by_name, submitted_by, submitted_by_name, created_at, updated_at,
                submitted_at, deleted_at, deleted_by
            FROM stock_documents
            WHERE id = ?
            """,
            (new_document_id, doc_no, request_id, source_document_id),
        )
        connection.execute(
            """
            INSERT INTO stock_document_lines (
                id, document_id, line_no, material_id, material_code, material_name,
                unit, quantity_type, category_code, category_name,
                parent_category_code, parent_category_name, manufacturer_id,
                manufacturer_code, manufacturer_name, project_id, project_code,
                project_name, quantity_milli
            )
            SELECT
                ?, ?, 1, material_id, material_code, material_name, unit,
                quantity_type, category_code, category_name, parent_category_code,
                parent_category_name, manufacturer_id, manufacturer_code,
                manufacturer_name, project_id, project_code, project_name,
                quantity_milli
            FROM stock_document_lines
            WHERE document_id = ? AND line_no = 1
            """,
            (new_line_id, new_document_id, source_document_id),
        )
        connection.execute(
            "UPDATE document_counters SET next_value = next_value + 100 WHERE doc_kind = 'INBOUND'"
        )
        Path(marker_path).write_text("inserted", encoding="utf-8")
    finally:
        os._exit(1)


def test_process_termination_before_commit_leaves_no_partial_document(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    tmp_path: Path,
) -> None:
    source = _inbound(session_factory, master_data, settings, "1")
    before = _snapshot(session_factory)
    marker = tmp_path / "child-inserted"
    new_document_id = str(uuid4())
    new_line_id = str(uuid4())
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_terminate_mid_transaction,
        args=(str(settings.database_path), source.document.id),
        kwargs={
            "new_document_id": new_document_id,
            "new_line_id": new_line_id,
            "doc_no": "RK19990101-000123",
            "request_id": str(uuid4()),
            "marker_path": str(marker),
        },
        daemon=True,
    )
    process.start()
    process.join(30)
    try:
        assert process.exitcode == 1, process.exitcode
    finally:
        if process.is_alive():  # pragma: no cover - 超时保护
            process.terminate()
            process.join()

    assert marker.is_file(), "子进程未执行到插入完成"
    assert _snapshot(session_factory) == before
    with session_factory() as session:
        assert session.get(StockDocument, new_document_id) is None
        assert session.get(StockDocumentLine, new_line_id) is None

    second = _inbound(session_factory, master_data, settings, "1")
    assert second.document.doc_no.endswith("-000002")
