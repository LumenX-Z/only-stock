"""六类基础资料、统一编码与审计日志迁移。

Revision ID: 0002_master_data
Revises: 0001_accounts
Create Date: 2026-11-03

依据 docs/05-数据模型与实体约束.md 6.2、6.4、6.6–6.8 与
docs/04-认证、会话与凭证安全.md 5.6：建立 warehouses、departments、categories、
materials、projects、manufacturers、code_counters、audit_logs；分类使用部分唯一
索引保证一级名称全局唯一、二级名称在父级内唯一；审计日志以触发器保证只追加。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_master_data"
down_revision: str | Sequence[str] | None = "0001_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "code_counters",
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("entity_type"),
    )

    op.create_table(
        "warehouses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_warehouses_is_active"),
        sa.CheckConstraint("version >= 1", name="ck_warehouses_version"),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 100", name="ck_warehouses_name_length"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_warehouses_code", "warehouses", ["code"], unique=True)
    op.create_index("ux_warehouses_name", "warehouses", ["name"], unique=True)

    op.create_table(
        "departments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_departments_is_active"),
        sa.CheckConstraint("version >= 1", name="ck_departments_version"),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 100", name="ck_departments_name_length"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_departments_code", "departments", ["code"], unique=True)
    op.create_index("ux_departments_name", "departments", ["name"], unique=True)

    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_projects_is_active"),
        sa.CheckConstraint("version >= 1", name="ck_projects_version"),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 100", name="ck_projects_name_length"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_projects_code", "projects", ["code"], unique=True)
    op.create_index("ux_projects_name", "projects", ["name"], unique=True)

    op.create_table(
        "manufacturers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_manufacturers_is_active"),
        sa.CheckConstraint("version >= 1", name="ck_manufacturers_version"),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 100", name="ck_manufacturers_name_length"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_manufacturers_code", "manufacturers", ["code"], unique=True)
    op.create_index("ux_manufacturers_name", "manufacturers", ["name"], unique=True)

    op.create_table(
        "categories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.CheckConstraint("level IN (1, 2)", name="ck_categories_level"),
        sa.CheckConstraint(
            "(level = 1 AND parent_id IS NULL) OR (level = 2 AND parent_id IS NOT NULL)",
            name="ck_categories_parent_level",
        ),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_categories_is_active"),
        sa.CheckConstraint("version >= 1", name="ck_categories_version"),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 100", name="ck_categories_name_length"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["categories.id"],
            name="fk_categories_parent_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_categories_code", "categories", ["code"], unique=True)
    op.create_index(
        "ux_categories_name_level1",
        "categories",
        ["name"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "ux_categories_parent_name_level2",
        "categories",
        ["parent_id", "name"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NOT NULL"),
    )
    op.create_index("ix_categories_parent_id", "categories", ["parent_id"], unique=False)

    op.create_table(
        "materials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.String(length=36), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("quantity_type", sa.String(length=8), nullable=False),
        sa.CheckConstraint(
            "quantity_type IN ('INTEGER', 'DECIMAL')", name="ck_materials_quantity_type"
        ),
        sa.CheckConstraint("is_active IN (0, 1)", name="ck_materials_is_active"),
        sa.CheckConstraint("version >= 1", name="ck_materials_version"),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 100", name="ck_materials_name_length"
        ),
        sa.CheckConstraint(
            "length(unit) BETWEEN 1 AND 20", name="ck_materials_unit_length"
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name="fk_materials_category_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_materials_code", "materials", ["code"], unique=True)
    op.create_index(
        "ux_materials_category_name", "materials", ["category_id", "name"], unique=True
    )
    op.create_index("ix_materials_category_id", "materials", ["category_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=True),
        sa.Column("before_json", sa.Text(), nullable=True),
        sa.Column("after_json", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("api_key_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("source IN ('WEB', 'API', 'MCP')", name="ck_audit_logs_source"),
        sa.CheckConstraint(
            "length(action) BETWEEN 1 AND 64", name="ck_audit_logs_action_length"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_logs_actor_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], unique=False)
    op.create_index(
        "ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"], unique=False
    )
    op.create_index(
        "ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"], unique=False
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)

    op.execute(
        "CREATE TRIGGER trg_audit_logs_no_update BEFORE UPDATE ON audit_logs "
        "BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_logs_no_delete BEFORE DELETE ON audit_logs "
        "BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_update")

    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_materials_category_id", table_name="materials")
    op.drop_index("ux_materials_category_name", table_name="materials")
    op.drop_index("ux_materials_code", table_name="materials")
    op.drop_table("materials")

    op.drop_index("ix_categories_parent_id", table_name="categories")
    op.drop_index("ux_categories_parent_name_level2", table_name="categories")
    op.drop_index("ux_categories_name_level1", table_name="categories")
    op.drop_index("ux_categories_code", table_name="categories")
    op.drop_table("categories")

    op.drop_index("ux_manufacturers_name", table_name="manufacturers")
    op.drop_index("ux_manufacturers_code", table_name="manufacturers")
    op.drop_table("manufacturers")

    op.drop_index("ux_projects_name", table_name="projects")
    op.drop_index("ux_projects_code", table_name="projects")
    op.drop_table("projects")

    op.drop_index("ux_departments_name", table_name="departments")
    op.drop_index("ux_departments_code", table_name="departments")
    op.drop_table("departments")

    op.drop_index("ux_warehouses_name", table_name="warehouses")
    op.drop_index("ux_warehouses_code", table_name="warehouses")
    op.drop_table("warehouses")

    op.drop_table("code_counters")
