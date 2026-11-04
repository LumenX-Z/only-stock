"""账户、会话与 API Key 首次迁移的数据库集成测试。"""

from datetime import datetime

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.passwords import hash_password
from app.config import Settings
from app.db.migrations import alembic_config, upgrade_to_head
from app.db.types import utc_now
from app.modules.users.models import Role, User, UserStatus


def test_first_migration_creates_account_tables(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)

    assert {"users", "sessions", "api_keys", "schema_migrations"} <= set(
        inspector.get_table_names()
    )
    assert inspector.get_pk_constraint("users")["constrained_columns"] == ["id"]
    assert inspector.get_pk_constraint("sessions")["constrained_columns"] == ["id_hash"]
    assert inspector.get_pk_constraint("api_keys")["constrained_columns"] == ["id"]


def test_migration_indexes_and_foreign_keys(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)

    user_indexes = {index["name"]: index for index in inspector.get_indexes("users")}
    assert user_indexes["ux_users_login_name"]["unique"] == 1
    assert user_indexes["ux_users_login_name"]["column_names"] == ["login_name"]

    key_indexes = {index["name"]: index for index in inspector.get_indexes("api_keys")}
    assert key_indexes["ux_api_keys_key_hash"]["unique"] == 1
    assert key_indexes["ix_api_keys_user_id_revoked_at"]["column_names"] == [
        "user_id",
        "revoked_at",
    ]

    session_fks = inspector.get_foreign_keys("sessions")
    assert session_fks[0]["referred_table"] == "users"
    assert session_fks[0]["constrained_columns"] == ["user_id"]
    key_fks = inspector.get_foreign_keys("api_keys")
    assert key_fks[0]["referred_table"] == "users"
    assert key_fks[0]["constrained_columns"] == ["user_id"]


def test_schema_enforces_role_status_and_hash_checks(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        users_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).scalar_one()
        sessions_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
        ).scalar_one()

    for fragment in ("ck_users_role", "ck_users_status", "ck_users_force_password_change"):
        assert fragment in users_sql
    for fragment in ("ck_sessions_id_hash_length", "ck_sessions_csrf_hash_length"):
        assert fragment in sessions_sql


def test_foreign_keys_are_enforced_on_account_tables(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO sessions "
                    "(id_hash, user_id, csrf_hash, created_at, last_seen_at, "
                    "absolute_expires_at) "
                    "VALUES (:id_hash, :user_id, :csrf_hash, 0, 0, 1000)"
                ),
                {
                    "id_hash": "a" * 64,
                    "user_id": "missing-user",
                    "csrf_hash": "b" * 64,
                },
            )
        session.rollback()


def test_normalized_login_name_is_unique(
    session_factory: sessionmaker[Session],
) -> None:
    now = utc_now()
    with session_factory() as session:
        session.add(_user("duplicate", now))
        session.commit()

    with session_factory() as session:
        session.add(_user("duplicate", now))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_upgrade_is_idempotent(settings: Settings, migrated_engine: Engine) -> None:
    upgrade_to_head(settings)

    with migrated_engine.connect() as connection:
        versions = (
            connection.exec_driver_sql("SELECT version_num FROM schema_migrations").scalars().all()
        )
    assert versions == ["0003_stock_documents"]


def _user(login_name: str, now: datetime) -> User:
    return User(
        login_name=login_name,
        password_hash=hash_password("duplicate-password-123"),
        display_name=login_name,
        role=Role.OPERATOR.value,
        status=UserStatus.ACTIVE.value,
        force_password_change=False,
        password_version=1,
        version=1,
        created_at=now,
        updated_at=now,
    )


def test_models_and_migration_are_in_sync(settings: Settings, migrated_engine: Engine) -> None:
    command.check(alembic_config(settings))


def test_migration_can_downgrade_and_upgrade(settings: Settings, migrated_engine: Engine) -> None:
    config = alembic_config(settings)
    command.downgrade(config, "base")
    assert "users" not in inspect(migrated_engine).get_table_names()

    command.upgrade(config, "head")
    assert "users" in inspect(migrated_engine).get_table_names()
