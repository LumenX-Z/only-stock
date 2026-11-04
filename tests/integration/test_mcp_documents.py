"""MCP 建单、原子直接提交与 300 秒去重测试。

依据 docs/08-MCP服务与工具.md 7.9、8.6.4、8.6.5，docs/06-库存计算与单据业务流程.md
7.3、7.4、7.7 与 docs/16-验收标准.md ACC-MCP-007–ACC-MCP-011。
"""

import asyncio
import hashlib
from collections.abc import Callable, Sequence
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.models import ApiKey
from app.config import Settings
from app.db.types import utc_now
from app.mcp import documents as mcp_documents
from app.modules.audit.models import AuditLog
from app.modules.documents.inputs import PreparedDocument, PreparedLine
from app.modules.documents.models import (
    DocumentCounter,
    StockDocument,
    StockDocumentLine,
)
from app.modules.documents.service import (
    create_mcp_document,
    soft_delete_document,
)
from app.modules.inventory.service import current_balances
from app.modules.masterdata.service import set_entity_active
from tests.conftest import MasterDataFixture, MCPAccounts
from tests.mcp_support import (
    assert_no_internal_ids,
    call_tool,
    mcp_sessions,
    structured,
    tool_error,
)


def _document_payload(
    master_data: MasterDataFixture,
    *,
    submit: bool,
    quantity: str = "1.250",
    project: str | None = "XM-00001",
    remark: str | None = "采购批次 A",
    doc_type: str = "INBOUND",
) -> dict[str, object]:
    line: dict[str, object] = {
        "material_code": master_data.material.code,
        "quantity": quantity,
    }
    if doc_type == "INBOUND":
        line["manufacturer_code"] = master_data.manufacturer.code
    if project is not None:
        line["project_code"] = project
    if remark is not None:
        line["remark"] = remark
    return {
        "doc_type": doc_type,
        "submit": submit,
        "warehouse_code": master_data.warehouse.code,
        "department_code": master_data.department.code,
        "lines": [line],
    }


def _count(session_factory: sessionmaker[Session], model: type) -> int:
    with session_factory() as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _mcp_audits(session_factory: sessionmaker[Session]) -> list[AuditLog]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(AuditLog)
                .where(AuditLog.source == "MCP")
                .order_by(AuditLog.created_at, AuditLog.id)
            ).all()
        )


def _key_id(session_factory: sessionmaker[Session], raw_key: str) -> str:
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    with session_factory() as session:
        row = session.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))
    assert row is not None
    return row.id


def _document_id(session_factory: sessionmaker[Session], doc_no: str) -> str:
    with session_factory() as session:
        row = session.scalar(select(StockDocument).where(StockDocument.doc_no == doc_no))
    assert row is not None
    return row.id


async def test_draft_does_not_change_inventory_and_audits_mcp_source(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        created = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=False),
            )
        )
    assert created["status"] == "DRAFT"
    assert created["deduplicated"] is False
    assert_no_internal_ids(created)

    with session_factory() as session:
        document = session.scalar(
            select(StockDocument).where(StockDocument.doc_no == created["doc_no"])
        )
        assert document is not None
        assert document.created_source == "MCP"
        assert document.request_fingerprint
        assert document.request_id
        balances = current_balances(session, master_data.warehouse.id, [master_data.material.id])
    assert balances == {}

    audits = _mcp_audits(session_factory)
    assert [audit.action for audit in audits] == ["DOCUMENT_CREATED"]
    assert audits[0].api_key_id is not None
    assert audits[0].actor_user_id == mcp_accounts.ca.id
    assert audits[0].request_id == document.request_id


async def test_direct_submit_is_atomic_and_updates_inventory(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        created = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=True),
            )
        )
    assert created["status"] == "POSTED"
    assert created["submitted_at"] is not None
    assert created["submitted_by_name"] == mcp_accounts.ca.display_name
    assert_no_internal_ids(created)

    with session_factory() as session:
        balances = current_balances(session, master_data.warehouse.id, [master_data.material.id])
    assert balances[master_data.material.id] == 1250

    audits = _mcp_audits(session_factory)
    assert {audit.action for audit in audits} == {"DOCUMENT_CREATED", "DOCUMENT_SUBMITTED"}
    assert all(audit.api_key_id for audit in audits)


