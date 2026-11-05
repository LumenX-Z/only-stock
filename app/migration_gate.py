"""迁移未完成时的业务请求闸门。

依据 docs/09-HTTP-API通用约定与健康检查.md 8.7 与
docs/13-Docker部署与运维.md 10.5：数据库未处于单一迁移 head 时，
就绪检查失败且应用不得继续提供可能损坏数据的业务请求。健康检查与静态资源
保持可用，便于运维定位；所有页面、``/api/v1`` 与 ``/mcp`` 请求返回 503。
"""

import json
from uuid import uuid4

from starlette.types import ASGIApp, Receive, Scope, Send

from app.errors import error_payload

_ALLOWED_PREFIXES = ("/health/", "/static/")
_ALLOWED_PATHS = frozenset({"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"})


class MigrationGateMiddleware:
    def __init__(self, app: ASGIApp, *, runtime: object) -> None:
        self.app = app
        self._runtime = runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._runtime.migrations_current:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in _ALLOWED_PATHS or path.startswith(_ALLOWED_PREFIXES):
            await self.app(scope, receive, send)
            return

        request_id = str((scope.get("state") or {}).get("request_id") or uuid4())
        body = json.dumps(
            error_payload(
                code="SYSTEM_NOT_READY",
                message="数据库迁移未完成，服务暂不可用",
                request_id=request_id,
            ),
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
