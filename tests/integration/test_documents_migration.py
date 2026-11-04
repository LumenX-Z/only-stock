"""单据表、视图、索引与数据库保护触发器迁移测试。

依据 docs/05-数据模型与实体约束.md 6.5 与 docs/06-库存计算与单据业务流程.md 7.1：
已生效单据/明细禁止更新或物理删除，至少一行、厂家、项目与整数数量规则由
触发器兜底；库存视图只统计已生效且未软删除的单据。
"""

from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.migrations import alembic_config
from app.modules.documents.inputs import DocumentInput, DocumentLineInput
from app.modules.documents.service import (
    create_draft,
    soft_delete_document,
    submit_document,
)
from tests.conftest import MasterDataFixture

_NOW_MS = 1_700_000_000_000


def _document_row(
    master: MasterDataFixture,
    *,
    doc_id: str | None = None,
    doc_no: str | None = None,
    doc_type: str = "INBOUND",
    status: str = "DRAFT",
    submitted: bool = False,
) -> dict[str, object]:
    identifier = doc_id or str(uuid4())
    return {
        "id": identifier,
        "doc_no": doc_no or f"RK{identifier[:8]}",
        "doc_type": doc_type,
        "status": status,
        "warehouse_id": master.warehouse.id,
        "warehouse_code": master.warehouse.code,
        "warehouse_name": master.warehouse.name,
        "department_id": master.department.id,
        "department_code": master.department.code,
        "department_name": master.department.name,
        "created_source": "API",
        "request_id": str(uuid4()),
        "request_fingerprint": "0" * 64,
        "version": 1,
        "created_by": master.actor.id,
        "created_by_name": master.actor.display_name,
        "submitted_by": master.actor.id if submitted else None,
        "submitted_by_name": master.actor.display_name if submitted else None,
        "created_at": _NOW_MS,
        "updated_at": _NOW_MS,
        "submitted_at": _NOW_MS if submitted else None,
    }


_INSERT_DOCUMENT = text(
    """
    INSERT INTO stock_documents (
        id, doc_no, doc_type, status,
        warehouse_id, warehouse_code, warehouse_name,
        department_id, department_code, department_name,
        created_source, request_id, request_fingerprint, version,
        created_by, created_by_name, submitted_by, submitted_by_name,
        created_at, updated_at, submitted_at, deleted_at, deleted_by
    ) VALUES (
        :id, :doc_no, :doc_type, :status,
        :warehouse_id, :warehouse_code, :warehouse_name,
        :department_id, :department_code, :department_name,
        :created_source, :request_id, :request_fingerprint, :version,
        :created_by, :created_by_name, :submitted_by, :submitted_by_name,
        :created_at, :updated_at, :submitted_at, NULL, NULL
    )
    """
)


def _line_values(
    master: MasterDataFixture,
    document_id: str,
    *,
    line_no: int,
    project_id: str | None = None,
    manufacturer_id: str | None = "default",
    material_id: str | None = None,
    quantity_milli: int = 1000,
) -> dict[str, object]:
    material = master.material if material_id is None else None
    return {
        "id": str(uuid4()),
        "document_id": document_id,
        "line_no": line_no,
        "material_id": material_id or master.material.id,
        "material_code": material.code if material else master.material_integer.code,
        "material_name": material.name if material else master.material_integer.name,
        "unit": material.unit if material else master.material_integer.unit,
        "quantity_type": material.quantity_type if material else "INTEGER",
        "category_code": master.category_child.code,
        "category_name": master.category_child.name,
        "parent_category_code": master.category.code,
        "parent_category_name": master.category.name,
        "manufacturer_id": (
            master.manufacturer.id if manufacturer_id == "default" else manufacturer_id
        ),
        "manufacturer_code": (master.manufacturer.code if manufacturer_id == "default" else None),
        "manufacturer_name": (master.manufacturer.name if manufacturer_id == "default" else None),
        "project_id": project_id,
        "project_code": None,
        "project_name": None,
        "quantity_milli": quantity_milli,
        "remark": None,
    }


