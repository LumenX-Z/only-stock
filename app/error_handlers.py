"""统一错误响应处理器。

所有响应遵循 docs/09-HTTP-API通用约定与健康检查.md 8.1 的结构；
校验错误不回显请求体的原始值，避免密码等敏感输入进入响应或日志。
"""

import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth.sessions import SESSION_COOKIE_NAME, clear_session_cookie
from app.db.sqlite import is_busy_error
from app.errors import AppError, error_payload
from app.request_context import get_request_id

logger = logging.getLogger(__name__)

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    503: "SERVICE_UNAVAILABLE",
}
_HTTP_MESSAGES = {
    400: "请求报文错误",
    401: "未认证或认证已失效",
    403: "无权访问该资源",
    404: "请求的资源不存在",
    405: "请求方法不被允许",
    409: "请求与当前资源状态冲突",
    422: "请求参数校验失败",
    429: "请求过于频繁，请稍后重试",
    503: "服务尚未就绪",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = _request_id(request)
        logger.info(
            "application error code=%s status=%s",
            exc.code,
            exc.status_code,
            extra={"request_id": request_id},
        )
        if _is_page_request(request):
            if exc.code == "UNAUTHORIZED":
                response = _page_login_redirect(request)
                _clear_stale_session_cookie(request, response)
                return response
            if exc.code == "PASSWORD_CHANGE_REQUIRED":
                return RedirectResponse(url="/change-password", status_code=303)
            from app.pages.templating import render_error_page

            response = render_error_page(
                request,
                status_code=exc.status_code,
                code=exc.code,
                message=exc.message,
                request_id=request_id,
                details={"resource": exc.resource, "items": list(exc.items)},
            )
            if exc.code == "ACCOUNT_DISABLED":
                _clear_stale_session_cookie(request, response)
            return response
        response = JSONResponse(
            status_code=exc.status_code,
            content=error_payload(
                code=exc.code,
                message=exc.message,
                request_id=request_id,
                resource=exc.resource,
                items=exc.items,
            ),
        )
        if exc.code in {"UNAUTHORIZED", "ACCOUNT_DISABLED"} and request.url.path.startswith(
            ("/api/", "/auth/")
        ):
            _clear_stale_session_cookie(request, response)
        return response

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = _request_id(request)
        items = [_validation_item(error) for error in exc.errors()]
        logger.info(
            "validation failed item_count=%s path=%s",
            len(items),
            request.url.path,
            extra={"request_id": request_id},
        )
        if _is_page_request(request):
            from app.pages.templating import render_error_page

            return render_error_page(
                request,
                status_code=422,
                code="VALIDATION_ERROR",
                message="请求参数校验失败",
                request_id=request_id,
                details={"resource": None, "items": items},
            )
        return JSONResponse(
            status_code=422,
            content=error_payload(
                code="VALIDATION_ERROR",
                message="请求参数校验失败",
                request_id=request_id,
                items=items,
            ),
        )

    @app.exception_handler(OperationalError)
    async def _handle_operational_error(request: Request, exc: OperationalError) -> JSONResponse:
        request_id = _request_id(request)
        if is_busy_error(exc):
            logger.warning(
                "database busy path=%s",
                request.url.path,
                extra={"request_id": request_id},
            )
            return JSONResponse(
                status_code=409,
                content=error_payload(
                    code="WRITE_CONFLICT",
                    message="数据库正在处理其他写入，请稍后重试",
                    request_id=request_id,
                ),
            )

        logger.exception(
            "database error path=%s", request.url.path, extra={"request_id": request_id}
        )
        return _internal_error(request_id)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status_code = exc.status_code
        if _is_page_request(request):
            from app.pages.templating import render_error_page

            return render_error_page(
                request,
                status_code=status_code,
                code=_HTTP_CODES.get(status_code, "HTTP_ERROR"),
                message=_HTTP_MESSAGES.get(status_code, "请求失败"),
                request_id=_request_id(request),
            )
        return JSONResponse(
            status_code=status_code,
            content=error_payload(
                code=_HTTP_CODES.get(status_code, "HTTP_ERROR"),
                message=_HTTP_MESSAGES.get(status_code, "请求失败"),
                request_id=_request_id(request),
            ),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled exception path=%s",
            request.url.path,
            extra={"request_id": _request_id(request)},
        )
        if _is_page_request(request):
            from app.pages.templating import render_error_page

            return render_error_page(
                request,
                status_code=500,
                code="INTERNAL_ERROR",
                message="服务器内部错误",
                request_id=_request_id(request),
            )
        return _internal_error(_request_id(request))


def _validation_item(error: dict[str, Any]) -> dict[str, Any]:
    location = error.get("loc") or ("request",)
    return {
        "field": ".".join(str(part) for part in location),
        "reason": str(error.get("msg", "invalid value")),
        "type": str(error.get("type", "validation_error")),
    }


def _internal_error(request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_payload(
            code="INTERNAL_ERROR",
            message="服务器内部错误",
            request_id=request_id,
        ),
        headers={"X-Request-ID": request_id},
    )


_PAGE_EXCLUDED_PREFIXES = (
    "/api/",
    "/auth/",
    "/health/",
    "/mcp",
    "/static/",
    "/openapi.json",
    "/docs",
    "/redoc",
)


def _is_page_request(request: Request) -> bool:
    if not getattr(request.app.state, "pages_enabled", False):
        return False
    path = request.url.path
    return not path.startswith(_PAGE_EXCLUDED_PREFIXES)


def _clear_stale_session_cookie(request: Request, response: Response) -> None:
    """错误响应中的 Cookie 删除头必须写在最终响应上，不能依赖被替换的 Response。"""
    if request.cookies.get(SESSION_COOKIE_NAME):
        clear_session_cookie(response)


def _page_login_redirect(request: Request) -> RedirectResponse:
    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(url=f"/login?next={next_url}", status_code=303)


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None) or get_request_id()
    return value or str(uuid4())
