"""用户账户模型。

依据 docs/05-数据模型与实体约束.md 6.1：UUID 主键、UTC 时间、规范化登录名
唯一、角色仅两种、用户不物理删除。依据 docs/03-角色、权限与账户生命周期.md
4.3：初始密码与重置密码由 ``force_password_change`` 标记强制首次改密。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime, new_uuid, utc_now


class Role(StrEnum):
    COMPANY_ADMIN = "COMPANY_ADMIN"
    OPERATOR = "OPERATOR"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('COMPANY_ADMIN', 'OPERATOR')", name="ck_users_role"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_users_status"),
        CheckConstraint("force_password_change IN (0, 1)", name="ck_users_force_password_change"),
        CheckConstraint("password_version >= 1", name="ck_users_password_version"),
        CheckConstraint("version >= 1", name="ck_users_version"),
        CheckConstraint("length(login_name) > 0", name="ck_users_login_name_not_empty"),
        CheckConstraint("length(display_name) > 0", name="ck_users_display_name_not_empty"),
        Index("ux_users_login_name", "login_name", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    login_name: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=UserStatus.ACTIVE.value)
    force_password_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    @property
    def role_enum(self) -> Role:
        return Role(self.role)

    @property
    def status_enum(self) -> UserStatus:
        return UserStatus(self.status)

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE.value
