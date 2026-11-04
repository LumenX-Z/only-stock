"""MCP 限流、传输安全与响应大小单元测试。"""

import asyncio
from typing import Any

from app.config import Settings
from app.mcp.rate_limit import RATE_WINDOW_SECONDS, MCPRateLimiter
from app.mcp.server import (
    MCP_MAX_RESPONSE_BYTES,
    MCPResponseLimitMiddleware,
    transport_security,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _limiter() -> tuple[MCPRateLimiter, _Clock]:
    clock = _Clock()
    return (
        MCPRateLimiter(key_per_minute=2, ip_per_minute=3, clock=clock),
        clock,
    )


def test_ip_limit_is_a_sliding_window() -> None:
    limiter, clock = _limiter()

    assert limiter.check_and_record_ip("10.0.0.1") is None
    assert limiter.check_and_record_ip("10.0.0.1") is None
    assert limiter.check_and_record_ip("10.0.0.1") is None
    retry = limiter.check_and_record_ip("10.0.0.1")
    assert retry == RATE_WINDOW_SECONDS

    clock.advance(RATE_WINDOW_SECONDS + 1)
    assert limiter.check_and_record_ip("10.0.0.1") is None
    assert limiter.check_and_record_ip("10.0.0.2") is None


def test_key_limit_is_independent_from_ip_limit() -> None:
    limiter, _clock = _limiter()

    assert limiter.check_and_record_key("key-a") is None
    assert limiter.check_and_record_key("key-a") is None
    assert limiter.check_and_record_key("key-a") == RATE_WINDOW_SECONDS
    assert limiter.check_and_record_key("key-b") is None


def test_transport_security_uses_explicit_allowlists() -> None:
    settings = Settings.from_env({"MCP_ALLOWED_HOSTS": "127.0.0.1,localhost"})

    security = transport_security(settings)

    assert security.enable_dns_rebinding_protection is True
    assert "127.0.0.1" in security.allowed_hosts
    assert "127.0.0.1:*" in security.allowed_hosts
    assert "http://127.0.0.1:*" in security.allowed_origins
    assert "*" not in security.allowed_hosts
    assert settings.mcp_allowed_hosts == ("127.0.0.1", "localhost")


class _FakeApp:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        content_length: int | None = None,
        content_type: bytes = b"application/json",
    ) -> None:
        self.chunks = chunks
        self.content_length = content_length
        self.content_type = content_type

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        headers = [(b"content-type", self.content_type)]
        if self.content_length is not None:
            headers.append((b"content-length", str(self.content_length).encode()))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        for chunk in self.chunks:
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _collect(app: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [],
        "state": {"request_id": "test-request"},
    }
    await app(scope, receive, send)
    return messages


def test_declared_oversized_response_is_rejected() -> None:
    inner = _FakeApp([b"x"], content_length=MCP_MAX_RESPONSE_BYTES + 1)
    messages = asyncio.run(_collect(MCPResponseLimitMiddleware(inner, MCP_MAX_RESPONSE_BYTES)))

    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 500
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    assert b"RESPONSE_TOO_LARGE" in body


def test_streamed_json_response_is_capped() -> None:
    inner = _FakeApp([b"x" * 10, b"x" * 10, b"x" * 10], content_type=b"application/json")
    messages = asyncio.run(_collect(MCPResponseLimitMiddleware(inner, 15)))

    bodies = [message for message in messages if message["type"] == "http.response.body"]
    forwarded = b"".join(message.get("body", b"") for message in bodies)
    assert len(forwarded) <= 15
    assert bodies[-1]["more_body"] is False


def test_sse_stream_is_bounded_per_chunk_not_cumulatively() -> None:
    inner = _FakeApp([b"x" * 10, b"x" * 10, b"x" * 10], content_type=b"text/event-stream")
    messages = asyncio.run(_collect(MCPResponseLimitMiddleware(inner, 15)))

    bodies = [message for message in messages if message["type"] == "http.response.body"]
    assert len(b"".join(message.get("body", b"") for message in bodies)) == 30
    assert bodies[-1]["more_body"] is False

    oversized = _FakeApp([b"x" * 16], content_type=b"text/event-stream")
    messages = asyncio.run(_collect(MCPResponseLimitMiddleware(oversized, 15)))
    bodies = [message for message in messages if message["type"] == "http.response.body"]
    assert bodies[-1]["more_body"] is False
    assert len(bodies[-1].get("body", b"")) == 0
