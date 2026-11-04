"""MCP Streamable HTTP 挂载、认证中间件与协议处理。

依据 docs/08-MCP服务与工具.md 8.6.1 与 docs/04-认证、会话与凭证安全.md 5.5：
- 官方 MCP Python SDK 的 Streamable HTTP 与 FastAPI 同应用、同端口、单进程；
- ``/mcp`` 只接受 ``Authorization: Bearer <api_key>``，Cookie 不能代替认证；
- 校验 Host/Origin 白名单、按 Key/IP 限流、限制请求体与响应大小；
- 每次请求重新鉴权，``MCP-Session-Id`` 只是协议传输会话。
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.orm import Session, sessionmaker
from starlette.authentication import AuthCredentials
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.api_keys import authenticate_api_key
from app.config import Settings
from app.db.types import utc_now
from app.errors import error_payload
from app.mcp import tools
from app.mcp.identity import MCPIdentity, reset_identity, set_identity
from app.mcp.rate_limit import MCPRateLimiter
from app.runtime import Runtime
from mcp import types

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"
MCP_PATH = "/mcp"
MCP_MAX_REQUEST_BYTES = 4 * 1024 * 1024
MCP_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MCP_MAX_SESSIONS = 100
MCP_SESSION_IDLE_TIMEOUT = 30 * 60
MCP_INSTRUCTIONS = (
    "only-stock 库存管理 MCP。使用业务编码 code 与单号 doc_no 定位记录；"
    "输入输出不含内部 ID。查询工具对所有启用用户开放，基础资料新增工具仅公司管理员可用，"
    "document_create 对所有启用用户开放。"
)


@dataclass(frozen=True, slots=True)
class MCPApplication:
    """MCP 协议服务器与挂载用的 ASGI 应用。"""

    server: Server[Any]
    asgi_app: ASGIApp
    session_manager: StreamableHTTPSessionManager


def build_mcp(runtime: Runtime) -> MCPApplication:
    """构建 MCP 协议服务；不连接数据库、不注册路由。"""

    async def on_list_tools(
        _context: object, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=await tools.list_tools_for_current_identity(runtime.session_factory)
        )

    async def on_call_tool(
        _context: object, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return await tools.invoke_tool(
            runtime.session_factory,
            runtime.settings,
            params.name,
            params.arguments or {},
        )

    server: Server[Any] = Server(
        "only-stock",
        version=APP_VERSION,
        instructions=MCP_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    streamable_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=False,
        stateless_http=False,
        max_request_body_size=MCP_MAX_REQUEST_BYTES,
        session_idle_timeout=MCP_SESSION_IDLE_TIMEOUT,
        max_sessions=MCP_MAX_SESSIONS,
        transport_security=transport_security(runtime.settings),
    )
    rate_limiter = MCPRateLimiter(
        key_per_minute=runtime.settings.mcp_key_rate_per_minute,
        ip_per_minute=runtime.settings.mcp_ip_rate_per_minute,
    )
    authenticated = MCPAuthenticationMiddleware(
        streamable_app,
        session_factory=runtime.session_factory,
        rate_limiter=rate_limiter,
        allowed_hosts=runtime.settings.mcp_allowed_hosts,
    )
    application = MCPResponseLimitMiddleware(authenticated, MCP_MAX_RESPONSE_BYTES)
    return MCPApplication(
        server=server,
        asgi_app=application,
        session_manager=server.session_manager,
    )


def transport_security(settings: Settings) -> TransportSecuritySettings:
    """由显式白名单构造 DNS rebinding 防护；不使用任意 Host 反射。"""
    allowed_hosts: list[str] = []
    allowed_origins: list[str] = []
    for raw_host in settings.mcp_allowed_hosts:
        host = _host_only(raw_host)
        if ":" in host:  # IPv6 地址在 Host 头中带方括号
            host = f"[{host}]"
        allowed_hosts.extend([host, f"{host}:*"])
        for scheme in ("http", "https"):
            allowed_origins.extend([f"{scheme}://{host}", f"{scheme}://{host}:*"])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


class MCPAuthenticationMiddleware:
    """``/mcp`` 的 Bearer 认证、Host/Origin 校验与限流。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        session_factory: sessionmaker[Session],
        rate_limiter: MCPRateLimiter,
        allowed_hosts: tuple[str, ...],
    ) -> None:
        self.app = app
        self.session_factory = session_factory
        self.rate_limiter = rate_limiter
        self.allowed_hosts = frozenset(_host_only(host) for host in allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        headers = Headers(scope=scope)
        host = headers.get("host")
        if host is None or _host_only(host) not in self.allowed_hosts:
            logger.warning("mcp request rejected reason=host", extra={"request_id": request_id})
            await _reject(scope, send, 421, "INVALID_HOST", request_id)
            return
        origin = headers.get("origin")
        if origin is not None and _origin_host(origin) not in self.allowed_hosts:
            logger.warning("mcp request rejected reason=origin", extra={"request_id": request_id})
            await _reject(scope, send, 403, "INVALID_ORIGIN", request_id)
            return

        ip = _client_ip(scope)
        retry_after = self.rate_limiter.check_and_record_ip(ip)
        if retry_after is not None:
            logger.warning("mcp request rejected reason=ip_rate", extra={"request_id": request_id})
            await _reject(scope, send, 429, "RATE_LIMITED", request_id, retry_after=retry_after)
            return

        if _declared_body_too_large(headers.get("content-length")):
            logger.warning(
                "mcp request rejected reason=body_size", extra={"request_id": request_id}
            )
            await _reject(scope, send, 413, "REQUEST_TOO_LARGE", request_id)
            return

        raw_key = _bearer_token(headers.get("authorization"))
        identity = None if raw_key is None else await asyncio.to_thread(self._authenticate, raw_key)
        if identity is None:
            logger.info(
                "mcp request rejected reason=authentication", extra={"request_id": request_id}
            )
            await _reject(scope, send, 401, "UNAUTHORIZED", request_id)
            return

        retry_after = self.rate_limiter.check_and_record_key(identity.api_key_id)
        if retry_after is not None:
            logger.warning(
                "mcp request rejected reason=key_rate api_key_id=%s",
                identity.api_key_id,
                extra={"request_id": request_id},
            )
            await _reject(scope, send, 429, "RATE_LIMITED", request_id, retry_after=retry_after)
            return

        scope["user"] = AuthenticatedUser(
            AccessToken(
                token=f"mcp-key:{identity.api_key_id}",
                client_id=identity.user_id,
                scopes=[],
                subject=identity.api_key_id,
            )
        )
        scope["auth"] = AuthCredentials(scopes=[])
        token = set_identity(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_identity(token)

    def _authenticate(self, raw_key: str) -> MCPIdentity | None:
        with self.session_factory() as session:
            authenticated = authenticate_api_key(session, raw_key, now=utc_now())
            if authenticated is None:
                return None
            identity = MCPIdentity(
                user_id=str(authenticated.user.id),
                api_key_id=str(authenticated.api_key.id),
            )
            session.commit()
            return identity


class MCPResponseLimitMiddleware:
    """限制单个响应体大小，避免流式响应耗尽单 worker。"""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        total = 0
        exceeded = False
        streaming = False

        async def send_limited(message: Message) -> None:
            nonlocal total, exceeded, streaming
            if exceeded:
                return
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers") or [])
                content_type = headers.get(b"content-type", b"").decode("latin-1")
                streaming = content_type.startswith("text/event-stream")
                declared = headers.get(b"content-length")
                if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
                    exceeded = True
                    logger.error(
                        "mcp response exceeded limit bytes=%s",
                        declared,
                        extra={"request_id": request_id},
                    )
                    await _reject(scope, send, 500, "RESPONSE_TOO_LARGE", request_id)
                    return
                await send(message)
                return
            if message["type"] == "http.response.body":
                size = len(message.get("body") or b"")
                if streaming:
                    # 流式连接按单个数据块限制，不按整条会话累计，避免长连接被误截断。
                    if size > self.max_bytes:
                        exceeded = True
                        logger.error(
                            "mcp stream chunk exceeded limit bytes=%s",
                            size,
                            extra={"request_id": request_id},
                        )
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
                        return
                else:
                    total += size
                    if total > self.max_bytes:
                        exceeded = True
                        logger.error(
                            "mcp response exceeded limit bytes=%s",
                            total,
                            extra={"request_id": request_id},
                        )
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
                        return
            await send(message)

        await self.app(scope, receive, send_limited)


def _declared_body_too_large(content_length: str | None) -> bool:
    if content_length is None:
        return False
    try:
        declared = int(content_length)
    except ValueError:
        return False
    return declared > MCP_MAX_REQUEST_BYTES


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _host_only(value: str) -> str:
    """去掉 Host 或 Origin 中的端口与协议，返回小写主机名。"""
    text = value.strip().lower()
    if "://" in text:
        text = urlsplit(text).hostname or ""
    elif text.startswith("["):
        end = text.find("]")
        text = text[1:end] if end > 0 else text
    else:
        host, separator, port = text.rpartition(":")
        if separator and port.isdigit():
            text = host
    return text


def _origin_host(origin: str) -> str:
    parsed = urlsplit(origin.strip())
    if parsed.scheme not in {"http", "https"}:
        return ""
    return (parsed.hostname or "").lower()


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    if not client:
        return "unknown"
    return str(client[0])


def _request_id(scope: Scope) -> str:
    state = scope.get("state") or {}
    value = state.get("request_id") if isinstance(state, dict) else None
    return str(value) if value else str(uuid4())


async def _reject(
    scope: Scope,
    send: Send,
    status_code: int,
    code: str,
    request_id: str,
    *,
    retry_after: int | None = None,
) -> None:
    message = {
        401: "未认证或认证已失效",
        403: "来源 Origin 不被允许",
        413: "请求体超出大小限制",
        421: "请求 Host 不被允许",
        429: "请求过于频繁，请稍后重试",
        500: "响应超出大小限制",
    }.get(status_code, "请求被拒绝")
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    resource = "api_key" if status_code == 401 else "mcp"
    response = JSONResponse(
        status_code=status_code,
        content=error_payload(code=code, message=message, request_id=request_id, resource=resource),
        headers=headers,
    )
    await response(scope, _empty_receive, send)


async def _empty_receive() -> dict[str, Any]:  # pragma: no cover - JSONResponse 不读取请求体
    return {"type": "http.request", "body": b"", "more_body": False}
