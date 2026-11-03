"""统一 Engine/Session 与请求级数据库会话。

依据 docs/04-认证、会话与凭证安全.md 5.1：每次请求新建数据库会话，
请求结束必须关闭；后台任务使用独立会话。数据库路径只来自服务端配置。
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.sqlite import install_sqlite_pragmas


def create_engine_from_settings(settings: Settings) -> Engine:
    """创建带文档要求 PRAGMA 的 SQLite 引擎。

    ``isolation_level="IMMEDIATE"`` 让普通写事务也从 BEGIN IMMEDIATE 开始；
    单据提交仍需显式使用 ``begin_immediate`` 以便先加锁再读取余额。
    """

    engine = create_engine(
        settings.database_url,
        connect_args={
            "check_same_thread": False,
            "timeout": settings.sqlite_busy_timeout_ms / 1000,
            "isolation_level": "IMMEDIATE",
        },
        pool_pre_ping=True,
    )
    install_sqlite_pragmas(engine, settings.sqlite_busy_timeout_ms)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话，响应结束后关闭。"""
    runtime = request.app.state.runtime
    session = runtime.session_factory()
    try:
        yield session
    finally:
        session.close()
