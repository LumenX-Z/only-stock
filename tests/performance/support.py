"""性能基线的数据生成与查询计划辅助。

只用于测试和压测脚本；不接入应用运行时。生成的数据全部写入临时数据库，
不读写开发数据库、生产数据卷或真实备份目录。
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

BASE_MILLIS = 1_767_225_600_000  # 2026-01-01T00:00:00Z
USER_ID = "10000000-0000-4000-8000-000000000001"
WAREHOUSE_ID = "10000000-0000-4000-8000-000000000002"
DEPARTMENT_ID = "10000000-0000-4000-8000-000000000003"
CATEGORY_ID = "10000000-0000-4000-8000-000000000004"
MANUFACTURER_ID = "10000000-0000-4000-8000-000000000005"


@dataclass(frozen=True, slots=True)
class DatasetStats:
    documents: int
    lines: int
    materials: int
    database_bytes: int


def _uuid(index: int) -> str:
    return f"20000000-0000-4000-8000-{index:012d}"


def build_dataset(
    database_path: Path,
    *,
    documents: int = 1_000,
    lines_per_document: int = 100,
    materials: int = 500,
) -> DatasetStats:
    """直接写入已迁移数据库，生成 ``documents * lines_per_document`` 条有效明细。"""
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        with connection:
            _insert_master_data(connection, materials)
            _insert_documents(
                connection,
                documents=documents,
                lines_per_document=lines_per_document,
                materials=materials,
            )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        database_bytes = database_path.stat().st_size
    finally:
        connection.close()
    return DatasetStats(
        documents=documents,
        lines=documents * lines_per_document,
        materials=materials,
        database_bytes=database_bytes,
    )


def _insert_master_data(connection: sqlite3.Connection, materials: int) -> None:
    created = BASE_MILLIS
    connection.execute(
        "INSERT INTO users (id, login_name, password_hash, display_name, role, status, "
        "force_password_change, password_version, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            USER_ID,
            "perf-operator",
            "x" * 32,
            "性能操作员",
            "OPERATOR",
            "ACTIVE",
            0,
            1,
            1,
            created,
            created,
        ),
    )
    connection.execute(
        "INSERT INTO warehouses (id, code, name, location, is_active, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (WAREHOUSE_ID, "CK-00001", "性能仓", "测试位置", 1, 1, created, created),
    )
    connection.execute(
        "INSERT INTO departments (id, code, name, is_active, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (DEPARTMENT_ID, "BM-00001", "性能部", 1, 1, created, created),
    )
    connection.execute(
        "INSERT INTO categories (id, code, name, parent_id, level, is_active, version, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (CATEGORY_ID, "FL-00001", "性能分类", None, 1, 1, 1, created, created),
    )
    connection.execute(
        "INSERT INTO manufacturers (id, code, name, is_active, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (MANUFACTURER_ID, "CJ-00001", "性能厂家", 1, 1, created, created),
    )
    connection.executemany(
        "INSERT INTO materials (id, code, name, category_id, unit, quantity_type, is_active, "
        "version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                _uuid(index),
                f"WZ-{index:05d}",
                f"性能物资{index:05d}",
                CATEGORY_ID,
                "个",
                "DECIMAL",
                1,
                1,
                created,
                created,
            )
            for index in range(1, materials + 1)
        ],
    )


def _insert_documents(
    connection: sqlite3.Connection,
    *,
    documents: int,
    lines_per_document: int,
    materials: int,
) -> None:
    document_rows = []
    line_rows = []
    for document_index in range(1, documents + 1):
        document_id = _uuid(document_index)
        created = BASE_MILLIS + document_index * 1_000
        document_rows.append(
            (
                document_id,
                f"RK20260101-{document_index:06d}",
                "INBOUND",
                "POSTED",
                WAREHOUSE_ID,
                "CK-00001",
                "性能仓",
                DEPARTMENT_ID,
                "BM-00001",
                "性能部",
                "API",
                str(_uuid(900_000 + document_index)),
                "a" * 64,
                1,
                USER_ID,
                "性能操作员",
                USER_ID,
                "性能操作员",
                created,
                created,
                created,
                None,
                None,
            )
        )
        for line_no in range(1, lines_per_document + 1):
            material_index = (document_index * lines_per_document + line_no) % materials + 1
            line_rows.append(
                (
                    _uuid(document_index * 1_000 + line_no),
                    document_id,
                    line_no,
                    _uuid(material_index),
                    f"WZ-{material_index:05d}",
                    f"性能物资{material_index:05d}",
                    "个",
                    "DECIMAL",
                    "FL-00001",
                    "性能分类",
                    None,
                    None,
                    MANUFACTURER_ID,
                    "CJ-00001",
                    "性能厂家",
                    1_000,
                    None,
                )
            )
    connection.executemany(
        "INSERT INTO stock_documents (id, doc_no, doc_type, status, warehouse_id, "
        "warehouse_code, warehouse_name, department_id, department_code, department_name, "
        "created_source, request_id, request_fingerprint, version, created_by, created_by_name, "
        "submitted_by, submitted_by_name, created_at, updated_at, submitted_at, deleted_at, "
        "deleted_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        document_rows,
    )
    for start in range(0, len(line_rows), 5_000):
        _flush_lines(connection, line_rows[start : start + 5_000])


def _flush_lines(connection: sqlite3.Connection, rows: list[object]) -> None:
    connection.executemany(
        "INSERT INTO stock_document_lines (id, document_id, line_no, material_id, "
        "material_code, material_name, unit, quantity_type, category_code, category_name, "
        "parent_category_code, parent_category_name, manufacturer_id, manufacturer_code, "
        "manufacturer_name, quantity_milli, remark) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def query_plan(engine: Engine, sql: str, parameters: tuple[object, ...] = ()) -> list[str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {sql}", parameters).all()
    return [str(row[-1]) for row in rows]
