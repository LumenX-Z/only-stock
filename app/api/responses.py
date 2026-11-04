"""OpenAPI 统一错误响应组件。

依据 docs/09-HTTP-API通用约定与健康检查.md 8.1：错误体固定为
``{"error": {"code", "message", "request_id", "details"}}``，``details``
是对象并至少包含 ``resource`` 与 ``items``。组件只用于 OpenAPI 契约，
运行时错误由 ``app.error_handlers`` 统一构造。
"""

from typing import Any

from pydantic import BaseModel


class ErrorDetails(BaseModel):
    resource: str | None
    items: list[dict[str, Any]]


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: ErrorDetails


class ErrorResponse(BaseModel):
    error: ErrorBody


def error_response(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


# 所有 /api/v1 与 /auth 操作共享的错误状态；实现可能返回的子集也包含在内。
COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: error_response("请求报文错误"),
    401: error_response("未认证或认证已失效"),
    403: error_response("无权限、账户被禁用或 CSRF 校验失败"),
    404: error_response("请求的对象不存在"),
    409: error_response("状态、版本、库存、名称或写锁冲突"),
    422: error_response("字段校验失败或排序字段不在白名单内"),
    500: error_response("服务器内部错误"),
}

AUTH_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **COMMON_ERROR_RESPONSES,
    429: error_response("登录限流"),
}
