"""10 万有效明细的查询计划与响应时间基线。"""

from collections.abc import Iterator
from pathlib import Path
from time import perf_counter

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.database import create_engine_from_settings, create_session_factory
from app.db.migrations import upgrade_to_head
from app.modules.documents.service import DocumentFilters, list_documents
from app.modules.inventory.service import list_inventory, list_transactions
from tests.performance.support import build_dataset, query_plan

_VIEW_SQL = """
SELECT d.warehouse_id, l.material_id, SUM(l.quantity_milli * CASE
    WHEN d.doc_type = 'INBOUND' THEN 1 ELSE -1 END) AS balance_milli
FROM stock_documents d
JOIN stock_document_lines l ON l.document_id = d.id
WHERE d.status = 'POSTED' AND d.deleted_at IS NULL
GROUP BY d.warehouse_id, l.material_id
"""


@pytest.fixture(scope="module")
def perf_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    root = tmp_path_factory.mktemp("perf")
    settings = Settings.from_env(
        {
            "APP_ENV": "test",
            "DATA_DIR": str(root / "data"),
            "BACKUP_DIR": str(root / "backups"),
            "BOOTSTRAP_ADMIN_USERNAME": "",
            "BOOTSTRAP_ADMIN_PASSWORD": "",
        }
    )
    upgrade_to_head(settings)
    stats = build_dataset(settings.database_path)
    assert stats.lines == 100_000
    return settings


@pytest.fixture(scope="module")
def perf_engine(perf_settings: Settings) -> Iterator[Engine]:
    engine = create_engine_from_settings(perf_settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def perf_session_factory(perf_engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(perf_engine)


def _assert_indexed(details: list[str], schema_name: str, index: str) -> None:
    text = "\n".join(details)
    assert index in text, details
    for detail in details:
        if detail.startswith(f"SCAN {schema_name}") and "USING" not in detail:
            pytest.fail(f"发现无索引全表扫描：{detail}")


def test_dataset_contains_100k_effective_lines(perf_engine: Engine) -> None:
    with perf_engine.connect() as connection:
        lines = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM stock_document_lines l JOIN stock_documents d "
            "ON d.id = l.document_id WHERE d.status = 'POSTED' AND d.deleted_at IS NULL"
        ).scalar()
        balance = connection.exec_driver_sql(
            "SELECT COALESCE(SUM(balance_milli), 0) FROM v_inventory_balance"
        ).scalar()
    assert lines == 100_000
    assert balance > 0


def test_inventory_view_uses_status_and_line_indexes(perf_engine: Engine) -> None:
    details = query_plan(perf_engine, _VIEW_SQL)

    _assert_indexed(details, "stock_documents", "ix_stock_documents_status_warehouse")
    _assert_indexed(details, "stock_document_lines", "ix_stock_document_lines_document_id")


def test_document_list_uses_created_at_index(perf_engine: Engine) -> None:
    details = query_plan(
        perf_engine,
        "SELECT id, doc_no FROM stock_documents ORDER BY created_at DESC LIMIT 20",
    )

    _assert_indexed(details, "stock_documents", "ix_stock_documents_created_at")


def test_posting_filters_use_submitted_index(perf_engine: Engine) -> None:
    details = query_plan(
        perf_engine,
        "SELECT id, doc_no FROM stock_documents "
        "WHERE status = 'POSTED' AND deleted_at IS NULL "
        "ORDER BY submitted_at DESC LIMIT 20",
    )

    _assert_indexed(details, "stock_documents", "ix_stock_documents_status_submitted")


def test_transactions_query_uses_document_and_line_indexes(perf_engine: Engine) -> None:
    details = query_plan(
        perf_engine,
        "SELECT l.id, d.doc_no, l.quantity_milli FROM stock_document_lines l "
        "JOIN stock_documents d ON d.id = l.document_id "
        "WHERE d.status = 'POSTED' AND d.deleted_at IS NULL "
        "ORDER BY d.submitted_at DESC LIMIT 20",
    )

    _assert_indexed(details, "stock_documents", "ix_stock_documents_status_submitted")
    _assert_indexed(details, "stock_document_lines", "ix_stock_document_lines_document_id")


def test_material_filter_uses_material_index(perf_engine: Engine) -> None:
    details = query_plan(
        perf_engine,
        "SELECT d.id FROM stock_documents d "
        "JOIN stock_document_lines l ON l.document_id = d.id "
        "WHERE l.material_id = ? AND d.status = 'POSTED' AND d.deleted_at IS NULL",
        ("20000000-0000-4000-8000-000000000001",),
    )

    assert any("ix_stock_document_lines_material_id" in detail for detail in details), details


def test_common_queries_stay_within_p95_budget(
    perf_session_factory: sessionmaker[Session],
) -> None:
    timings: dict[str, float] = {}
    with perf_session_factory() as session:
        started = perf_counter()
        inventory, total = list_inventory(session, page=1, page_size=20)
        timings["inventory"] = perf_counter() - started
        assert total > 0
        assert len(inventory) == 20

        started = perf_counter()
        documents, _projects, document_total = list_documents(
            session,
            filters=DocumentFilters(status="POSTED"),
            page=1,
            page_size=20,
        )
        timings["documents"] = perf_counter() - started
        assert document_total == 1_000
        assert len(documents) == 20

        started = perf_counter()
        transactions, transaction_total = list_transactions(session, page=1, page_size=20)
        timings["transactions"] = perf_counter() - started
        assert transaction_total == 100_000
        assert len(transactions) == 20

    for name, seconds in timings.items():
        assert seconds < 2.0, f"{name} 查询耗时 {seconds:.3f}s 超过 2 秒基线"


def test_database_file_size_is_bounded(perf_settings: Settings) -> None:
    size = Path(perf_settings.database_path).stat().st_size
    assert size > 0
    # 100k 明细的基线体积，防止测试数据生成意外膨胀。
    assert size < 200 * 1024 * 1024


def test_twenty_concurrent_sessions_are_closed_and_memory_stays_bounded(
    perf_engine: Engine,
    perf_session_factory: sessionmaker[Session],
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def rss_kb() -> int:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
        return 0

    def task() -> None:
        for _ in range(5):
            with perf_session_factory() as session:
                list_inventory(session, page=1, page_size=20)

    before = rss_kb()
    with ThreadPoolExecutor(max_workers=20) as executor:
        list(executor.map(lambda _index: task(), range(20)))
    after = rss_kb()

    assert perf_engine.pool.checkedout() == 0
    assert after - before < 200 * 1024