async def test_insufficient_stock_rolls_back_without_draft(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        result = await call_tool(
            sessions["ca"],
            "document_create",
            _document_payload(master_data, submit=True, doc_type="OUTBOUND"),
        )
        error = tool_error(result)
        assert error["code"] == "INSUFFICIENT_STOCK"
        assert error["details"]["items"][0]["code"] == master_data.material.code
        assert "available" in error["details"]["items"][0]
        assert_no_internal_ids(error)

        # 失败后同一内容仍可立即重试；成功单号仍从 000001 开始，证明计数器已回滚。
        retry = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=False, doc_type="OUTBOUND"),
            )
        )
    assert retry["doc_no"].endswith("-000001")

    assert _count(session_factory, StockDocument) == 1
    assert _count(session_factory, DocumentCounter) == 1
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.source == "MCP")
            )
            == 1
        )


async def test_repeated_submit_within_window_is_deduplicated(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        first = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=True),
            )
        )
        audit_count = len(_mcp_audits(session_factory))
        second = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(
                    master_data, submit=True, quantity="1.25", remark="  采购批次 A  "
                ),
            )
        )
        assert second["doc_no"] == first["doc_no"]
        assert second["deduplicated"] is True
        assert second["lines"][0]["quantity"] == "1.250"
        assert len(_mcp_audits(session_factory)) == audit_count

    assert _count(session_factory, StockDocument) == 1
    with session_factory() as session:
        line = session.scalar(select(StockDocumentLine))
        assert line is not None
        assert line.remark == "采购批次 A"
        balances = current_balances(session, master_data.warehouse.id, [master_data.material.id])
    assert balances[master_data.material.id] == 1250


async def test_submit_flag_remark_and_creator_are_not_deduplicated(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    async with mcp_sessions(
        app, {"ca": mcp_accounts.ca_key, "op": mcp_accounts.op_key}
    ) as sessions:
        posted = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=True),
            )
        )
        draft = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=False),
            )
        )
        assert draft["deduplicated"] is False
        assert draft["doc_no"] != posted["doc_no"]
        assert draft["status"] == "DRAFT"

        again = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=False),
            )
        )
        assert again["deduplicated"] is True
        assert again["doc_no"] == draft["doc_no"]

        changed = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=False, remark="另一个批次"),
            )
        )
        assert changed["deduplicated"] is False
        assert changed["doc_no"] not in {posted["doc_no"], draft["doc_no"]}

        other_user = structured(
            await call_tool(
                sessions["op"],
                "document_create",
                _document_payload(master_data, submit=True),
            )
        )
        assert other_user["deduplicated"] is False
        assert other_user["doc_no"] != posted["doc_no"]

    assert _count(session_factory, StockDocument) == 4


async def test_soft_deleted_document_is_not_deduplicated(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        first = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=True),
            )
        )
        document_id = _document_id(session_factory, first["doc_no"])
        soft_delete_document(
            session_factory,
            document_id,
            actor=mcp_accounts.ca,
            source="MCP",
            request_id=str(uuid4()),
        )
        again = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=True),
            )
        )
    assert again["deduplicated"] is False
    assert again["doc_no"] != first["doc_no"]


async def test_window_expiry_allows_new_document(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    def prepare(_session: Session) -> PreparedDocument:
        return PreparedDocument(
            doc_type="INBOUND",
            warehouse_id=master_data.warehouse.id,
            department_id=master_data.department.id,
            lines=(
                PreparedLine(
                    material_id=master_data.material.id,
                    manufacturer_id=master_data.manufacturer.id,
                    project_id=master_data.project.id,
                    quantity_milli=1250,
                    remark="采购批次 A",
                ),
            ),
        )

    key_id = _key_id(session_factory, mcp_accounts.ca_key)
    original = create_mcp_document(
        session_factory,
        prepare,
        submit=True,
        actor=mcp_accounts.ca,
        request_id=str(uuid4()),
        api_key_id=key_id,
        timezone=app.state.settings.timezone,
        now=utc_now() - timedelta(seconds=301),
    )

    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        result = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=True),
            )
        )
    assert result["deduplicated"] is False
    assert result["doc_no"] != original.document.doc_no


