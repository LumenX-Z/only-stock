"""账户、会话与 API Key 首次迁移。

Revision ID: 0001_accounts
Revises:
Create Date: 2026-11-03

依据 docs/05-数据模型与实体约束.md 6.1 与 docs/04-认证、会话与凭证安全.md
5.1、5.2、5.4、5.5：UUID 字符串主键、epoch 毫秒 UTC 时间、规范化登录名唯一、
会话与 Key 只保存哈希、Key 保存末四位。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_accounts"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("login_name", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("force_password_change", sa.Boolean(), nullable=False),
        sa.Column("password_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("role IN ('COMPANY_ADMIN', 'OPERATOR')", name="ck_users_role"),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_users_status"),
        sa.CheckConstraint(
            "force_password_change IN (0, 1)", name="ck_users_force_password_change"
        ),
        sa.CheckConstraint("password_version >= 1", name="ck_users_password_version"),
        sa.CheckConstraint("version >= 1", name="ck_users_version"),
        sa.CheckConstraint("length(login_name) > 0", name="ck_users_login_name_not_empty"),
        sa.CheckConstraint(
            "length(display_name) > 0", name="ck_users_display_name_not_empty"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_users_login_name", "users", ["login_name"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.Integer(), nullable=False),
        sa.Column("absolute_expires_at", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("length(id_hash) = 64", name="ck_sessions_id_hash_length"),
        sa.CheckConstraint("length(csrf_hash) = 64", name="ck_sessions_csrf_hash_length"),
        sa.CheckConstraint(
            "absolute_expires_at >= created_at", name="ck_sessions_absolute_after_created"
        ),
        sa.CheckConstraint(
            "last_seen_at >= created_at", name="ck_sessions_last_seen_after_created"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_sessions_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], unique=False)
    op.create_index(
        "ix_sessions_absolute_expires_at",
        "sessions",
        ["absolute_expires_at"],
        unique=False,
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_suffix", sa.String(length=4), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=64), nullable=True),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 50", name="ck_api_keys_name_length"),
        sa.CheckConstraint("length(key_hash) = 64", name="ck_api_keys_hash_length"),
        sa.CheckConstraint("length(key_suffix) = 4", name="ck_api_keys_suffix_length"),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_api_keys_revoked_after_created",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_api_keys_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"], unique=False)
    op.create_index(
        "ix_api_keys_user_id_revoked_at",
        "api_keys",
        ["user_id", "revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_user_id_revoked_at", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_index("ux_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")

    op.drop_index("ix_sessions_absolute_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("ux_users_login_name", table_name="users")
    op.drop_table("users")
