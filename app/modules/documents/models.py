"""单据头、单据明细与单号计数器模型。

依据 docs/05-数据模型与实体约束.md 6.5：
- ``doc_type`` 仅 ``INBOUND`` / ``OUTBOUND``，``status`` 仅 ``DRAFT`` / ``POSTED``；
- ``doc_no`` 唯一，``(created_by, request_id)`` 唯一，并建立
  ``(created_by, request_fingerprint, created_at)`` 索引供 MCP 窗口去重；
- 明细保存物资、分类、厂家、项目、仓库与部门的显示快照以及逐行备注；
- 同一单据内同一物资在同一项目（空项目视为同一取值）下只能有一行；
- 已生效单据、明细禁止更新或物理删除，数据库触发器作为服务层之外的第二道保护。

依据 docs/06-库存计算与单据业务流程.md 7.1：库存只按“仓库 + 物资”汇总，
草稿与已软删除单不计入。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UtcDateTime, new_uuid, utc_now

DOC_NO_MAX_LENGTH = 32
REMARK_MAX_LENGTH = 500
REQUEST_FINGERPRINT_MAX_LENGTH = 64
DOCUMENT_COUNTER_KIND_MAX_LENGTH = 8


class DocType(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class DocStatus(StrEnum):
    DRAFT = "DRAFT"
    POSTED = "POSTED"


class CreatedSource(StrEnum):
    WEB = "WEB"
    API = "API"
    MCP = "MCP"


class StockDocument(Base):
    """普通出入库单头；草稿可编辑，生效后只允许软删除标记。"""

    __tablename__ = "stock_documents"
    __table_args__ = (
        CheckConstraint("doc_type IN ('INBOUND', 'OUTBOUND')", name="ck_stock_documents_doc_type"),
        CheckConstraint("status IN ('DRAFT', 'POSTED')", name="ck_stock_documents_status"),
        CheckConstraint(
            "created_source IN ('WEB', 'API', 'MCP')", name="ck_stock_documents_created_source"
        ),
        CheckConstraint("version >= 1", name="ck_stock_documents_version"),
        CheckConstraint("length(doc_no) BETWEEN 1 AND 32", name="ck_stock_documents_doc_no_length"),
        CheckConstraint(
            "(status = 'DRAFT' AND submitted_at IS NULL AND submitted_by IS NULL) "
            "OR (status = 'POSTED' AND submitted_at IS NOT NULL AND submitted_by IS NOT NULL)",
            name="ck_stock_documents_submitted_pair",
        ),
        CheckConstraint(
            "(deleted_at IS NULL AND deleted_by IS NULL) "
            "OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL)",
            name="ck_stock_documents_deleted_pair",
        ),
        Index("ux_stock_documents_doc_no", "doc_no", unique=True),
        Index(
            "ux_stock_documents_creator_request",
            "created_by",
            "request_id",
            unique=True,
        ),
        Index(
            "ix_stock_documents_creator_fingerprint_created",
            "created_by",
            "request_fingerprint",
            "created_at",
        ),
        Index("ix_stock_documents_status_warehouse", "status", "warehouse_id"),
        Index("ix_stock_documents_status_submitted", "status", "submitted_at"),
        Index("ix_stock_documents_department_id", "department_id"),
        Index("ix_stock_documents_created_at", "created_at"),
        Index("ix_stock_documents_warehouse_id", "warehouse_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    doc_no: Mapped[str] = mapped_column(String(DOC_NO_MAX_LENGTH), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default=DocStatus.DRAFT.value)

    warehouse_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("warehouses.id", name="fk_stock_documents_warehouse_id", ondelete="RESTRICT"),
        nullable=False,
    )
    warehouse_code: Mapped[str] = mapped_column(String(16), nullable=False)
    warehouse_name: Mapped[str] = mapped_column(String(100), nullable=False)
    department_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("departments.id", name="fk_stock_documents_department_id", ondelete="RESTRICT"),
        nullable=False,
    )
    department_code: Mapped[str] = mapped_column(String(16), nullable=False)
    department_name: Mapped[str] = mapped_column(String(100), nullable=False)

    created_source: Mapped[str] = mapped_column(String(8), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(
        String(REQUEST_FINGERPRINT_MAX_LENGTH), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", name="fk_stock_documents_created_by", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_name: Mapped[str] = mapped_column(String(100), nullable=False)
    submitted_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", name="fk_stock_documents_submitted_by", ondelete="RESTRICT"),
        nullable=True,
    )
    submitted_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )
    submitted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", name="fk_stock_documents_deleted_by", ondelete="RESTRICT"),
        nullable=True,
    )

    lines: Mapped[list["StockDocumentLine"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="StockDocumentLine.line_no",
    )


class StockDocumentLine(Base):
    """单据明细；数量以千分比整数保存，并保留历史显示快照。"""

    __tablename__ = "stock_document_lines"
    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="ck_stock_document_lines_quantity_positive"),
        CheckConstraint("line_no >= 1", name="ck_stock_document_lines_line_no"),
        CheckConstraint(
            "quantity_type IN ('INTEGER', 'DECIMAL')",
            name="ck_stock_document_lines_quantity_type",
        ),
        CheckConstraint(
            "length(material_code) BETWEEN 1 AND 16",
            name="ck_stock_document_lines_material_code_length",
        ),
        CheckConstraint(
            "length(material_name) BETWEEN 1 AND 100",
            name="ck_stock_document_lines_material_name_length",
        ),
        CheckConstraint(
            "length(unit) BETWEEN 1 AND 20", name="ck_stock_document_lines_unit_length"
        ),
        CheckConstraint(
            "remark IS NULL OR length(remark) <= 500",
            name="ck_stock_document_lines_remark_length",
        ),
        Index(
            "ux_stock_document_lines_document_material_project",
            "document_id",
            "material_id",
            "project_id",
            unique=True,
            sqlite_where=text("project_id IS NOT NULL"),
        ),
        Index(
            "ux_stock_document_lines_document_material",
            "document_id",
            "material_id",
            unique=True,
            sqlite_where=text("project_id IS NULL"),
        ),
        Index(
            "ux_stock_document_lines_document_line_no",
            "document_id",
            "line_no",
            unique=True,
        ),
        Index("ix_stock_document_lines_document_id", "document_id"),
        Index("ix_stock_document_lines_material_id", "material_id"),
        Index("ix_stock_document_lines_project_id", "project_id"),
        Index("ix_stock_document_lines_manufacturer_id", "manufacturer_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "stock_documents.id",
            name="fk_stock_document_lines_document_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    material_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("materials.id", name="fk_stock_document_lines_material_id", ondelete="RESTRICT"),
        nullable=False,
    )
    material_code: Mapped[str] = mapped_column(String(16), nullable=False)
    material_name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity_type: Mapped[str] = mapped_column(String(8), nullable=False)
    category_code: Mapped[str] = mapped_column(String(16), nullable=False)
    category_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_category_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    parent_category_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    manufacturer_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "manufacturers.id", name="fk_stock_document_lines_manufacturer_id", ondelete="RESTRICT"
        ),
        nullable=True,
    )
    manufacturer_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    manufacturer_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    project_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("projects.id", name="fk_stock_document_lines_project_id", ondelete="RESTRICT"),
        nullable=True,
    )
    project_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    quantity_milli: Mapped[int] = mapped_column(Integer, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(REMARK_MAX_LENGTH), nullable=True)

    document: Mapped[StockDocument] = relationship(back_populates="lines")


class DocumentCounter(Base):
    """按业务日期与单据类型生成可读单号的计数器；组合主键。"""

    __tablename__ = "document_counters"
    __table_args__ = (
        CheckConstraint("next_value >= 1", name="ck_document_counters_next_value"),
        CheckConstraint("length(biz_date) = 10", name="ck_document_counters_biz_date"),
        CheckConstraint(
            "doc_kind IN ('INBOUND', 'OUTBOUND')", name="ck_document_counters_doc_kind"
        ),
    )

    biz_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    doc_kind: Mapped[str] = mapped_column(
        String(DOCUMENT_COUNTER_KIND_MAX_LENGTH), primary_key=True
    )
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
