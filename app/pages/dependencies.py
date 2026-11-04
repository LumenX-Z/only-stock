"""页面认证、角色与 CSRF 依赖。

页面写请求使用表单隐藏字段 ``_csrf``；也接受 ``X-CSRF-Token`` 头，
便于 HTMX 请求复用同一校验。后端始终是权限边界（docs/12 9.2）。
"""

from typing import Annotated

from fastapi import Depends, Request

from app.auth.dependencies import AuthContext, get_page_auth_context
from app.auth.sessions import CSRF_HEADER_NAME, verify_csrf
from app.errors import AppError
from app.modules.users.models import Role

PageRawAuth = Annotated[AuthContext, Depends(get_page_auth_context)]


def require_page_user(auth: PageRawAuth) -> AuthContext:
    if auth.user.force_password_change:
        raise AppError(
            "PASSWORD_CHANGE_REQUIRED",
            "请先修改密码",
            status_code=403,
            resource="user",
        )
    return auth


PageUser = Annotated[AuthContext, Depends(require_page_user)]


def require_page_admin(auth: PageUser) -> AuthContext:
    if auth.user.role != Role.COMPANY_ADMIN.value:
        raise AppError(
            "FORBIDDEN",
            "无权执行该操作",
            status_code=403,
            resource="permission",
            items=[{"required_role": Role.COMPANY_ADMIN.value}],
        )
    return auth


PageAdmin = Annotated[AuthContext, Depends(require_page_admin)]


async def check_page_csrf(request: Request, auth: AuthContext) -> None:
    token = request.headers.get(CSRF_HEADER_NAME)
    if not token:
        form = await request.form()
        token = form.get("_csrf")
    if not token or not verify_csrf(auth.session, str(token)):
        raise AppError(
            "CSRF_INVALID",
            "CSRF 校验失败，请刷新页面后重试",
            status_code=403,
            resource="session",
        )


async def require_page_csrf(request: Request, auth: PageUser) -> AuthContext:
    await check_page_csrf(request, auth)
    return auth


PageForm = Annotated[AuthContext, Depends(require_page_csrf)]


async def require_page_admin_csrf(request: Request, auth: PageAdmin) -> AuthContext:
    return await require_page_csrf(request, auth)


PageAdminForm = Annotated[AuthContext, Depends(require_page_admin_csrf)]
