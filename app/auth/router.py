"""认证与本人 API Key 接口。

依据 docs/10-账户、基础资料与系统维护API.md 8.3：
- ``/auth`` 使用会话 Cookie，登录匿名且限流，写请求校验 CSRF；
- ``/api/v1/api-keys`` 只允许本人管理，完整 Key 仅在创建成功响应中出现一次。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.listing import ListQuery
from app.api.responses import COMMON_ERROR_RESPONSES, error_response
from app.auth.account import authenticate_and_create_session, change_password
from app.auth.api_keys import (
    create_api_key,
    list_api_keys,
    revoke_api_key,
)
from app.auth.dependencies import AuthContext, get_auth_context, require_password_changed
from app.auth.login_names import validate_login_name
from app.auth.schemas import (
    ApiKeyCreated,
    ApiKeyCreateRequest,
    ApiKeyListResponse,
    ApiKeyMetadata,
    ChangePasswordRequest,
    IdentityResponse,
    LoginRequest,
    UserIdentity,
)
from app.auth.sessions import clear_session_cookie, revoke_session, set_session_cookie
from app.db.database import get_session
from app.db.types import utc_now
from app.errors import AppError
from app.modules.audit.service import record_audit
from app.modules.users.models import User
from app.request_context import get_request_id

auth_router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses=COMMON_ERROR_RESPONSES,
)
api_key_router = APIRouter(
    prefix="/api/v1/api-keys",
    tags=["api-keys"],
    responses=COMMON_ERROR_RESPONSES,
)


@auth_router.post(
    "/login",
    response_model=IdentityResponse,
    responses={429: error_response("登录限流")},
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> IdentityResponse:
    settings = request.app.state.settings
    limiter = request.app.state.runtime.login_rate_limiter
    ip = request.client.host if request.client else "unknown"
    login_name = validate_login_name(payload.login_name)

    retry_after = limiter.check(ip, login_name)
    if retry_after is not None:
        raise AppError(
            "RATE_LIMITED",
            "登录尝试过于频繁，请稍后重试",
            status_code=429,
            resource="login",
            items=[{"retry_after_seconds": retry_after}],
        )

    try:
        credentials, user = authenticate_and_create_session(
            request.app.state.runtime.session_factory,
            settings,
            login_name,
            payload.password,
            now=utc_now(),
        )
    except AppError as error:
        if error.code == "INVALID_CREDENTIALS":
            limiter.record_failure(ip, login_name)
        raise

    limiter.reset(ip, login_name)
    record_audit(
        session,
        actor_user_id=user.id,
        action="LOGIN_SUCCEEDED",
        entity_type="user",
        entity_id=user.id,
        after={"id": user.id, "login_name": user.login_name, "role": user.role},
        request_id=get_request_id(),
        source="API",
    )
    session.commit()
    set_session_cookie(response, credentials.raw_token)
    return _identity(user, credentials.csrf_token)


@auth_router.post("/logout", status_code=204)
def logout(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    revoke_session(auth.session, utc_now())
    session.commit()
    result = Response(status_code=204)
    clear_session_cookie(result)
    return result


@auth_router.get("/me", response_model=IdentityResponse)
def me(auth: Annotated[AuthContext, Depends(get_auth_context)]) -> IdentityResponse:
    return _identity(auth.user, auth.csrf_token)


@auth_router.post("/change-password", response_model=IdentityResponse)
def change_own_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
) -> IdentityResponse:
    credentials, user = change_password(
        request.app.state.runtime.session_factory,
        request.app.state.settings,
        auth.raw_token,
        payload.old_password,
        payload.new_password,
    )
    set_session_cookie(response, credentials.raw_token)
    return _identity(user, credentials.csrf_token)


@api_key_router.get("", response_model=ApiKeyListResponse)
def get_api_keys(
    auth: Annotated[AuthContext, Depends(require_password_changed)],
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
    include_revoked: bool = False,
) -> ApiKeyListResponse:
    rows, total = list_api_keys(
        session,
        auth.user.id,
        include_revoked=include_revoked,
        page=params.page,
        page_size=params.page_size,
        sort=params.sort,
        order=params.order,
    )
    return ApiKeyListResponse(
        items=[ApiKeyMetadata.model_validate(row) for row in rows],
        page=params.page,
        page_size=params.page_size,
        total=total,
    )


@api_key_router.post("", response_model=ApiKeyCreated, status_code=201)
def post_api_key(
    payload: ApiKeyCreateRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_password_changed)],
) -> ApiKeyCreated:
    row, raw_key = create_api_key(
        request.app.state.runtime.session_factory,
        auth.user.id,
        payload.name,
        request_id=get_request_id(),
    )
    metadata = ApiKeyMetadata.model_validate(row)
    return ApiKeyCreated(**metadata.model_dump(), key=raw_key)


@api_key_router.post("/{key_id}/revoke", response_model=ApiKeyMetadata)
def revoke_own_api_key(
    key_id: UUID,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_password_changed)],
) -> ApiKeyMetadata:
    row = revoke_api_key(
        request.app.state.runtime.session_factory,
        auth.user.id,
        str(key_id),
        request_id=get_request_id(),
    )
    return ApiKeyMetadata.model_validate(row)


def _identity(user: User, csrf_token: str) -> IdentityResponse:
    identity = UserIdentity.model_validate(user)
    return IdentityResponse(**identity.model_dump(), csrf_token=csrf_token)
