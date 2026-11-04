"""单据头、明细、单号计数器、库存视图与数据库保护触发器。

Revision ID: 0003_stock_documents
Revises: 0002_master_data
Create Date: 2026-11-04

依据 docs/05-数据模型与实体约束.md 6.5、6.6：
- 建立 ``stock_documents``、``stock_document_lines``、``document_counters``；
- ``(created_by, request_id)`` 唯一，``(created_by, request_fingerprint, created_at)`` 建索引；
- 非空项目行以 ``(document_id, material_id, project_id)`` 唯一索引兜底，
  空项目行以 ``(document_id, material_id)`` 部分唯一索引兜底；
- 已生效单据/明细禁止更新或物理删除，至少一行由“转 POSTED”触发器兜底；
- 入库厂家必填、出库厂家为空、项目必须启用、整数物资不得出现小数由触发器兜底。

依据 docs/06-库存计算与单据业务流程.md 7.1：建立 ``v_inventory_balance``，
只汇总已生效且未软删除的单据；库存索引覆盖状态、仓库、明细、物资与项目。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_stock_documents"
down_revision: str | Sequence[str] | None = "0002_master_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("doc_no", sa.String(length=32), nullable=False),
        sa.Column("doc_type", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("remark", sa.String(length=500), nullable=True),
        sa.Column("warehouse_id", sa.String(length=36), nullable=False),
        sa.Column("warehouse_code", sa.String(length=16), nullable=False),
        sa.Column("warehouse_name", sa.String(length=100), nullable=False),
        sa.Column("department_id", sa.String(length=36), nullable=False),
        sa.Column("department_code", sa.String(length=16), nullable=False),
        sa.Column("department_name", sa.String(length=100), nullable=False),
        sa.Column("created_source", sa.String(length=8), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_by_name", sa.String(length=100), nullable=False),
        sa.Column("submitted_by", sa.String(length=36), nullable=True),
        sa.Column("submitted_by_name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.Integer(), nullable=True),
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "doc_type IN ('INBOUND', 'OUTBOUND')", name="ck_stock_documents_doc_type"
        ),
        sa.CheckConstraint("status IN ('DRAFT', 'POSTED')", name="ck_stock_documents_status"),
        sa.CheckConstraint(
            "created_source IN ('WEB', 'API', 'MCP')", name="ck_stock_documents_created_source"
        ),
        sa.CheckConstraint("version >= 1", name="ck_stock_documents_version"),
        sa.CheckConstraint(
            "remark IS NULL OR length(remark) <= 500",
            name="ck_stock_documents_remark_length",
        ),
        sa.CheckConstraint(
            "length(doc_no) BETWEEN 1 AND 32", name="ck_stock_documents_doc_no_length"
        ),
        sa.CheckConstraint(
            "(status = 'DRAFT' AND submitted_at IS NULL AND submitted_by IS NULL) "
            "OR (status = 'POSTED' AND submitted_at IS NOT NULL AND submitted_by IS NOT NULL)",
            name="ck_stock_documents_submitted_pair",
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL AND deleted_by IS NULL) "
            "OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)",
            name="ck_stock_documents_deleted_pair",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
            name="fk_stock_documents_warehouse_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_stock_documents_department_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_stock_documents_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by"],
            ["users.id"],
            name="fk_stock_documents_submitted_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"],
            ["users.id"],
            name="fk_stock_documents_deleted_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_stock_documents_doc_no", "stock_documents", ["doc_no"], unique=True)
    op.create_index(
        "ux_stock_documents_creator_request",
        "stock_documents",
        ["created_by", "request_id"],
        unique=True,
    )
    op.create_index(
        "ix_stock_documents_creator_fingerprint_created",
        "stock_documents",
        ["created_by", "request_fingerprint", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_stock_documents_status_warehouse",
        "stock_documents",
        ["status", "warehouse_id"],
        unique=False,
    )
    op.create_index(
        "ix_stock_documents_status_submitted",
        "stock_documents",
        ["status", "submitted_at"],
        unique=False,
    )
    op.create_index(
        "ix_stock_documents_department_id", "stock_documents", ["department_id"], unique=False
    )
    op.create_index("ix_stock_documents_created_at", "stock_documents", ["created_at"])
    op.create_index("ix_stock_documents_warehouse_id", "stock_documents", ["warehouse_id"])

    op.create_table(
        "stock_document_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.String(length=36), nullable=False),
        sa.Column("material_code", sa.String(length=16), nullable=False),
        sa.Column("material_name", sa.String(length=100), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("quantity_type", sa.String(length=8), nullable=False),
        sa.Column("category_code", sa.String(length=16), nullable=False),
        sa.Column("category_name", sa.String(length=100), nullable=False),
        sa.Column("parent_category_code", sa.String(length=16), nullable=True),
        sa.Column("parent_category_name", sa.String(length=100), nullable=True),
        sa.Column("manufacturer_id", sa.String(length=36), nullable=True),
        sa.Column("manufacturer_code", sa.String(length=16), nullable=True),
        sa.Column("manufacturer_name", sa.String(length=100), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("project_code", sa.String(length=16), nullable=True),
        sa.Column("project_name", sa.String(length=100), nullable=True),
        sa.Column("quantity_milli", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity_milli > 0", name="ck_stock_document_lines_quantity_positive"),
        sa.CheckConstraint("line_no >= 1", name="ck_stock_document_lines_line_no"),
        sa.CheckConstraint(
            "quantity_type IN ('INTEGER', 'DECIMAL')",
            name="ck_stock_document_lines_quantity_type",
        ),
        sa.CheckConstraint(
            "length(material_code) BETWEEN 1 AND 16",
            name="ck_stock_document_lines_material_code_length",
        ),
        sa.CheckConstraint(
            "length(material_name) BETWEEN 1 AND 100",
            name="ck_stock_document_lines_material_name_length",
        ),
        sa.CheckConstraint(
            "length(unit) BETWEEN 1 AND 20", name="ck_stock_document_lines_unit_length"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["stock_documents.id"],
            name="fk_stock_document_lines_document_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["materials.id"],
            name="fk_stock_document_lines_material_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["manufacturer_id"],
            ["manufacturers.id"],
            name="fk_stock_document_lines_manufacturer_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_stock_document_lines_project_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_stock_document_lines_document_material_project",
        "stock_document_lines",
        ["document_id", "material_id", "project_id"],
        unique=True,
        sqlite_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index(
        "ux_stock_document_lines_document_material",
        "stock_document_lines",
        ["document_id", "material_id"],
        unique=True,
        sqlite_where=sa.text("project_id IS NULL"),
    )
    op.create_index(
        "ux_stock_document_lines_document_line_no",
        "stock_document_lines",
        ["document_id", "line_no"],
        unique=True,
    )
    op.create_index(
        "ix_stock_document_lines_document_id", "stock_document_lines", ["document_id"]
    )
    op.create_index(
        "ix_stock_document_lines_material_id", "stock_document_lines", ["material_id"]
    )
    op.create_index("ix_stock_document_lines_project_id", "stock_document_lines", ["project_id"])
    op.create_index(
        "ix_stock_document_lines_manufacturer_id",
        "stock_document_lines",
        ["manufacturer_id"],
    )

    op.create_table(
        "document_counters",
        sa.Column("biz_date", sa.String(length=10), nullable=False),
        sa.Column("doc_kind", sa.String(length=8), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.CheckConstraint("next_value >= 1", name="ck_document_counters_next_value"),
        sa.CheckConstraint("length(biz_date) = 10", name="ck_document_counters_biz_date"),
        sa.CheckConstraint(
            "doc_kind IN ('INBOUND', 'OUTBOUND')", name="ck_document_counters_doc_kind"
        ),
        sa.PrimaryKeyConstraint("biz_date", "doc_kind"),
    )

    op.execute(
        """
        CREATE VIEW v_inventory_balance AS
        SELECT
            d.warehouse_id AS warehouse_id,
            l.material_id AS material_id,
            SUM(
                l.quantity_milli * CASE
                    WHEN d.doc_type = 'INBOUND' THEN 1
                    WHEN d.doc_type = 'OUTBOUND' THEN -1
                END
            ) AS balance_milli
        FROM stock_documents d
        JOIN stock_document_lines l ON l.document_id = d.id
        WHERE d.status = 'POSTED' AND d.deleted_at IS NULL
        GROUP BY d.warehouse_id, l.material_id
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_stock_documents_posted_immutable
        BEFORE UPDATE ON stock_documents
        WHEN OLD.status = 'POSTED' AND (
            NEW.doc_no IS NOT OLD.doc_no
            OR NEW.doc_type IS NOT OLD.doc_type
            OR NEW.status IS NOT OLD.status
            OR NEW.remark IS NOT OLD.remark
            OR NEW.warehouse_id IS NOT OLD.warehouse_id
            OR NEW.warehouse_code IS NOT OLD.warehouse_code
            OR NEW.warehouse_name IS NOT OLD.warehouse_name
            OR NEW.department_id IS NOT OLD.department_id
            OR NEW.department_code IS NOT OLD.department_code
            OR NEW.department_name IS NOT OLD.department_name
            OR NEW.created_source IS NOT OLD.created_source
            OR NEW.request_id IS NOT OLD.request_id
            OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
            OR NEW.created_by IS NOT OLD.created_by
            OR NEW.created_by_name IS NOT OLD.created_by_name
            OR NEW.created_at IS NOT OLD.created_at
            OR NEW.submitted_by IS NOT OLD.submitted_by
            OR NEW.submitted_by_name IS NOT OLD.submitted_by_name
            OR NEW.submitted_at IS NOT OLD.submitted_at
            OR (OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NOT OLD.deleted_at)
            OR (OLD.deleted_at IS NOT NULL AND NEW.deleted_by IS NOT OLD.deleted_by)
            OR (OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL)
        )
        BEGIN
            SELECT RAISE(ABORT, 'posted stock document is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_documents_identity_immutable
        BEFORE UPDATE ON stock_documents
        WHEN NEW.doc_no IS NOT OLD.doc_no
            OR NEW.doc_type IS NOT OLD.doc_type
            OR NEW.created_source IS NOT OLD.created_source
            OR NEW.request_id IS NOT OLD.request_id
            OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
            OR NEW.created_by IS NOT OLD.created_by
            OR NEW.created_by_name IS NOT OLD.created_by_name
            OR NEW.created_at IS NOT OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'stock document identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_documents_posted_no_delete
        BEFORE DELETE ON stock_documents
        WHEN OLD.status = 'POSTED'
        BEGIN
            SELECT RAISE(ABORT, 'posted stock document cannot be deleted');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_documents_posted_requires_lines
        BEFORE UPDATE OF status ON stock_documents
        WHEN NEW.status = 'POSTED' AND OLD.status = 'DRAFT'
            AND (SELECT COUNT(*) FROM stock_document_lines WHERE document_id = NEW.id) = 0
        BEGIN
            SELECT RAISE(ABORT, 'stock document requires at least one line');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_document_lines_posted_no_update
        BEFORE UPDATE ON stock_document_lines
        WHEN (SELECT status FROM stock_documents WHERE id = OLD.document_id) = 'POSTED'
        BEGIN
            SELECT RAISE(ABORT, 'posted stock document lines are immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_document_lines_posted_no_delete
        BEFORE DELETE ON stock_document_lines
        WHEN (SELECT status FROM stock_documents WHERE id = OLD.document_id) = 'POSTED'
        BEGIN
            SELECT RAISE(ABORT, 'posted stock document lines cannot be deleted');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_document_lines_manufacturer_insert
        BEFORE INSERT ON stock_document_lines
        WHEN (
            (SELECT doc_type FROM stock_documents WHERE id = NEW.document_id) = 'INBOUND'
            AND NEW.manufacturer_id IS NULL
        ) OR (
            (SELECT doc_type FROM stock_documents WHERE id = NEW.document_id) = 'OUTBOUND'
            AND NEW.manufacturer_id IS NOT NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'manufacturer rule violated');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_document_lines_manufacturer_update
        BEFORE UPDATE OF manufacturer_id ON stock_document_lines
        WHEN (
            (SELECT doc_type FROM stock_documents WHERE id = NEW.document_id) = 'INBOUND'
            AND NEW.manufacturer_id IS NULL
        ) OR (
            (SELECT doc_type FROM stock_documents WHERE id = NEW.document_id) = 'OUTBOUND'
            AND NEW.manufacturer_id IS NOT NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'manufacturer rule violated');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_document_lines_integer_quantity_insert
        BEFORE INSERT ON stock_document_lines
        WHEN (SELECT quantity_type FROM materials WHERE id = NEW.material_id) = 'INTEGER'
            AND NEW.quantity_milli % 1000 <> 0
        BEGIN
            SELECT RAISE(ABORT, 'integer material requires integer quantity');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stock_document_lines_project_active_insert
        BEFORE INSERT ON stock_document_lines
        WHEN NEW.project_id IS NOT NULL
            AND (SELECT is_active FROM projects WHERE id = NEW.project_id) IS NOT 1
        BEGIN
            SELECT RAISE(ABORT, 'project is not active');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_stock_document_lines_project_active_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_document_lines_integer_quantity_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_document_lines_manufacturer_update")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_document_lines_manufacturer_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_document_lines_posted_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_document_lines_posted_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_documents_posted_requires_lines")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_documents_identity_immutable")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_documents_posted_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_documents_posted_immutable")
    op.execute("DROP VIEW IF EXISTS v_inventory_balance")

    op.drop_table("document_counters")

    op.drop_index(
        "ix_stock_document_lines_manufacturer_id", table_name="stock_document_lines"
    )
    op.drop_index("ix_stock_document_lines_project_id", table_name="stock_document_lines")
    op.drop_index("ix_stock_document_lines_material_id", table_name="stock_document_lines")
    op.drop_index("ix_stock_document_lines_document_id", table_name="stock_document_lines")
    op.drop_index(
        "ux_stock_document_lines_document_line_no", table_name="stock_document_lines"
    )
    op.drop_index(
        "ux_stock_document_lines_document_material", table_name="stock_document_lines"
    )
    op.drop_index(
        "ux_stock_document_lines_document_material_project",
        table_name="stock_document_lines",
    )
    op.drop_table("stock_document_lines")

    op.drop_index("ix_stock_documents_warehouse_id", table_name="stock_documents")
    op.drop_index("ix_stock_documents_created_at", table_name="stock_documents")
    op.drop_index("ix_stock_documents_department_id", table_name="stock_documents")
    op.drop_index("ix_stock_documents_status_submitted", table_name="stock_documents")
    op.drop_index("ix_stock_documents_status_warehouse", table_name="stock_documents")
    op.drop_index(
        "ix_stock_documents_creator_fingerprint_created", table_name="stock_documents"
    )
    op.drop_index("ux_stock_documents_creator_request", table_name="stock_documents")
    op.drop_index("ux_stock_documents_doc_no", table_name="stock_documents")
    op.drop_table("stock_documents")
