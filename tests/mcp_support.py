"""MCP 测试支持：标准协议客户端与断言辅助。

依据 docs/15-测试方案.md 12.1、12.2：标准协议客户端覆盖初始化、``MCP-Session-Id``、
``tools/list``、``tools/call`` 与错误响应；测试使用独立临时数据库。
"""

import contextlib
import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx2
from fastapi import FastAPI
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp import types

MCP_URL = "http://127.0.0.1:8000/mcp"
MCP_BASE_URL = "http://127.0.0.1:8000"
MCP_HOST = "127.0.0.1:8000"
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@contextlib.asynccontextmanager
async def mcp_sessions(
    app: FastAPI, keys: Mapping[str, str]
) -> AsyncIterator[dict[str, ClientSession]]:
    """在同一应用生命周期内建立多个已初始化的标准 MCP 会话。"""
    async with app.router.lifespan_context(app):
        async with contextlib.AsyncExitStack() as stack:
            sessions: dict[str, ClientSession] = {}
            for name, raw_key in keys.items():
                http_client = await stack.enter_async_context(
                    httpx2.AsyncClient(
                        transport=httpx2.ASGITransport(app=app),
                        base_url=MCP_BASE_URL,
                        headers={"Authorization": f"Bearer {raw_key}"},
                    )
                )
                read_stream, write_stream = await stack.enter_async_context(
                    streamable_http_client(MCP_URL, http_client=http_client)
                )
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await session.initialize()
                sessions[name] = session
            yield sessions


async def call_tool(
    session: ClientSession, name: str, arguments: Mapping[str, Any] | None = None
) -> types.CallToolResult:
    return await session.call_tool(name, dict(arguments or {}))


def structured(result: types.CallToolResult) -> dict[str, Any]:
    content = result.structured_content
    assert content is not None, result
    return dict(content)


def tool_error(result: types.CallToolResult) -> dict[str, Any]:
    """取出 ``isError=true`` 的结构化错误对象，并校验协议字段。"""
    assert result.is_error is True, result
    payload = structured(result)
    error = payload["error"]
    assert set(error) == {"code", "message", "request_id", "details"}
    assert isinstance(error["request_id"], str) and error["request_id"]
    assert set(error["details"]) == {"resource", "items"}
    return error


def assert_no_internal_ids(value: object, path: str = "result") -> None:
    """递归扫描结果键名与 UUID 值，确保不泄露内部标识。"""
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            # request_id 只允许作为错误对象的关联标识，不是实体内部 ID。
            if name == "request_id":
                continue
            assert not name.endswith("_id"), f"内部键 {path}.{name}"
            assert_no_internal_ids(item, f"{path}.{name}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            assert_no_internal_ids(item, f"{path}[{index}]")
        return
    assert not (isinstance(value, str) and _UUID.fullmatch(value)), f"内部 UUID {path}={value}"


def initialize_body() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "only-stock-tests", "version": "0.0.0"},
        },
    }


def tool_names(tools: Sequence[types.Tool]) -> list[str]:
    return [tool.name for tool in tools]


def tool_by_name(tools: Sequence[types.Tool], name: str) -> types.Tool:
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"缺少工具 {name}")


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
