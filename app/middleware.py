"""请求 ID 与访问日志中间件。

每个 HTTP 请求生成服务端 UUID，写入 ``request.state``、日志上下文和
``X-Request-ID`` 响应头；不记录查询字符串、请求体、Cookie 或 Authorization。
"""

import logging
import time
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.request_context import reset_request_id, set_request_id

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)
        started_at = time.perf_counter()
        status_code: int | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = (time.perf_counter() - started_at) * 1000
            if status_code is not None:
                method = scope.get("method", "-")
                path = scope.get("path", "-")
                log = logger.debug if path.startswith("/health/") else logger.info
                log(
                    "request completed method=%s path=%s status=%s duration_ms=%.1f",
                    method,
                    path,
                    status_code,
                    duration_ms,
                    extra={"request_id": request_id},
                )
            reset_request_id(token)
