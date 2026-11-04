"""MCP 工具结果与错误映射。

依据 docs/08-MCP服务与工具.md 8.6.1、8.6.5 与 docs/09-HTTP-API通用约定与健康检查.md 8.1：
- 成功结果使用 ``structuredContent``，并附 JSON 文本作为兼容展示；
- 业务失败返回 ``isError=true`` 的 MCP 工具错误，错误对象包含
  ``code``、``message``、``request_id``、``details``；
- 任何结果与错误 ``details`` 都不得包含内部 ``id``、``*_id`` 或内部 UUID。
"""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.errors import AppError
from mcp import types

_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_FORBIDDEN_KEYS = frozenset({"id", "request_id", "deleted_by"})


def is_internal_key(key: str) -> bool:
    """内部主键或外键键名（``id``、``*_id``）。"""
    return key in _FORBIDDEN_KEYS or key.endswith("_id")


def is_internal_id(value: object) -> bool:
    return isinstance(value, str) and bool(_UUID_PATTERN.fullmatch(value))


def success(payload: Mapping[str, Any]) -> types.CallToolResult:
    """结构化成功结果；同时返回 JSON 文本供仅支持文本的客户端展示。"""
    body = dict(payload)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=_dump(body))],
        structured_content=body,
        is_error=False,
    )


def tool_error(
    code: str,
    message: str,
    request_id: str,
    *,
    resource: str | None = None,
    items: Sequence[Mapping[str, Any]] | None = None,
) -> types.CallToolResult:
    error = {
        "code": code,
        "message": message,
        "request_id": request_id,
        "details": {
            "resource": resource,
            "items": sanitize_items(items),
        },
    }
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=_dump({"error": error}))],
        structured_content={"error": error},
        is_error=True,
    )


def from_app_error(exc: AppError, request_id: str) -> types.CallToolResult:
    return tool_error(
        exc.code,
        exc.message,
        request_id,
        resource=exc.resource,
        items=exc.items,
    )


def validation_error(
    request_id: str,
    items: Sequence[Mapping[str, Any]],
    *,
    resource: str | None = None,
) -> types.CallToolResult:
    return tool_error(
        "VALIDATION_ERROR",
        "请求参数校验失败",
        request_id,
        resource=resource,
        items=items,
    )


def internal_error(request_id: str) -> types.CallToolResult:
    return tool_error("INTERNAL_ERROR", "服务器内部错误", request_id)


def sanitize_items(items: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """递归剔除内部键与内部 UUID 值，避免细节泄露到工具错误中。"""
    if not items:
        return []
    return [_clean_item(item) for item in items]


def _clean_item(value: object) -> Any:
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if is_internal_key(name) or is_internal_id(item):
                continue
            cleaned[name] = _clean_item(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean_item(item) for item in value]
    return value


def _dump(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
