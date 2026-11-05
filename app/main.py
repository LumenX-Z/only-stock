"""only-stock 应用入口：生命周期、中间件、错误处理与路由注册。

启动时按 docs/14-安全、可靠性与性能要求.md 11.2 检查数据目录、权限、
PRAGMA、迁移状态和调度器状态；迁移未完成时应用仍可启动，但 /health/ready
返回 503，避免在数据库结构不兼容时提供业务服务。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from app.api.openapi import APP_DESCRIPTION, OPENAPI_TAGS, install_openapi_contract
from app.auth.router import api_key_router, auth_router
from app.auth.sessions import purge_expired_sessions
from app.config import Settings
from app.db.sqlite import verify_sqlite_pragmas
from app.db.types import utc_now
from app.error_handlers import register_error_handlers
from app.health import router as health_router
from app.logging import configure_logging
from app.mcp.server import MCP_PATH, build_mcp
from app.middleware import RequestContextMiddleware
from app.migration_gate import MigrationGateMiddleware
from app.modules.audit.router import router as audit_router
from app.modules.backups.router import router as backups_router
from app.modules.documents.router import router as documents_router
from app.modules.inventory.router import router as inventory_router
from app.modules.masterdata.router import router as masterdata_router
from app.modules.users.router import router as users_router
from app.modules.users.service import bootstrap_initial_admin
from app.modules.xlsx.router import router as xlsx_router
from app.pages import pages_router
from app.runtime import Runtime, build_runtime
from app.version import APP_VERSION

logger = logging.getLogger(__name__)

APP_NAME = "only-stock"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging()
    runtime = build_runtime(settings)
    mcp = build_mcp(runtime)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await asyncio.to_thread(_startup, runtime)
        except BaseException:
            logger.exception("startup failed")
            await asyncio.to_thread(_shutdown, runtime)
            raise
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await asyncio.to_thread(_shutdown, runtime)

    app = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        description=APP_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.runtime = runtime
    app.state.mcp = mcp
    app.state.pages_enabled = True
    # 后添加的中间件在最外层；请求 ID 必须覆盖迁移闸门返回的 503。
    app.add_middleware(MigrationGateMiddleware, runtime=runtime)
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    install_openapi_contract(app)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(api_key_router)
    app.include_router(users_router)
    app.include_router(masterdata_router)
    app.include_router(documents_router)
    app.include_router(xlsx_router)
    app.include_router(inventory_router)
    app.include_router(audit_router)
    app.include_router(backups_router)
    app.include_router(pages_router)
    # MCP 与页面/API 同应用、同端口、同进程；不注册独立服务或旧版 HTTP+SSE 端点。
    app.router.routes.append(Route(MCP_PATH, endpoint=mcp.asgi_app))
    return app


def _startup(runtime: Runtime) -> None:
    runtime.settings.prepare_directories()
    runtime.app_lock.acquire()
    verify_sqlite_pragmas(runtime.engine, runtime.settings.sqlite_busy_timeout_ms)

    runtime.migrations_current = runtime.migration_checker.is_current(runtime.engine)
    if runtime.migrations_current:
        created_admin = bootstrap_initial_admin(runtime.session_factory, runtime.settings)
        if created_admin is not None:
            logger.info("initial company admin created")
        _purge_expired_sessions(runtime)
    else:
        logger.warning("database migrations not current; initial admin bootstrap skipped")

    if runtime.migrations_current:
        try:
            runtime.scheduler.start()
        except Exception:
            logger.exception("backup scheduler start failed")
    else:
        logger.warning("backup scheduler not started while migrations are not current")

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
        try:
            runtime.engine.dispose()
        finally:
            runtime.app_lock.release()


app = create_app()
