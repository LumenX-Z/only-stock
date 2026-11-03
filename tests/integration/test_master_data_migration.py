"""基础资料、编码计数器与审计表的迁移集成测试。"""

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.modules.masterdata.registry import SPECS


def test_master_data_tables_and_columns_exist(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    tables = set(inspector.get_table_names())

    assert {
        "warehouses",
        "departments",
        "categories",
        "materials",
        "projects",
        "manufacturers",
        "code_counters",
        "audit_logs",
    } <= tables
    for spec in SPECS:
        columns = {column["name"] for column in inspector.get_columns(spec.table)}
        assert {"id", "code", "name", "is_active", "version", "created_at", "updated_at"} <= (
            columns
        )
        assert "code" in columns


def test_all_six_codes_have_unique_indexes(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)

    for spec in SPECS:
        indexes = {index["name"]: index for index in inspector.get_indexes(spec.table)}
        assert indexes[f"ux_{spec.table}_code"]["unique"] == 1

    category_indexes = {index["name"]: index for index in inspector.get_indexes("categories")}
    assert category_indexes["ux_categories_name_level1"]["unique"] == 1
    assert category_indexes["ux_categories_name_level1"]["column_names"] == ["name"]
    assert category_indexes["ux_categories_parent_name_level2"]["column_names"] == [
        "parent_id",
        "name",
    ]
    material_indexes = {index["name"]: index for index in inspector.get_indexes("materials")}
    assert material_indexes["ux_materials_category_name"]["unique"] == 1


def test_partial_unique_indexes_enforce_category_name_scope(
    migrated_engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    with migrated_engine.connect() as connection:
        for index_name in ("ux_categories_name_level1", "ux_categories_parent_name_level2"):
            sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name",
                {"name": index_name},
            ).scalar_one()
            assert "WHERE" in sql

    with session_factory() as session:
        for category_id, code, name, parent_id, level in (
            ("c1", "FL-00001", "一级 A", None, 1),
            ("c2", "FL-00002", "子类", "c1", 2),
            ("c3", "FL-00003", "一级 B", None, 1),
            ("c4", "FL-00004", "子类", "c3", 2),
        ):
            session.execute(
                text(
                    "INSERT INTO categories (id, code, name, is_active, version, "
                    "created_at, updated_at, parent_id, level) "
                    "VALUES (:id, :code, :name, 1, 1, 0, 0, :parent_id, :level)"
                ),
                {
                    "id": category_id,
                    "code": code,
                    "name": name,
                    "parent_id": parent_id,
                    "level": level,
                },
            )
        session.commit()

    with session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO categories (id, code, name, is_active, version, "
                    "created_at, updated_at, parent_id, level) "
                    "VALUES ('c5', 'FL-00005', '一级 A', 1, 1, 0, 0, NULL, 1)"
                )
            )
        session.rollback()

    with session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO categories (id, code, name, is_active, version, "
                    "created_at, updated_at, parent_id, level) "
                    "VALUES ('c6', 'FL-00006', '子类', 1, 1, 0, 0, 'c1', 2)"
                )
            )
        session.rollback()


def test_category_level_check_constraint(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'categories'"
        ).scalar_one()
    assert "ck_categories_level" in sql
    assert "ck_categories_parent_level" in sql


def test_foreign_keys_are_enforced_for_categories_and_materials(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO materials (id, code, name, is_active, version, "
                    "created_at, updated_at, category_id, unit, quantity_type) "
                    "VALUES ('m1', 'WZ-00001', '物资', 1, 1, 0, 0, 'missing', '个', 'DECIMAL')"
                )
            )
        session.rollback()


def test_audit_logs_have_append_only_triggers(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        triggers = set(
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).scalars()
        )
    assert {"trg_audit_logs_no_update", "trg_audit_logs_no_delete"} <= triggers


def test_models_metadata_contains_all_p3_tables() -> None:
    assert {
        "warehouses",
        "departments",
        "categories",
        "materials",
        "projects",
        "manufacturers",
        "code_counters",
        "audit_logs",
    } <= set(Base.metadata.tables)