_INSERT_LINE = text(
    """
    INSERT INTO stock_document_lines (
        id, document_id, line_no, material_id, material_code, material_name,
        unit, quantity_type, category_code, category_name,
        parent_category_code, parent_category_name,
        manufacturer_id, manufacturer_code, manufacturer_name,
        project_id, project_code, project_name, quantity_milli, remark
    ) VALUES (
        :id, :document_id, :line_no, :material_id, :material_code, :material_name,
        :unit, :quantity_type, :category_code, :category_name,
        :parent_category_code, :parent_category_name,
        :manufacturer_id, :manufacturer_code, :manufacturer_name,
        :project_id, :project_code, :project_name, :quantity_milli, :remark
    )
    """
)


def test_document_tables_columns_indexes_view_and_triggers_exist(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)
    tables = set(inspector.get_table_names())
    assert {"stock_documents", "stock_document_lines", "document_counters"} <= tables
    assert "v_inventory_balance" in inspector.get_view_names()

    document_indexes = {index["name"]: index for index in inspector.get_indexes("stock_documents")}
    assert document_indexes["ux_stock_documents_doc_no"]["unique"] == 1
    assert document_indexes["ux_stock_documents_creator_request"]["unique"] == 1
    assert document_indexes["ix_stock_documents_creator_fingerprint_created"]["column_names"] == [
        "created_by",
        "request_fingerprint",
        "created_at",
    ]
    assert document_indexes["ix_stock_documents_status_warehouse"]["column_names"] == [
        "status",
        "warehouse_id",
    ]

    line_indexes = {index["name"]: index for index in inspector.get_indexes("stock_document_lines")}
    assert line_indexes["ux_stock_document_lines_document_material_project"]["unique"] == 1
    assert line_indexes["ux_stock_document_lines_document_material"]["unique"] == 1
    assert {
        "ix_stock_document_lines_document_id",
        "ix_stock_document_lines_material_id",
        "ix_stock_document_lines_project_id",
    } <= set(line_indexes)

    with migrated_engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        document_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(stock_documents)")
        }
        line_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(stock_document_lines)")
        }
        view_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'view' AND name = 'v_inventory_balance'"
        ).scalar_one()
    assert "deleted_at" in document_columns
    assert "deleted_by" in document_columns
    assert "request_fingerprint" in document_columns
    assert "remark" not in document_columns
    assert "quantity_milli" in line_columns
    assert "remark" in line_columns
    assert "project_name" in line_columns
    assert "manufacturer_name" in line_columns
    assert "document_counters" in tables
    assert {
        "trg_stock_documents_identity_immutable",
        "trg_stock_documents_posted_immutable",
        "trg_stock_documents_posted_no_delete",
        "trg_stock_documents_posted_requires_lines",
        "trg_stock_document_lines_posted_no_update",
        "trg_stock_document_lines_posted_no_delete",
        "trg_stock_document_lines_manufacturer_insert",
        "trg_stock_document_lines_integer_quantity_insert",
        "trg_stock_document_lines_project_active_insert",
    } <= triggers
    assert "deleted_at IS NULL" in view_sql


