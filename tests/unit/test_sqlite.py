from collections.abc import Iterator
from dataclasses import replace

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.database import create_engine_from_settings, create_session_factory
from app.db.sqlite import begin_immediate, verify_sqlite_pragmas
from app.errors import DatabaseBusyError


@pytest.fixture
def session_factory(db_engine: Engine) -> Iterator[sessionmaker[Session]]:
    _create_table(db_engine)
    yield create_session_factory(db_engine)


def _create_table(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.commit()


def test_each_connection_uses_documented_pragmas(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert connection.exec_driver_sql("PRAGMA synchronous").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000

    verify_sqlite_pragmas(db_engine, 5000)


def test_verify_pragmas_detects_busy_timeout_mismatch(db_engine: Engine) -> None:
    with pytest.raises(RuntimeError, match="busy_timeout"):
        verify_sqlite_pragmas(db_engine, 1234)


def test_begin_immediate_commits(session_factory: Iterator) -> None:
    with session_factory() as session:
        with begin_immediate(session):
            session.execute(text("INSERT INTO items (value) VALUES ('committed')"))

    with session_factory() as session:
        assert session.execute(text("SELECT COUNT(*) FROM items")).scalar_one() == 1


def test_begin_immediate_rolls_back_on_error(session_factory: Iterator) -> None:
    class BoomError(Exception):
        pass

    with session_factory() as session:
        with pytest.raises(BoomError):
            with begin_immediate(session):
                session.execute(text("INSERT INTO items (value) VALUES ('rolled-back')"))
                raise BoomError

    with session_factory() as session:
        assert session.execute(text("SELECT COUNT(*) FROM items")).scalar_one() == 0


def test_begin_immediate_requires_a_fresh_session(session_factory: Iterator) -> None:
    with session_factory() as session:
        session.execute(text("SELECT 1"))
        with pytest.raises(RuntimeError, match="第一条语句"):
            with begin_immediate(session):
                pass


def test_sqlite_busy_is_mapped_to_write_conflict(settings: Settings) -> None:
    settings.prepare_directories()
    engine = create_engine_from_settings(replace(settings, sqlite_busy_timeout_ms=50))
    try:
        _create_table(engine)
        session_factory = create_session_factory(engine)

        with session_factory() as holder:
            with begin_immediate(holder):
                holder.execute(text("INSERT INTO items (value) VALUES ('locked')"))
                with session_factory() as contender:
                    with pytest.raises(DatabaseBusyError):
                        with begin_immediate(contender):
                            pass
    finally:
        engine.dispose()


def test_session_closes_without_leaking_connections(db_engine: Engine) -> None:
    session_factory = create_session_factory(db_engine)
    session: Session = session_factory()
    session.execute(text("SELECT 1"))
    session.close()

    assert not session.in_transaction()
