"""OpenAPI 元数据与契约补全。

依据 docs/09-HTTP-API通用约定与健康检查.md 8.1：OpenAPI 必须可生成并与
实现一致，包含会话 Cookie 安全方案、统一错误响应和每个响应的请求标识头。
"""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

APP_DESCRIPTION = (
    "only-stock 单一公司库存管理系统 HTTP API。"
    "认证使用会话 Cookie；除 `/auth/login` 与健康检查外均需认证，"
    "写请求还需 `X-CSRF-Token`。错误响应统一为 "
    '`{"error": {"code", "message", "request_id", "details"}}`，'
    "`details` 为包含 `resource` 与 `items` 的对象。"
    "接口规则见 docs/09、docs/10、docs/11。"
)

OPENAPI_TAGS: list[dict[str, Any]] = [
    {"name": "health", "description": "存活与就绪检查（docs/09 8.7）"},
    {"name": "auth", "description": "登录、注销、当前身份与本人改密（docs/10 8.3）"},
    {"name": "api-keys", "description": "本人 API Key 管理（docs/10 8.3、docs/04 5.5）"},
    {"name": "users", "description": "用户账户管理，仅公司管理员（docs/10 8.4）"},
    {"name": "master-data", "description": "仓库、部门、项目、厂家、分类与物资（docs/10 8.4）"},
    {"name": "documents", "description": "出入库草稿、提交与软删除（docs/11 8.5）"},
    {"name": "xlsx", "description": "XLSX 模板、快速录入解析与单张导出（docs/11 8.5）"},
    {"name": "inventory", "description": "实时库存与有效单据流水查询（docs/11 8.5）"},
    {"name": "audit", "description": "审计查询，仅公司管理员（docs/10 8.4）"},
]

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "trace"})
_UNSAFE_METHODS = frozenset({"post", "put", "patch", "delete"})
_CSRF_EXEMPT = {("/auth/login", "post")}


def install_openapi_contract(app: FastAPI) -> None:
    """替换 ``app.openapi``，补充安全方案与统一响应头。"""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            servers=app.servers,
        )
        components = schema.setdefault("components", {})
        components.setdefault("securitySchemes", {}).setdefault(
            "sessionCookie",
            {
                "type": "apiKey",
                "in": "cookie",
                "name": "only_stock_session",
                "description": "登录成功后由 /auth/login 设置的 HttpOnly 会话 Cookie",
            },
        )
        _add_request_id_header(schema)
        _add_csrf_parameters(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def _add_csrf_parameters(schema: dict[str, Any]) -> None:
    """所有会话写请求都要求 ``X-CSRF-Token``，登录除外。"""
    parameter = {
        "name": "X-CSRF-Token",
        "in": "header",
        "required": True,
        "description": "会话派生 CSRF Token；登录时由 /auth/login 响应返回",
        "schema": {"type": "string"},
    }
    for path, path_item in schema.get("paths", {}).items():
        if not (path.startswith("/api/v1") or path.startswith("/auth")):
            continue
        for method, operation in path_item.items():
            if method not in _UNSAFE_METHODS or not isinstance(operation, dict):
                continue
            if (path, method) in _CSRF_EXEMPT:
                continue
            parameters = operation.setdefault("parameters", [])
            if not any(item.get("name") == "X-CSRF-Token" for item in parameters):
                parameters.append(dict(parameter))


def _add_request_id_header(schema: dict[str, Any]) -> None:
    """每个 HTTP 响应都带 ``X-Request-ID``；与请求上下文中间件一致。"""
    header = {
        "description": "服务端请求标识；Web/API 创建单据时沿用客户端 request_id",
        "schema": {"type": "string", "format": "uuid"},
    }
    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            for response in operation.get("responses", {}).values():
                if isinstance(response, dict):
                    response.setdefault("headers", {})["X-Request-ID"] = dict(header)
