"""用户管理页面：列表、创建、编辑、禁用与重置密码。"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.listing import ListQuery
from app.db.database import get_session
from app.errors import AppError
from app.modules.users.models import Role, User, UserStatus
from app.modules.users.service import (
    create_user,
    disable_user,
    list_users,
    reset_user_password,
    update_user,
)
from app.pages import common
from app.pages.dependencies import PageAdmin, PageAdminForm
from app.pages.templating import flash, redirect, render
from app.request_context import get_request_id

router = APIRouter(prefix="/users", include_in_schema=False)


def _render_list(
    request: Request,
    auth: Any,
    session: Session,
    params: Any,
    *,
    error: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    status_code: int = 200,
) -> Response:
    role = request.query_params.get("role") or None
    status = request.query_params.get("status") or None
    login_name = request.query_params.get("login_name") or None
    rows, total = list_users(
        session,
        page=params.page,
        page_size=params.page_size,
        role=role,
        status=status,
        login_name=login_name,
    )
    return render(
        request,
        "users/list.html",
        auth=auth,
        status_code=status_code,
        users=rows,
        total=total,
        page=params.page,
        page_size=params.page_size,
        filters={"role": role or "", "status": status or "", "login_name": login_name or ""},
        roles=list(Role),
        statuses=list(UserStatus),
        form=form or {},
        error=error,
    )


@router.get("")
def users_page(
    request: Request,
    auth: PageAdmin,
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
) -> Response:
    return _render_list(request, auth, session, params)


@router.post("")
async def create_user_page(
    request: Request,
    auth: PageAdminForm,
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
) -> Response:
    form = await request.form()
    values = {
        "login_name": str(form.get("login_name", "")).strip(),
        "display_name": str(form.get("display_name", "")).strip(),
        "role": str(form.get("role", "OPERATOR")),
    }
    try:
        user = create_user(
            request.app.state.runtime.session_factory,
            auth.user.id,
            login_name=values["login_name"],
            display_name=values["display_name"],
            role=values["role"],
            initial_password=str(form.get("initial_password", "")),
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        return _render_list(
            request,
            auth,
            session,
            params,
            error=common.error_view(exc, get_request_id() or ""),
            form=values,
            status_code=exc.status_code,
        )
    flash(auth, f"用户 {user.login_name} 已创建", detail="初始密码需在首次登录时修改。")
    return redirect("/users")


@router.get("/{user_id}/edit")
def edit_user_page(
    user_id: UUID,
    request: Request,
    auth: PageAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    user = session.get(User, str(user_id))
    if user is None:
        return render(
            request,
            "error.html",
            status_code=404,
            code="USER_NOT_FOUND",
            message="用户不存在",
            request_id=get_request_id() or "",
            details={"resource": "user", "items": []},
        )
    return render(
        request,
        "users/edit.html",
        auth=auth,
        target=user,
        roles=list(Role),
        error=None,
    )


@router.post("/{user_id}/edit")
async def update_user_page(
    user_id: UUID,
    request: Request,
    auth: PageAdminForm,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    form = await request.form()
    try:
        update_user(
            request.app.state.runtime.session_factory,
            auth.user.id,
            str(user_id),
            display_name=str(form.get("display_name", "")).strip() or None,
            role=str(form.get("role", "")) or None,
            version=int(str(form.get("version", "0")) or "0") or None,
            request_id=get_request_id(),
            source="WEB",
        )
    except (AppError, ValueError) as exc:
        user = session.get(User, str(user_id))
        error = (
            common.error_view(exc, get_request_id() or "")
            if isinstance(exc, AppError)
            else {
                "code": "VALIDATION_ERROR",
                "message": "版本号无效",
                "request_id": get_request_id() or "",
                "details": {"resource": "user", "items": []},
            }
        )
        return render(
            request,
            "users/edit.html",
            auth=auth,
            status_code=getattr(exc, "status_code", 422),
            target=user,
            roles=list(Role),
            error=error,
        )
    flash(auth, "用户信息已更新")
    return redirect("/users")


@router.post("/{user_id}/disable")
async def disable_user_page(user_id: UUID, request: Request, auth: PageAdminForm) -> Response:
    try:
        disable_user(
            request.app.state.runtime.session_factory,
            auth.user.id,
            str(user_id),
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        flash(auth, f"禁用失败：{exc.message}", level="danger")
        return redirect("/users")
    flash(auth, "用户已禁用，其会话与 API Key 已全部撤销")
    return redirect("/users")


@router.post("/{user_id}/reset-password")
async def reset_password_page(user_id: UUID, request: Request, auth: PageAdminForm) -> Response:
    form = await request.form()
    try:
        reset_user_password(
            request.app.state.runtime.session_factory,
            auth.user.id,
            str(user_id),
            str(form.get("new_password", "")),
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        flash(auth, f"重置失败：{exc.message}", level="danger")
        return redirect("/users")
    flash(auth, "密码已重置", detail="该用户下次登录必须修改密码，旧会话和 API Key 已撤销。")
    return redirect("/users")
