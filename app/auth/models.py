"""浏览器会话与 API Key 模型。

依据 docs/05-数据模型与实体约束.md 6.1 与 docs/04-认证、会话与凭证安全.md
5.4、5.5：会话 ID 与 API Key 只保存哈希；会话另有 CSRF 哈希；API Key 保存
末四位用于识别，不保存明文和角色。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime, new_uuid, utc_now

SESSION_ID_HASH_LENGTH = 64
API_KEY_HASH_LENGTH = 64
API_KEY_SUFFIX_LENGTH = 4
API_KEY_NAME_MAX_LENGTH = 50


class AuthSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("length(id_hash) = 64", name="ck_sessions_id_hash_length"),
        CheckConstraint("length(csrf_hash) = 64", name="ck_sessions_csrf_hash_length"),
        CheckConstraint(
            "absolute_expires_at >= created_at", name="ck_sessions_absolute_after_created"
        ),
        CheckConstraint("last_seen_at >= created_at", name="ck_sessions_last_seen_after_created"),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_absolute_expires_at", "absolute_expires_at"),
    )

    id_hash: Mapped[str] = mapped_column(String(SESSION_ID_HASH_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", name="fk_sessions_user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    absolute_expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 1 AND 50", name="ck_api_keys_name_length"),
        CheckConstraint("length(key_hash) = 64", name="ck_api_keys_hash_length"),
        CheckConstraint("length(key_suffix) = 4", name="ck_api_keys_suffix_length"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_api_keys_revoked_after_created",
        ),
        Index("ux_api_keys_key_hash", "key_hash", unique=True),
        Index("ix_api_keys_user_id", "user_id"),
        Index("ix_api_keys_user_id_revoked_at", "user_id", "revoked_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", name="fk_api_keys_user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(API_KEY_NAME_MAX_LENGTH), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(API_KEY_HASH_LENGTH), nullable=False)
    key_suffix: Mapped[str] = mapped_column(String(API_KEY_SUFFIX_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
