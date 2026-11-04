"""SQLite 连接、PRAGMA、写事务与锁冲突处理。

依据 docs/02-技术架构与代码组织.md 3.4：
- 每个新连接执行 foreign_keys=ON、journal_mode=WAL、synchronous=NORMAL、busy_timeout=5000；
- 单据提交显式使用 BEGIN IMMEDIATE，不得依赖 ORM 隐式提交；
- 捕获 SQLITE_BUSY/SQLITE_LOCKED 后回滚并返回明确冲突。
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.errors import DatabaseBusyError

_BUSY_ERROR_NAMES = frozenset({"SQLITE_BUSY", "SQLITE_LOCKED"})
_BUSY_ERROR_CODES = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})


def install_sqlite_pragmas(engine: Engine, busy_timeout_ms: int) -> None:
    """为引擎的每个连接安装文档要求的 PRAGMA。"""

    def on_connect(dbapi_connection: Any, _connection_record: Any) -> None:
        _apply_pragmas(dbapi_connection, busy_timeout_ms)

    event.listen(engine, "connect", on_connect)


def verify_sqlite_pragmas(engine: Engine, busy_timeout_ms: int) -> None:
    """启动自检；任何一项不符合即抛错，避免带着错误连接配置提供服务。"""
    with engine.connect() as connection:
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
        synchronous = connection.exec_driver_sql("PRAGMA synchronous").scalar()
        busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()

    if foreign_keys != 1:
        raise RuntimeError("SQLite 启动自检失败：foreign_keys 未启用")
    if str(journal_mode).lower() != "wal":
        raise RuntimeError("SQLite 启动自检失败：journal_mode 不是 WAL")
    if synchronous != 1:
        raise RuntimeError("SQLite 启动自检失败：synchronous 不是 NORMAL")
    if busy_timeout != busy_timeout_ms:
        raise RuntimeError("SQLite 启动自检失败：busy_timeout 与配置不一致")


@contextmanager
def begin_immediate(session: Session) -> Iterator[Session]:
    """显式取得 SQLite 写锁的事务边界。

    必须是会话中的第一条语句：调用方先进入本上下文，再在事务内读取库存、
    校验并写入，避免以加锁前的旧余额为依据。异常时整体回滚。
    """
    if session.in_transaction():
        raise RuntimeError("BEGIN IMMEDIATE 必须是会话中的第一条语句")

    try:
        session.execute(text("BEGIN IMMEDIATE"))
        yield session
        session.commit()
    except OperationalError as exc:
        _safe_rollback(session)
        if is_busy_error(exc):
            raise DatabaseBusyError() from exc
        raise
    except BaseException:
        _safe_rollback(session)
        raise


def is_busy_error(exc: OperationalError) -> bool:
    """识别 SQLITE_BUSY/SQLITE_LOCKED，兼容不同 Python/sqlite3 版本。"""
    original = exc.orig
    if original is None:
        return False

    error_name = getattr(original, "sqlite_errorname", None)
    if error_name in _BUSY_ERROR_NAMES:
        return True

    error_code = getattr(original, "sqlite_errorcode", None)
    if isinstance(error_code, int) and (error_code & 0xFF) in _BUSY_ERROR_CODES:
        return True

    message = str(original).lower()
    return "database is locked" in message or "database table is locked" in message


def _apply_pragmas(dbapi_connection: Any, busy_timeout_ms: int) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        cursor.execute("PRAGMA temp_store=MEMORY")
    finally:
        cursor.close()


def _safe_rollback(session: Session) -> None:
    if session.in_transaction():
        session.rollback()
