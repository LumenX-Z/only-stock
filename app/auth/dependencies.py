"""认证依赖：当前身份、账户状态与 CSRF 校验。

依据 docs/04-认证、会话与凭证安全.md 5.4 与 docs/09-HTTP-API通用约定与健康检查.md
8.1：除登录和健康检查外均需认证；所有登录态写请求必须校验 CSRF；账户被禁用
或认证已失效时立即失败。
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from app.auth.models import AuthSession
from app.auth.sessions import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    AuthenticatedSession,
    authenticate_session,
    clear_session_cookie,
    revoke_session,
    verify_csrf,
)
from app.db.database import get_session
from app.db.types import utc_now
from app.errors import AppError
from app.modules.users.models import User, UserStatus

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: AuthSession
    raw_token: str
    csrf_token: str


def get_auth_context(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> AuthContext:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise _unauthorized()

    authenticated = authenticate_session(
        session, raw_token, request.app.state.settings, now=utc_now()
    )
    if authenticated is None:
        session.commit()
        clear_session_cookie(response)
        raise _unauthorized()

    if authenticated.user.status != UserStatus.ACTIVE.value:
        revoke_session(authenticated.session, utc_now())
        session.commit()
        clear_session_cookie(response)
        raise AppError(
            "ACCOUNT_DISABLED",
            "账户不可用",
            status_code=403,
            resource="user",
        )

    if request.method in _UNSAFE_METHODS and not verify_csrf(
        authenticated.session, request.headers.get(CSRF_HEADER_NAME)
    ):
        raise AppError(
            "CSRF_INVALID",
            "CSRF 校验失败",
            status_code=403,
            resource="session",
        )

    session.commit()
    return _context(authenticated)


def require_password_changed(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    """初始密码或重置密码后的账户必须先改密才能使用其他功能。"""
    if auth.user.force_password_change:
        raise AppError(
            "PASSWORD_CHANGE_REQUIRED",
            "请先修改密码",
            status_code=403,
            resource="user",
        )
    return auth


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def _context(authenticated: AuthenticatedSession) -> AuthContext:
    return AuthContext(
        user=authenticated.user,
        session=authenticated.session,
        raw_token=authenticated.raw_token,
        csrf_token=authenticated.csrf_token,
    )


def _unauthorized() -> AppError:
    return AppError(
        "UNAUTHORIZED",
        "未认证或认证已失效",
        status_code=401,
        resource="session",
    )
