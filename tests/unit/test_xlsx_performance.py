"""XLSX 2,000 行解析与导出性能基准。

依据 docs/14-安全、可靠性与性能要求.md 11.3 与 docs/16-验收标准.md ACC-PERF-004：
- 参考环境下 2,000 行 XLSX 无副作用解析或单据导出单次目标不超过 10 秒；
- 名称解析必须批量查询，禁止逐行重复查询。
"""

import time
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.modules.documents.inputs import DocumentInput, DocumentLineInput
from app.modules.documents.service import create_draft
from app.modules.masterdata.service import create_entity
from app.modules.xlsx.export import build_export_bytes
from app.modules.xlsx.parser import parse_document_entry
from tests.conftest import MasterDataFixture
from tests.xlsx_support import filled_workbook


def _large_reference_data(
    session_factory: sessionmaker[Session],
    master: MasterDataFixture,
    *,
    material_count: int = 50,
    project_count: int = 41,
) -> tuple[list[Any], list[Any]]:
    materials = [
        create_entity(
            session_factory,
            "material",
            name=f"性能物资{index:03d}",
            actor_user_id=master.actor.id,
            request_id=None,
            category_id=master.category.id,
            unit="个",
            quantity_type="DECIMAL",
        )
        for index in range(material_count)
    ]
    projects = [
        create_entity(
            session_factory,
            "project",
            name=f"性能项目{index:03d}",
            actor_user_id=master.actor.id,
            request_id=None,
        )
        for index in range(project_count)
    ]
    return materials, projects


def _rows(master: MasterDataFixture, materials: list[Any], projects: list[Any], count: int):
    return [
        [
            projects[index // len(materials)].name,
            master.category.name,
            None,
            materials[index % len(materials)].name,
            "1",
            master.manufacturer.name,
            None,
        ]
        for index in range(count)
    ]


def test_xlsx_parse_2000_rows_performance_and_batch_queries(
    tmp_path: Path,
    migrated_engine: Engine,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    materials, projects = _large_reference_data(session_factory, master_data)
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=_rows(master_data, materials, projects, 2000),
    )
    path = tmp_path / "perf.xlsx"
    path.write_bytes(data)

    selects = 0

    def count_select(conn, cursor, statement, parameters, context, executemany) -> None:
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(migrated_engine, "before_cursor_execute", count_select)
    try:
        started = time.perf_counter()
        with session_factory() as session:
            parsed = parse_document_entry(path, doc_type="INBOUND", session=session)
        elapsed = time.perf_counter() - started
    finally:
        event.remove(migrated_engine, "before_cursor_execute", count_select)

    assert parsed.status == "SUCCESS"
    assert parsed.total_rows == 2000
    assert len(parsed.lines) == 2000
    assert elapsed < 10.0, elapsed
    assert selects <= 20, selects


def test_xlsx_export_2000_rows_performance(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    materials, projects = _large_reference_data(session_factory, master_data)
    lines = tuple(
        DocumentLineInput(
            material_id=materials[index % len(materials)].id,
            quantity="1.250",
            manufacturer_id=master_data.manufacturer.id,
            project_id=projects[index // len(materials)].id,
            remark="性能备注" if index % 2 == 0 else None,
        )
        for index in range(2000)
    )
    record = create_draft(
        session_factory,
        DocumentInput(
            doc_type="INBOUND",
            warehouse_id=master_data.warehouse.id,
            department_id=master_data.department.id,
            lines=lines,
        ),
        actor=master_data.actor,
        request_id=str(uuid4()),
        timezone=ZoneInfo("Asia/Shanghai"),
        source="API",
    )

    started = time.perf_counter()
    with session_factory() as session:
        data = build_export_bytes(
            session,
            record.document,
            record.lines,
            timezone=ZoneInfo("Asia/Shanghai"),
        )
    elapsed = time.perf_counter() - started

    assert data.startswith(b"PK")
    assert elapsed < 10.0, elapsed