def test_posted_documents_and_lines_are_immutable_and_not_physically_deleted(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
) -> None:
    document = create_draft(
        session_factory,
        DocumentInput(
            doc_type="INBOUND",
            warehouse_id=master_data.warehouse.id,
            department_id=master_data.department.id,
            lines=(
                DocumentLineInput(
                    material_id=master_data.material.id,
                    quantity="1.5",
                    manufacturer_id=master_data.manufacturer.id,
                ),
            ),
        ),
        actor=master_data.actor,
        request_id=str(uuid4()),
        timezone=settings.timezone,
    ).document
    submit_document(session_factory, document.id, version=1, actor=master_data.actor)

    expected_errors = (
        (
            "UPDATE stock_documents SET warehouse_name = '篡改' WHERE id = :id",
            "posted stock document is immutable",
        ),
        (
            "UPDATE stock_document_lines SET quantity_milli = 2000 WHERE document_id = :id",
            "posted stock document lines are immutable",
        ),
        (
            "DELETE FROM stock_document_lines WHERE document_id = :id",
            "posted stock document lines cannot be deleted",
        ),
        (
            "DELETE FROM stock_documents WHERE id = :id",
            "posted stock document cannot be deleted",
        ),
    )
    for statement, message in expected_errors:
        with session_factory() as session:
            with pytest.raises(IntegrityError, match=message):
                session.execute(text(statement), {"id": document.id})
            session.rollback()


