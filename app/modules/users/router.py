"""用户管理接口。

依据 docs/10-账户、基础资料与系统维护API.md 8.4：用户列表、创建、修改、禁用和
重置密码仅公司管理员可用；修改携带 ``version`` 并保护最后一名启用管理员。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth.permissions import CompanyAdmin
from app.auth.schemas import UserIdentity
from app.db.database import get_session
from app.modules.users.schemas import (
    UserCreateRequest,
    UserDisableRequest,
    UserListResponse,
    UserResetPasswordRequest,
    UserUpdateRequest,
)
from app.modules.users.service import (
    create_user,
    disable_user,
    list_users,
    reset_user_password,
    update_user,
)
from app.request_context import get_request_id

router = APIRouter(prefix="/api/v1/users", tags=["users"])

PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=UserListResponse)
def get_users(
    _auth: CompanyAdmin,
    session: Annotated[Session, Depends(get_session)],
    page: PageNumber = 1,
    page_size: PageSize = 20,
    role: str | None = None,
    status: str | None = None,
    login_name: str | None = None,
) -> UserListResponse:
    rows, total = list_users(
        session,
        page=page,
        page_size=page_size,
        role=role,
        status=status,
        login_name=login_name,
    )
    return UserListResponse(
        items=[UserIdentity.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=UserIdentity, status_code=201)
def post_user(
    payload: UserCreateRequest,
    auth: CompanyAdmin,
    request: Request,
) -> UserIdentity:
    user = create_user(
        request.app.state.runtime.session_factory,
        auth.user.id,
        login_name=payload.login_name,
        display_name=payload.display_name,
        role=payload.role,
        initial_password=payload.initial_password,
        request_id=get_request_id(),
    )
    return UserIdentity.model_validate(user)


@router.patch("/{user_id}", response_model=UserIdentity)
def patch_user(
    user_id: UUID,
    payload: UserUpdateRequest,
    auth: CompanyAdmin,
    request: Request,
) -> UserIdentity:
    user = update_user(
        request.app.state.runtime.session_factory,
        auth.user.id,
        str(user_id),
        display_name=payload.display_name,
        role=payload.role,
        version=payload.version,
        request_id=get_request_id(),
    )
    return UserIdentity.model_validate(user)


@router.post("/{user_id}/disable", response_model=UserIdentity)
def disable(
    user_id: UUID,
    auth: CompanyAdmin,
    request: Request,
    payload: UserDisableRequest | None = None,
) -> UserIdentity:
    user = disable_user(
        request.app.state.runtime.session_factory,
        auth.user.id,
        str(user_id),
        version=payload.version if payload else None,
        request_id=get_request_id(),
    )
    return UserIdentity.model_validate(user)


@router.post("/{user_id}/reset-password", response_model=UserIdentity)
def reset_password(
    user_id: UUID,
    payload: UserResetPasswordRequest,
    auth: CompanyAdmin,
    request: Request,
) -> UserIdentity:
    user = reset_user_password(
        request.app.state.runtime.session_factory,
        auth.user.id,
        str(user_id),
        payload.new_password,
        request_id=get_request_id(),
    )
    return UserIdentity.model_validate(user)
