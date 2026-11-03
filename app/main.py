"""only-stock 应用入口：生命周期、中间件、错误处理与路由注册。

启动时按 docs/14-安全、可靠性与性能要求.md 11.2 检查数据目录、权限、
PRAGMA、迁移状态和调度器状态；迁移未完成时应用仍可启动，但 /health/ready
返回 503，避免在数据库结构不兼容时提供业务服务。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth.router import api_key_router, auth_router
from app.auth.sessions import purge_expired_sessions
from app.config import Settings
from app.db.sqlite import verify_sqlite_pragmas
from app.db.types import utc_now
from app.error_handlers import register_error_handlers
from app.health import router as health_router
from app.logging import configure_logging
from app.middleware import RequestContextMiddleware
from app.modules.users.service import bootstrap_initial_admin
from app.runtime import Runtime, build_runtime

logger = logging.getLogger(__name__)

APP_NAME = "only-stock"
APP_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging()
    runtime = build_runtime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await asyncio.to_thread(_startup, runtime)
        except BaseException:
            logger.exception("startup failed")
            await asyncio.to_thread(_shutdown, runtime)
            raise
        try:
            yield
        finally:
            await asyncio.to_thread(_shutdown, runtime)

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
    app.state.settings = settings
    app.state.runtime = runtime
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(api_key_router)
    return app


def _startup(runtime: Runtime) -> None:
    runtime.settings.prepare_directories()
    verify_sqlite_pragmas(runtime.engine, runtime.settings.sqlite_busy_timeout_ms)

    if runtime.migration_checker.is_current(runtime.engine):
        created_admin = bootstrap_initial_admin(runtime.session_factory, runtime.settings)
        if created_admin is not None:
            logger.info("initial company admin created")
        _purge_expired_sessions(runtime)
    else:
        logger.warning("database migrations not current; initial admin bootstrap skipped")

    try:
        runtime.scheduler.start()
    except Exception:
        logger.exception("backup scheduler start failed")

    result = runtime.readiness.check()
    if result.ready:
        logger.info(
            "startup complete app_env=%s bootstrap_admin_configured=%s",
            runtime.settings.app_env,
            runtime.settings.bootstrap_admin_username is not None,
        )
    else:
        logger.warning("startup not ready failed=%s", ",".join(result.failed))


def _purge_expired_sessions(runtime: Runtime) -> None:
    try:
        with runtime.session_factory() as session:
            purge_expired_sessions(session, utc_now())
            session.commit()
    except Exception:
        logger.exception("expired session cleanup failed")


def _shutdown(runtime: Runtime) -> None:
    try:
        runtime.scheduler.shutdown()
    except Exception:
        logger.exception("backup scheduler shutdown failed")
    finally:
        runtime.engine.dispose()


app = create_app()