def test_identity_fields_cannot_be_changed_on_drafts(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    values = _document_row(master_data)
    with session_factory() as session:
        session.execute(_INSERT_DOCUMENT, values)
        session.commit()

    for column, value in (
        ("created_source", "MCP"),
        ("request_fingerprint", "f" * 64),
        ("doc_no", "RK99999999-999999"),
        ("doc_type", "OUTBOUND"),
    ):
        with session_factory() as session:
            with pytest.raises(IntegrityError, match="identity is immutable"):
                session.execute(
                    text(f"UPDATE stock_documents SET {column} = :value WHERE id = :id"),
                    {"value": value, "id": values["id"]},
                )
            session.rollback()


def test_at_least_one_line_constraint_on_posted_transition(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    values = _document_row(master_data)
    with session_factory() as session:
        session.execute(_INSERT_DOCUMENT, values)
        session.commit()

    with session_factory() as session:
        with pytest.raises(IntegrityError, match="requires at least one line"):
            session.execute(
                text(
                    "UPDATE stock_documents SET status = 'POSTED', "
                    "submitted_by = :submitted_by, submitted_by_name = :name, "
                    "submitted_at = :submitted_at, version = 2 WHERE id = :id"
                ),
                {
                    "submitted_by": master_data.actor.id,
                    "name": master_data.actor.display_name,
                    "submitted_at": _NOW_MS,
                    "id": values["id"],
                },
            )
        session.rollback()


def test_line_remark_length_is_enforced_by_database(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    values = _document_row(master_data)
    with session_factory() as session:
        session.execute(_INSERT_DOCUMENT, values)
        line_values = _line_values(master_data, str(values["id"]), line_no=1)
        line_values["remark"] = "备" * 501
        with pytest.raises(IntegrityError, match="ck_stock_document_lines_remark_length"):
            session.execute(_INSERT_LINE, line_values)
        session.rollback()


def test_partial_unique_indexes_allow_same_material_across_projects(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    values = _document_row(master_data)
    with session_factory() as session:
        session.execute(_INSERT_DOCUMENT, values)
        document_id = str(values["id"])
        session.execute(
            _INSERT_LINE,
            _line_values(
                master_data,
                document_id,
                line_no=1,
                project_id=master_data.project.id,
            ),
        )
        session.commit()

    with session_factory() as session:
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
            session.execute(
                _INSERT_LINE,
                _line_values(
                    master_data,
                    document_id,
                    line_no=2,
                    project_id=master_data.project.id,
                ),
            )
        session.rollback()

    with session_factory() as session:
        session.execute(
            _INSERT_LINE,
            _line_values(
                master_data,
                document_id,
                line_no=2,
                project_id=master_data.project_second.id,
            ),
        )
        session.commit()

    with session_factory() as session:
        session.execute(
            _INSERT_LINE,
            _line_values(master_data, document_id, line_no=3, project_id=None),
        )
        session.commit()

    with session_factory() as session:
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
            session.execute(
                _INSERT_LINE,
                _line_values(master_data, document_id, line_no=4, project_id=None),
            )
        session.rollback()


def test_line_triggers_enforce_manufacturer_quantity_and_project_rules(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    inbound = _document_row(master_data)
    outbound = _document_row(master_data, doc_type="OUTBOUND")
    with session_factory() as session:
        session.execute(_INSERT_DOCUMENT, inbound)
        session.execute(_INSERT_DOCUMENT, outbound)
        session.commit()

    with session_factory() as session:
        with pytest.raises(IntegrityError, match="manufacturer rule violated"):
            session.execute(
                _INSERT_LINE,
                _line_values(
                    master_data,
                    str(inbound["id"]),
                    line_no=1,
                    manufacturer_id=None,
                ),
            )
        session.rollback()

    with session_factory() as session:
        with pytest.raises(IntegrityError, match="manufacturer rule violated"):
            session.execute(
                _INSERT_LINE,
                _line_values(
                    master_data,
                    str(outbound["id"]),
                    line_no=1,
                    manufacturer_id=master_data.manufacturer.id,
                ),
            )
        session.rollback()

    with session_factory() as session:
        with pytest.raises(IntegrityError, match="integer material requires"):
            session.execute(
                _INSERT_LINE,
                _line_values(
                    master_data,
                    str(inbound["id"]),
                    line_no=1,
                    material_id=master_data.material_integer.id,
                    quantity_milli=1500,
                ),
            )
        session.rollback()

    with session_factory() as session:
        project_disable = master_data.project
        session.execute(
            text("UPDATE projects SET is_active = 0 WHERE id = :id"),
            {"id": project_disable.id},
        )
        session.commit()

    with session_factory() as session:
        with pytest.raises(IntegrityError, match="project is not active"):
            session.execute(
                _INSERT_LINE,
                _line_values(
                    master_data,
                    str(inbound["id"]),
                    line_no=1,
                    project_id=master_data.project.id,
                ),
            )
        session.rollback()


def test_inventory_view_counts_only_posted_and_not_deleted(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
) -> None:
    draft = create_draft(
        session_factory,
        DocumentInput(
            doc_type="INBOUND",
            warehouse_id=master_data.warehouse.id,
            department_id=master_data.department.id,
            lines=(
                DocumentLineInput(
                    material_id=master_data.material.id,
                    quantity="2.5",
                    manufacturer_id=master_data.manufacturer.id,
                ),
            ),
        ),
        actor=master_data.actor,
        request_id=str(uuid4()),
        timezone=settings.timezone,
    )

    def balance() -> int:
        with session_factory() as session:
            value = session.execute(
                text(
                    "SELECT balance_milli FROM v_inventory_balance "
                    "WHERE warehouse_id = :warehouse_id AND material_id = :material_id"
                ),
                {
                    "warehouse_id": master_data.warehouse.id,
                    "material_id": master_data.material.id,
                },
            ).scalar()
            return int(value or 0)

    assert balance() == 0
    submit_document(session_factory, draft.document.id, version=1, actor=master_data.actor)
    assert balance() == 2500
    deleted = soft_delete_document(session_factory, draft.document.id, actor=master_data.actor)
    assert deleted.document.deleted_at is not None
    assert balance() == 0


def test_line_remark_round_trips_through_downgrade_and_upgrade(
    settings: Settings,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    document = create_draft(
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
                    remark="迁移备注",
                ),
            ),
        ),
        actor=master_data.actor,
        request_id=str(uuid4()),
        timezone=settings.timezone,
    ).document

    config = alembic_config(settings)
    command.downgrade(config, "0003_stock_documents")
    with session_factory() as session:
        header_remark = session.execute(
            text("SELECT remark FROM stock_documents WHERE id = :id"),
            {"id": document.id},
        ).scalar_one()
    assert header_remark == "迁移备注"

    command.upgrade(config, "head")
    with session_factory() as session:
        line_remark = session.execute(
            text("SELECT remark FROM stock_document_lines WHERE document_id = :id"),
            {"id": document.id},
        ).scalar_one()
    assert line_remark == "迁移备注"