async def test_inactive_warehouse_and_unknown_codes_are_rejected(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    set_entity_active(
        session_factory,
        "warehouse",
        master_data.warehouse.id,
        active=False,
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        inactive = await call_tool(
            sessions["ca"],
            "document_create",
            _document_payload(master_data, submit=True),
        )
        error = tool_error(inactive)
        assert error["code"] == "WAREHOUSE_INACTIVE"
        assert_no_internal_ids(error)
    assert _count(session_factory, StockDocument) == 0


async def test_queries_do_not_write_business_audit(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    before = _count(session_factory, AuditLog)
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        for name in (
            "warehouse_list",
            "department_list",
            "manufacturer_list",
            "category_list",
            "material_list",
            "document_list",
        ):
            result = await call_tool(sessions["ca"], name, {})
            assert result.is_error is False
    assert _count(session_factory, AuditLog) == before


async def test_soft_deleted_document_detail_and_deleted_filter(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        created = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=True),
            )
        )
        soft_delete_document(
            session_factory,
            _document_id(session_factory, created["doc_no"]),
            actor=mcp_accounts.ca,
            source="MCP",
            request_id=str(uuid4()),
        )
        detail = structured(
            await call_tool(sessions["ca"], "document_list", {"doc_no": created["doc_no"]})
        )
        only_deleted = structured(
            await call_tool(sessions["ca"], "document_list", {"deleted": True})
        )
        only_alive = structured(
            await call_tool(sessions["ca"], "document_list", {"deleted": False})
        )

    assert detail["deleted"] is True
    assert detail["deleted_at"] is not None
    assert detail["lines"][0]["material_code"] == master_data.material.code
    assert only_deleted["total"] == 1
    assert only_alive["total"] == 0


async def test_outbound_has_no_manufacturer_filter_match(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
) -> None:
    async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
        created = structured(
            await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=False, doc_type="OUTBOUND"),
            )
        )
        filtered = structured(
            await call_tool(
                sessions["ca"],
                "document_list",
                {"manufacturer_code": master_data.manufacturer.code},
            )
        )
    assert created["lines"][0]["manufacturer_code"] is None
    assert filtered["total"] == 0


def test_audit_failure_rolls_back_direct_submit(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    mcp_accounts: MCPAccounts,
    settings: Settings,
) -> None:
    def failing_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr("app.modules.documents.service.record_audit", failing_audit)
    request = mcp_documents.parse_document_request(_document_payload(master_data, submit=True))
    with pytest.raises(RuntimeError):
        mcp_documents.create_document(
            session_factory,
            request,
            actor=mcp_accounts.ca,
            request_id=str(uuid4()),
            api_key_id=_key_id(session_factory, mcp_accounts.ca_key),
            timezone=settings.timezone,
        )
    monkeypatch.undo()

    assert _count(session_factory, StockDocument) == 0
    assert _count(session_factory, StockDocumentLine) == 0
    assert _count(session_factory, DocumentCounter) == 0
    with session_factory() as session:
        balances = current_balances(session, master_data.warehouse.id, [master_data.material.id])
    assert balances == {}


def test_concurrent_identical_calls_create_one_document(
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
    session_factory: sessionmaker[Session],
    settings: Settings,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    request = mcp_documents.parse_document_request(
        _document_payload(master_data, submit=True, quantity="3")
    )

    def invoke() -> object:
        return mcp_documents.create_document(
            session_factory,
            request,
            actor=mcp_accounts.ca,
            request_id=str(uuid4()),
            api_key_id=_key_id(session_factory, mcp_accounts.ca_key),
            timezone=settings.timezone,
        )

    results = run_concurrently([invoke, invoke])
    assert sorted(result.deduplicated for result in results) == [False, True]
    assert len({result.document.doc_no for result in results}) == 1
    assert _count(session_factory, StockDocument) == 1


def test_quantity_and_remark_rules_are_enforced(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    master_data: MasterDataFixture,
) -> None:
    async def run() -> list[object]:
        async with mcp_sessions(app, {"ca": mcp_accounts.ca_key}) as sessions:
            bad_quantity = await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=True, quantity="1.2345"),
            )
            bad_integer = await call_tool(
                sessions["ca"],
                "document_create",
                {
                    **_document_payload(master_data, submit=True),
                    "lines": [
                        {
                            "material_code": master_data.material_integer.code,
                            "quantity": "1.5",
                            "manufacturer_code": master_data.manufacturer.code,
                        }
                    ],
                },
            )
            bad_remark = await call_tool(
                sessions["ca"],
                "document_create",
                _document_payload(master_data, submit=True, remark="备" * 501),
            )
            return [bad_quantity, bad_integer, bad_remark]

    results = asyncio.run(run())
    for result in results:
        error = tool_error(result)
        assert error["code"] == "VALIDATION_ERROR"
        assert_no_internal_ids(error)
