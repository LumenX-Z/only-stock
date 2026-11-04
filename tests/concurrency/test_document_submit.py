"""并发出库提交、重复建单与重复提交测试。

依据 docs/06-库存计算与单据业务流程.md 7.7 与 docs/16-验收标准.md ACC-FLOW-008、
ACC-FLOW-012：两张出库单不能都基于旧余额成功；同一 ``request_id`` 并发重放只
生成一张单据；重复提交只生效一次。
"""

from collections.abc import Callable, Sequence
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.errors import AppError
from app.modules.audit.models import AuditLog
from app.modules.documents.inputs import DocumentInput, DocumentLineInput
from app.modules.documents.models import DocStatus, StockDocument
from app.modules.documents.service import create_draft, submit_document
from app.modules.inventory.service import current_balances
from tests.conftest import MasterDataFixture


def _inbound(
    session_factory: sessionmaker[Session],
    master: MasterDataFixture,
    settings: Settings,
    quantity: str,
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
                    project_id=master.project.id,
                ),
            ),
        ),
        actor=master.actor,
        request_id=request_id or str(uuid4()),
        timezone=settings.timezone,
    )


def _outbound(
    session_factory: sessionmaker[Session],
    master: MasterDataFixture,
    settings: Settings,
    quantity: str,
) -> Any:
    return create_draft(
        session_factory,
        DocumentInput(
            doc_type="OUTBOUND",
            warehouse_id=master.warehouse.id,
            department_id=master.department.id,
            lines=(
                DocumentLineInput(
                    material_id=master.material.id,
                    quantity=quantity,
                    project_id=master.project.id,
                ),
            ),
        ),
        actor=master.actor,
        request_id=str(uuid4()),
        timezone=settings.timezone,
    )


def _attempt(action: Callable[[], object]) -> tuple[str, str]:
    try:
        action()
    except AppError as error:
        return ("error", error.code)
    return ("ok", "")


def _statuses(
    session_factory: sessionmaker[Session], document_ids: Sequence[str]
) -> dict[str, int]:
    with session_factory() as session:
        rows = session.scalars(
            select(StockDocument).where(StockDocument.id.in_(list(document_ids)))
        ).all()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return counts


def _balance(session_factory: sessionmaker[Session], master: MasterDataFixture) -> int:
    with session_factory() as session:
        balances = current_balances(session, master.warehouse.id, [master.material.id])
        return balances.get(master.material.id, 0)


def test_concurrent_outbound_submits_cannot_both_use_old_balance(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    inbound = _inbound(session_factory, master_data, settings, "10")
    submit_document(session_factory, inbound.document.id, version=1, actor=master_data.actor)

    first = _outbound(session_factory, master_data, settings, "8")
    second = _outbound(session_factory, master_data, settings, "8")

    results = run_concurrently(
        [
            lambda: _attempt(
                lambda: submit_document(
                    session_factory,
                    first.document.id,
                    version=1,
                    actor=master_data.actor,
                )
            ),
            lambda: _attempt(
                lambda: submit_document(
                    session_factory,
                    second.document.id,
                    version=1,
                    actor=master_data.actor,
                )
            ),
        ]
    )

    assert sorted(results) == [("error", "INSUFFICIENT_STOCK"), ("ok", "")]
    assert _balance(session_factory, master_data) == 2000
    statuses = _statuses(session_factory, [first.document.id, second.document.id])
    assert statuses == {"DRAFT": 1, "POSTED": 1}


def test_concurrent_same_request_id_creates_one_document(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    request_id = str(uuid4())

    def create() -> Callable[[], object]:
        return lambda: _inbound(
            session_factory, master_data, settings, "1.25", request_id=request_id
        )

    results = run_concurrently([create(), create()])

    doc_nos = {result.document.doc_no for result in results}
    assert len(doc_nos) == 1
    assert sorted(result.replayed for result in results) == [False, True]
    with session_factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(StockDocument)
            .where(StockDocument.request_id == request_id)
        )
    assert count == 1


def test_concurrent_document_numbers_are_unique_and_sequential(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    results = run_concurrently(
        [lambda: _inbound(session_factory, master_data, settings, "1") for _ in range(5)]
    )

    doc_nos = sorted(result.document.doc_no for result in results)
    assert len(set(doc_nos)) == 5
    prefix = doc_nos[0][:10]
    assert all(doc_no.startswith(prefix) for doc_no in doc_nos)
    assert doc_nos == [f"{prefix}-{value:06d}" for value in range(1, 6)]


def test_concurrent_duplicate_submit_posts_and_counts_once(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    inbound = _inbound(session_factory, master_data, settings, "2")

    results = run_concurrently(
        [
            lambda: _attempt(
                lambda: submit_document(
                    session_factory,
                    inbound.document.id,
                    version=1,
                    actor=master_data.actor,
                )
            ),
            lambda: _attempt(
                lambda: submit_document(
                    session_factory,
                    inbound.document.id,
                    version=1,
                    actor=master_data.actor,
                )
            ),
        ]
    )

    assert sorted(results) == [("error", "DOCUMENT_ALREADY_POSTED"), ("ok", "")]
    assert _balance(session_factory, master_data) == 2000
    assert _statuses(session_factory, [inbound.document.id]) == {DocStatus.POSTED.value: 1}
    with session_factory() as session:
        submitted = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.entity_id == inbound.document.id,
                AuditLog.action == "DOCUMENT_SUBMITTED",
            )
        )
    assert submitted == 1
