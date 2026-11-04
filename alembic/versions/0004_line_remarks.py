"""把备注从单据头迁移到明细行。

Revision ID: 0004_line_remarks
Revises: 0003_stock_documents
Create Date: 2026-11-04

依据 docs/05-数据模型与实体约束.md 6.5 与 docs/16-验收标准.md ACC-DATA-031：
- ``stock_documents`` 移除 ``remark``；
- ``stock_document_lines`` 新增可空 ``remark``，最多 500 字符；
- 升级时把原单据头备注复制到该单每一行，避免丢失已有文本；
- 重建表时保留全部检查约束、外键、索引、触发器和库存视图。

SQLite 不支持直接删除带 CHECK 约束的列，因此本迁移在关闭外键检查的
Alembic 连接中重建两张表；索引与触发器在重建后按 0003 的定义恢复。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_line_remarks"
down_revision: str | Sequence[str] | None = "0003_stock_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOCUMENT_COLUMNS = (
    "id, doc_no, doc_type, status, warehouse_id, warehouse_code, warehouse_name, "
    "department_id, department_code, department_name, created_source, request_id, "
    "request_fingerprint, version, created_by, created_by_name, submitted_by, "
    "submitted_by_name, created_at, updated_at, submitted_at, deleted_at, deleted_by"
)

_LINE_COLUMNS = (
    "id, document_id, line_no, material_id, material_code, material_name, unit, "
    "quantity_type, category_code, category_name, parent_category_code, "
    "parent_category_name, manufacturer_id, manufacturer_code, manufacturer_name, "
    "project_id, project_code, project_name, quantity_milli"
)


def _create_documents_table(*, with_remark: bool) -> str:
    remark_column = "remark VARCHAR(500)," if with_remark else ""
    remark_check = (
        "CONSTRAINT ck_stock_documents_remark_length "
        "CHECK (remark IS NULL OR length(remark) <= 500),"
        if with_remark
        else ""
    )
    return f"""
        CREATE TABLE stock_documents_new (
            id VARCHAR(36) NOT NULL,
            doc_no VARCHAR(32) NOT NULL,
            doc_type VARCHAR(8) NOT NULL,
            status VARCHAR(8) NOT NULL,
            {remark_column}
            warehouse_id VARCHAR(36) NOT NULL,
            warehouse_code VARCHAR(16) NOT NULL,
            warehouse_name VARCHAR(100) NOT NULL,
            department_id VARCHAR(36) NOT NULL,
            department_code VARCHAR(16) NOT NULL,
            department_name VARCHAR(100) NOT NULL,
            created_source VARCHAR(8) NOT NULL,
            request_id VARCHAR(36) NOT NULL,
            request_fingerprint VARCHAR(64) NOT NULL,
            version INTEGER NOT NULL,
            created_by VARCHAR(36) NOT NULL,
            created_by_name VARCHAR(100) NOT NULL,
            submitted_by VARCHAR(36),
            submitted_by_name VARCHAR(100),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            submitted_at INTEGER,
            deleted_at INTEGER,
            deleted_by VARCHAR(36),
            CONSTRAINT ck_stock_documents_doc_type CHECK (doc_type IN ('INBOUND', 'OUTBOUND')),
            CONSTRAINT ck_stock_documents_status CHECK (status IN ('DRAFT', 'POSTED')),
            CONSTRAINT ck_stock_documents_created_source
                CHECK (created_source IN ('WEB', 'API', 'MCP')),
            CONSTRAINT ck_stock_documents_version CHECK (version >= 1),
            {remark_check}
            CONSTRAINT ck_stock_documents_doc_no_length
                CHECK (length(doc_no) BETWEEN 1 AND 32),
            CONSTRAINT ck_stock_documents_submitted_pair CHECK (
                (status = 'DRAFT' AND submitted_at IS NULL AND submitted_by IS NULL)
                OR (status = 'POSTED' AND submitted_at IS NOT NULL AND submitted_by IS NOT NULL)
            ),
            CONSTRAINT ck_stock_documents_deleted_pair CHECK (
                (deleted_at IS NULL AND deleted_by IS NULL)
                OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)
            ),
            CONSTRAINT fk_stock_documents_warehouse_id
                FOREIGN KEY (warehouse_id) REFERENCES warehouses (id) ON DELETE RESTRICT,
            CONSTRAINT fk_stock_documents_department_id
                FOREIGN KEY (department_id) REFERENCES departments (id) ON DELETE RESTRICT,
            CONSTRAINT fk_stock_documents_created_by
                FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_stock_documents_submitted_by
                FOREIGN KEY (submitted_by) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_stock_documents_deleted_by
                FOREIGN KEY (deleted_by) REFERENCES users (id) ON DELETE RESTRICT,
            PRIMARY KEY (id)
        )
    """


def _create_lines_table(*, with_remark: bool) -> str:
    remark_column = "remark VARCHAR(500)," if with_remark else ""
    remark_check = (
        "CONSTRAINT ck_stock_document_lines_remark_length "
        "CHECK (remark IS NULL OR length(remark) <= 500),"
        if with_remark
        else ""
    )
    return f"""
        CREATE TABLE stock_document_lines_new (
            id VARCHAR(36) NOT NULL,
            document_id VARCHAR(36) NOT NULL,
            line_no INTEGER NOT NULL,
            material_id VARCHAR(36) NOT NULL,
            material_code VARCHAR(16) NOT NULL,
            material_name VARCHAR(100) NOT NULL,
            unit VARCHAR(20) NOT NULL,
            quantity_type VARCHAR(8) NOT NULL,
            category_code VARCHAR(16) NOT NULL,
            category_name VARCHAR(100) NOT NULL,
            parent_category_code VARCHAR(16),
            parent_category_name VARCHAR(100),
            manufacturer_id VARCHAR(36),
            manufacturer_code VARCHAR(16),
            manufacturer_name VARCHAR(100),
            project_id VARCHAR(36),
            project_code VARCHAR(16),
            project_name VARCHAR(100),
            quantity_milli INTEGER NOT NULL,
            {remark_column}
            CONSTRAINT ck_stock_document_lines_quantity_positive CHECK (quantity_milli > 0),
            CONSTRAINT ck_stock_document_lines_line_no CHECK (line_no >= 1),
            CONSTRAINT ck_stock_document_lines_quantity_type
                CHECK (quantity_type IN ('INTEGER', 'DECIMAL')),
            CONSTRAINT ck_stock_document_lines_material_code_length
                CHECK (length(material_code) BETWEEN 1 AND 16),
            CONSTRAINT ck_stock_document_lines_material_name_length
                CHECK (length(material_name) BETWEEN 1 AND 100),
            CONSTRAINT ck_stock_document_lines_unit_length
                CHECK (length(unit) BETWEEN 1 AND 20),
            {remark_check}
            CONSTRAINT fk_stock_document_lines_document_id
                FOREIGN KEY (document_id) REFERENCES stock_documents (id) ON DELETE CASCADE,
            CONSTRAINT fk_stock_document_lines_material_id
                FOREIGN KEY (material_id) REFERENCES materials (id) ON DELETE RESTRICT,
            CONSTRAINT fk_stock_document_lines_manufacturer_id
                FOREIGN KEY (manufacturer_id) REFERENCES manufacturers (id) ON DELETE RESTRICT,
            CONSTRAINT fk_stock_document_lines_project_id
                FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE RESTRICT,
            PRIMARY KEY (id)
        )
    """


def _create_document_indexes() -> None:
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
    )
    op.create_index(
        "ix_stock_documents_status_warehouse",
        "stock_documents",
        ["status", "warehouse_id"],
    )
    op.create_index(
        "ix_stock_documents_status_submitted",
        "stock_documents",
        ["status", "submitted_at"],
    )
    op.create_index("ix_stock_documents_department_id", "stock_documents", ["department_id"])
    op.create_index("ix_stock_documents_created_at", "stock_documents", ["created_at"])
    op.create_index("ix_stock_documents_warehouse_id", "stock_documents", ["warehouse_id"])


def _create_line_indexes() -> None:
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
    op.create_index("ix_stock_document_lines_document_id", "stock_document_lines", ["document_id"])
    op.create_index("ix_stock_document_lines_material_id", "stock_document_lines", ["material_id"])
    op.create_index("ix_stock_document_lines_project_id", "stock_document_lines", ["project_id"])
    op.create_index(
        "ix_stock_document_lines_manufacturer_id",
        "stock_document_lines",
        ["manufacturer_id"],
    )


def _create_document_triggers(*, with_remark: bool) -> None:
    remark_condition = "NEW.remark IS NOT OLD.remark OR " if with_remark else ""
    op.execute(
        f"""
        CREATE TRIGGER trg_stock_documents_posted_immutable
        BEFORE UPDATE ON stock_documents
        WHEN OLD.status = 'POSTED' AND (
            NEW.doc_no IS NOT OLD.doc_no
            OR NEW.doc_type IS NOT OLD.doc_type
            OR NEW.status IS NOT OLD.status
            OR {remark_condition}
            NEW.warehouse_id IS NOT OLD.warehouse_id
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


def _create_line_triggers() -> None:
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


def _create_inventory_view() -> None:
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


def _drop_triggers() -> None:
    """重建表前先移除 0003 的触发器，避免重命名时触发器引用旧表。"""
    for name in (
        "trg_stock_documents_posted_immutable",
        "trg_stock_documents_identity_immutable",
        "trg_stock_documents_posted_no_delete",
        "trg_stock_documents_posted_requires_lines",
        "trg_stock_document_lines_posted_no_update",
        "trg_stock_document_lines_posted_no_delete",
        "trg_stock_document_lines_manufacturer_insert",
        "trg_stock_document_lines_manufacturer_update",
        "trg_stock_document_lines_integer_quantity_insert",
        "trg_stock_document_lines_project_active_insert",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")


def upgrade() -> None:
    # Alembic 连接未安装应用的 PRAGMA 事件，foreign_keys 默认为 OFF；
    # 重建父表期间不触发外键级联，重建完成后再恢复触发器。
    _drop_triggers()
    op.execute("DROP VIEW IF EXISTS v_inventory_balance")

    # 先重建明细并带上原单据头备注，再重建单据头。
    op.execute(_create_lines_table(with_remark=True))
    op.execute(
        f"""
        INSERT INTO stock_document_lines_new ({_LINE_COLUMNS}, remark)
        SELECT {_LINE_COLUMNS},
            (SELECT d.remark FROM stock_documents d WHERE d.id = stock_document_lines.document_id)
        FROM stock_document_lines
        """
    )
    op.execute("DROP TABLE stock_document_lines")
    op.execute("ALTER TABLE stock_document_lines_new RENAME TO stock_document_lines")

    op.execute(_create_documents_table(with_remark=False))
    op.execute(
        f"""
        INSERT INTO stock_documents_new ({_DOCUMENT_COLUMNS})
        SELECT {_DOCUMENT_COLUMNS} FROM stock_documents
        """
    )
    op.execute("DROP TABLE stock_documents")
    op.execute("ALTER TABLE stock_documents_new RENAME TO stock_documents")

    _create_document_indexes()
    _create_line_indexes()
    _create_document_triggers(with_remark=False)
    _create_line_triggers()
    _create_inventory_view()


def downgrade() -> None:
    _drop_triggers()
    op.execute("DROP VIEW IF EXISTS v_inventory_balance")

    # 降级无法恢复每行不同的备注，取该单第一行非空备注写回单据头。
    op.execute(_create_documents_table(with_remark=True))
    op.execute(
        f"""
        INSERT INTO stock_documents_new ({_DOCUMENT_COLUMNS}, remark)
        SELECT {_DOCUMENT_COLUMNS},
            (
                SELECT l.remark
                FROM stock_document_lines l
                WHERE l.document_id = stock_documents.id AND l.remark IS NOT NULL
                ORDER BY l.line_no
                LIMIT 1
            )
        FROM stock_documents
        """
    )
    op.execute("DROP TABLE stock_documents")
    op.execute("ALTER TABLE stock_documents_new RENAME TO stock_documents")

    op.execute(_create_lines_table(with_remark=False))
    op.execute(
        f"""
        INSERT INTO stock_document_lines_new ({_LINE_COLUMNS})
        SELECT {_LINE_COLUMNS} FROM stock_document_lines
        """
    )
    op.execute("DROP TABLE stock_document_lines")
    op.execute("ALTER TABLE stock_document_lines_new RENAME TO stock_document_lines")

    _create_document_indexes()
    _create_line_indexes()
    _create_document_triggers(with_remark=True)
    _create_line_triggers()
    _create_inventory_view()
