"""登录、注销、首次改密与工作台页面。"""

from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.account import change_password
from app.auth.login import login as perform_login
from app.auth.sessions import (
    SESSION_COOKIE_NAME,
    authenticate_session,
    clear_session_cookie,
    revoke_session,
    set_session_cookie,
)
from app.db.database import get_session
from app.db.types import utc_now
from app.errors import AppError
from app.modules.users.models import UserStatus
from app.pages import common
from app.pages.dependencies import PageRawAuth, PageUser, check_page_csrf
from app.pages.templating import render
from app.request_context import get_request_id

router = APIRouter(include_in_schema=False)


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    return value


@router.get("/login")
def login_page(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        authenticated = authenticate_session(
            session, raw_token, request.app.state.settings, now=utc_now()
        )
        if authenticated is not None and authenticated.user.status == UserStatus.ACTIVE.value:
            session.commit()
            return RedirectResponse(url="/", status_code=303)
        if authenticated is not None:
            revoke_session(authenticated.session, utc_now())
        session.commit()
    response = render(
        request,
        "login.html",
        next_url=_safe_next(request.query_params.get("next")),
        form={},
    )
    if raw_token:
        clear_session_cookie(response)
    return response


@router.post("/login")
async def login_submit(request: Request) -> Response:
    form = await request.form()
    login_name = str(form.get("login_name", "")).strip()
    password = str(form.get("password", ""))
    next_url = _safe_next(str(form.get("next", "/")))
    try:
        result = perform_login(
            request.app.state.runtime.session_factory,
            request.app.state.settings,
            request.app.state.runtime.login_rate_limiter,
            ip=request.client.host if request.client else "unknown",
            login_name=login_name,
            password=password,
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        return render(
            request,
            "login.html",
            status_code=exc.status_code,
            error=common.error_view(exc, get_request_id() or ""),
            next_url=next_url,
            form={"login_name": login_name},
        )
    response = RedirectResponse(url=next_url, status_code=303)
    set_session_cookie(response, result.credentials.raw_token)
    return response


@router.post("/logout")
async def logout_page(
    request: Request,
    auth: PageRawAuth,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    await check_page_csrf(request, auth)
    revoke_session(auth.session, utc_now())
    session.commit()
    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookie(response)
    return response


@router.get("/change-password")
def change_password_page(request: Request, auth: PageRawAuth) -> Response:
    return render(
        request,
        "change_password.html",
        auth=auth,
        forced=auth.user.force_password_change,
    )


@router.post("/change-password")
async def change_password_submit(request: Request, auth: PageRawAuth) -> Response:
    await check_page_csrf(request, auth)
    form = await request.form()
    old_password = str(form.get("old_password", ""))
    new_password = str(form.get("new_password", ""))
    confirm_password = str(form.get("confirm_password", ""))
    if new_password != confirm_password:
        error = {
            "code": "VALIDATION_ERROR",
            "message": "两次输入的新密码不一致",
            "request_id": get_request_id() or "",
            "details": {
                "resource": None,
                "items": [{"field": "confirm_password", "reason": "两次输入的新密码不一致"}],
            },
        }
        return render(
            request,
            "change_password.html",
            auth=auth,
            forced=auth.user.force_password_change,
            error=error,
            status_code=422,
        )
    try:
        credentials, user = change_password(
            request.app.state.runtime.session_factory,
            request.app.state.settings,
            auth.raw_token,
            old_password,
            new_password,
        )
    except AppError as exc:
        return render(
            request,
            "change_password.html",
            auth=auth,
            forced=auth.user.force_password_change,
            error=common.error_view(exc, get_request_id() or ""),
            status_code=exc.status_code,
        )
    response = RedirectResponse(url="/", status_code=303)
    set_session_cookie(response, credentials.raw_token)
    return response


@router.get("/")
def dashboard(
    request: Request,
    auth: PageUser,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    from app.modules.documents.presenter import summary_payload
    from app.modules.documents.service import list_documents

    rows, projects, _ = list_documents(session, page=1, page_size=6)
    recent = [summary_payload(row, projects.get(row.id, [])) for row in rows]
    return render(
        request,
        "dashboard.html",
        auth=auth,
        stats=common.dashboard_stats(session),
        recent_documents=recent,
    )
