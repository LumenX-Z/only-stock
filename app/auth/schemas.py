"""认证、身份与 API Key 的请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas import PageMeta


class LoginRequest(BaseModel):
    login_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserIdentity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    login_name: str
    display_name: str
    role: str
    status: str
    force_password_change: bool
    version: int
    created_at: datetime
    updated_at: datetime


class IdentityResponse(UserIdentity):
    csrf_token: str


class ApiKeyCreateRequest(BaseModel):
    name: str


class ApiKeyMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    key_suffix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    revoke_reason: str | None


class ApiKeyCreated(ApiKeyMetadata):
    key: str


class ApiKeyListResponse(PageMeta):
    items: list[ApiKeyMetadata]
