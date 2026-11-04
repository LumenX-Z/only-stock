"""用户管理接口的请求/响应模型。"""

from typing import Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.api.schemas import PageMeta
from app.auth.schemas import UserIdentity


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserCreateRequest(_StrictRequest):
    login_name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    role: Literal["COMPANY_ADMIN", "OPERATOR"]
    initial_password: str = Field(
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices("initial_password", "password"),
    )


class UserUpdateRequest(_StrictRequest):
    version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: Literal["COMPANY_ADMIN", "OPERATOR"] | None = None

    @model_validator(mode="after")
    def _require_change(self) -> Self:
        if self.display_name is None and self.role is None:
            raise ValueError("至少需要修改显示名或角色")
        return self


class UserDisableRequest(_StrictRequest):
    version: int | None = Field(default=None, ge=1)


class UserResetPasswordRequest(_StrictRequest):
    new_password: str = Field(min_length=1, max_length=256)


class UserListResponse(PageMeta):
    items: list[UserIdentity]
