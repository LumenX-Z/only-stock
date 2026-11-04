"""软删除、提交与停用启用并发竞争测试。

依据 docs/06-库存计算与单据业务流程.md 7.7、docs/16-验收标准.md 13.3
ACC-FLOW-008/009 与 docs/15-测试方案.md 12.1：
- 软删除与出库提交竞争不得产生负库存，成功结果必须唯一且可解释；
- 停用仓库/项目与单据提交竞争必须串行化，不能出现停用资料仍在使用的矛盾状态；
- 并发停用/启用只写实际状态变化对应的审计，版本与审计数一致。
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
from app.modules.documents.service import create_draft, soft_delete_document, submit_document
from app.modules.inventory.service import current_balances
from app.modules.masterdata.service import set_entity_active
from app.modules.projects.models import Project
from app.modules.warehouses.models import Warehouse
from tests.conftest import MasterDataFixture


def _inbound(
    session_factory: sessionmaker[Session],
    master: MasterDataFixture,
    settings: Settings,
    quantity: str,
    *,
    warehouse: Any = None,
    project: Any = None,
) -> Any:
    return create_draft(
        session_factory,
        DocumentInput(
            doc_type="INBOUND",
            warehouse_id=(warehouse or master.warehouse).id,
            department_id=master.department.id,
            lines=(
                DocumentLineInput(
                    material_id=master.material.id,
                    quantity=quantity,
                    manufacturer_id=master.manufacturer.id,
                    project_id=None if project is None else project.id,
                ),
            ),
        ),
        actor=master.actor,
        request_id=str(uuid4()),
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


def _balance(session_factory: sessionmaker[Session], master: MasterDataFixture) -> int:
    with session_factory() as session:
        balances = current_balances(session, master.warehouse.id, [master.material.id])
    return balances.get(master.material.id, 0)


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


def test_concurrent_soft_delete_and_outbound_submit_keep_balance_non_negative(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    inbound = _inbound(session_factory, master_data, settings, "10")
    submit_document(
        session_factory,
        inbound.document.id,
        version=1,
        actor=master_data.actor,
    )
    outbound = _outbound(session_factory, master_data, settings, "8")

    results = run_concurrently(
        [
            lambda: _attempt(
                lambda: soft_delete_document(
                    session_factory,
                    inbound.document.id,
                    actor=master_data.actor,
                )
            ),
            lambda: _attempt(
                lambda: submit_document(
                    session_factory,
                    outbound.document.id,
                    version=1,
                    actor=master_data.actor,
                )
            ),
        ]
    )

    delete_result, submit_result = results
    assert (delete_result[0] == "ok") != (submit_result[0] == "ok")
    balance = _balance(session_factory, master_data)
    assert balance >= 0
    with session_factory() as session:
        inbound_document = session.get(StockDocument, inbound.document.id)
        outbound_document = session.get(StockDocument, outbound.document.id)
        assert inbound_document is not None and outbound_document is not None
        assert outbound_document.status in {DocStatus.DRAFT.value, DocStatus.POSTED.value}
    if delete_result[0] == "ok":
        assert delete_result == ("ok", "")
        assert submit_result == ("error", "INSUFFICIENT_STOCK")
        assert balance == 0
        assert inbound_document is not None and inbound_document.deleted_at is not None
        assert outbound_document.status == DocStatus.DRAFT.value
    else:
        assert delete_result == ("error", "NEGATIVE_STOCK")
        assert submit_result == ("ok", "")
        assert balance == 2000
        assert inbound_document is not None and inbound_document.deleted_at is None
        assert outbound_document.status == DocStatus.POSTED.value


def test_concurrent_warehouse_disable_and_inbound_submit(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    draft = _inbound(
        session_factory,
        master_data,
        settings,
        "5",
        warehouse=master_data.warehouse_second,
    )

    results = run_concurrently(
        [
            lambda: _attempt(
                lambda: set_entity_active(
                    session_factory,
                    "warehouse",
                    master_data.warehouse_second.id,
                    active=False,
                    actor_user_id=master_data.actor.id,
                    request_id=None,
                )
            ),
            lambda: _attempt(
                lambda: submit_document(
                    session_factory,
                    draft.document.id,
                    version=1,
                    actor=master_data.actor,
                )
            ),
        ]
    )

    disable_result, submit_result = results
    assert (disable_result[0] == "ok") != (submit_result[0] == "ok")
    with session_factory() as session:
        warehouse = session.get(Warehouse, master_data.warehouse_second.id)
        document = session.get(StockDocument, draft.document.id)
        assert warehouse is not None and document is not None
        is_active = bool(warehouse.is_active)
        status = document.status
    balance = _balance_second(session_factory, master_data)
    assert not (not is_active and balance > 0)
    if submit_result[0] == "ok":
        assert disable_result == ("error", "WAREHOUSE_HAS_STOCK")
        assert is_active is True
        assert status == DocStatus.POSTED.value
        assert balance == 5000
    else:
        assert disable_result == ("ok", "")
        assert submit_result == ("error", "WAREHOUSE_INACTIVE")
        assert is_active is False
        assert status == DocStatus.DRAFT.value
        assert balance == 0


def _balance_second(session_factory: sessionmaker[Session], master: MasterDataFixture) -> int:
    with session_factory() as session:
        balances = current_balances(session, master.warehouse_second.id, [master.material.id])
    return balances.get(master.material.id, 0)


def test_concurrent_project_disable_and_submit_preserves_snapshot(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    draft = _inbound(
        session_factory,
        master_data,
        settings,
        "3",
        project=master_data.project,
    )

    results = run_concurrently(
        [
            lambda: _attempt(
                lambda: set_entity_active(
                    session_factory,
                    "project",
                    master_data.project.id,
                    active=False,
                    actor_user_id=master_data.actor.id,
                    request_id=None,
                )
            ),
            lambda: _attempt(
                lambda: submit_document(
                    session_factory,
                    draft.document.id,
                    version=1,
                    actor=master_data.actor,
                )
            ),
        ]
    )

    disable_result, submit_result = results
    # 项目停用没有库存前置条件，始终成功；提交则取决于是否先取得写锁。
    assert disable_result == ("ok", "")
    with session_factory() as session:
        project = session.get(Project, master_data.project.id)
        document = session.get(StockDocument, draft.document.id)
        assert project is not None and document is not None
        assert project.is_active is False
        if submit_result[0] == "ok":
            assert document.status == DocStatus.POSTED.value
            assert document.lines[0].project_id == master_data.project.id
            assert document.lines[0].project_name == master_data.project.name
        else:
            assert submit_result == ("error", "PROJECT_INACTIVE")
            assert document.status == DocStatus.DRAFT.value
    assert _audit_count(session_factory, str(master_data.project.id), "PROJECT_DISABLED") == 1
    if submit_result[0] == "ok":
        assert _balance(session_factory, master_data) == 3000
        assert _audit_count(session_factory, draft.document.id, "DOCUMENT_SUBMITTED") == 1
    else:
        assert _balance(session_factory, master_data) == 0
        assert _audit_count(session_factory, draft.document.id, "DOCUMENT_SUBMITTED") == 0


def test_concurrent_project_disable_and_enable_keep_audit_consistent(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    results = run_concurrently(
        [
            lambda: _attempt(
                lambda: set_entity_active(
                    session_factory,
                    "project",
                    master_data.project.id,
                    active=False,
                    actor_user_id=master_data.actor.id,
                    request_id=None,
                )
            ),
            lambda: _attempt(
                lambda: set_entity_active(
                    session_factory,
                    "project",
                    master_data.project.id,
                    active=True,
                    actor_user_id=master_data.actor.id,
                    request_id=None,
                )
            ),
        ]
    )

    assert results == [("ok", ""), ("ok", "")]
    enabled = _audit_count(session_factory, str(master_data.project.id), "PROJECT_ENABLED")
    disabled = _audit_count(session_factory, str(master_data.project.id), "PROJECT_DISABLED")
    transitions = enabled + disabled
    with session_factory() as session:
        project = session.get(Project, master_data.project.id)
        assert project is not None
        assert project.version == 1 + transitions
        if project.is_active:
            # 先停用后启用才可能回到启用；启用已启用记录是幂等无审计的。
            assert transitions == 2
        else:
            assert transitions == 1
    assert transitions in {1, 2}


def test_concurrent_double_soft_delete_writes_marker_and_audit_once(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    inbound = _inbound(session_factory, master_data, settings, "5")
    submit_document(
        session_factory,
        inbound.document.id,
        version=1,
        actor=master_data.actor,
    )

    results = run_concurrently(
        [
            lambda: _attempt(
                lambda: soft_delete_document(
                    session_factory,
                    inbound.document.id,
                    actor=master_data.actor,
                )
            ),
            lambda: _attempt(
                lambda: soft_delete_document(
                    session_factory,
                    inbound.document.id,
                    actor=master_data.actor,
                )
            ),
        ]
    )

    assert sorted(results) == [("error", "DOCUMENT_ALREADY_DELETED"), ("ok", "")]
    assert _balance(session_factory, master_data) == 0
    assert _audit_count(session_factory, inbound.document.id, "DOCUMENT_SOFT_DELETED") == 1
