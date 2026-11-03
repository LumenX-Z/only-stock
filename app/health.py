"""健康检查路由。

依据 docs/09-HTTP-API通用约定与健康检查.md 8.7：
- /health/live 只表示进程存活，不访问业务数据；
- /health/ready 检查数据库、迁移、数据目录与备份调度，失败不泄露路径或配置。
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="进程存活检查")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", summary="就绪检查")
def ready(request: Request) -> JSONResponse:
    result = request.app.state.runtime.readiness.check()
    if result.ready:
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "checks": dict(result.checks)},
        )
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "failed": list(result.failed)},
    )
