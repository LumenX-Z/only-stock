"""物资模型。

依据 docs/05-数据模型与实体约束.md 6.4、6.6、6.8：物资必须归属一个一级或二级
分类，名称在直接所属分类内唯一；数量类型仅 ``INTEGER`` / ``DECIMAL``。
"""

from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.masterdata.base import NAME_MAX_LENGTH, UNIT_MAX_LENGTH, CodedEntity


class QuantityType(StrEnum):
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"


class Material(CodedEntity, Base):
    __tablename__ = "materials"
    __table_args__ = (
        CheckConstraint(
            "quantity_type IN ('INTEGER', 'DECIMAL')", name="ck_materials_quantity_type"
        ),
        CheckConstraint("is_active IN (0, 1)", name="ck_materials_is_active"),
        CheckConstraint("version >= 1", name="ck_materials_version"),
        CheckConstraint(
            f"length(name) BETWEEN 1 AND {NAME_MAX_LENGTH}", name="ck_materials_name_length"
        ),
        CheckConstraint(
            f"length(unit) BETWEEN 1 AND {UNIT_MAX_LENGTH}", name="ck_materials_unit_length"
        ),
        Index("ux_materials_code", "code", unique=True),
        Index("ux_materials_category_name", "category_id", "name", unique=True),
        Index("ix_materials_category_id", "category_id"),
    )

    category_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("categories.id", name="fk_materials_category_id", ondelete="RESTRICT"),
        nullable=False,
    )
    unit: Mapped[str] = mapped_column(String(UNIT_MAX_LENGTH), nullable=False)
    quantity_type: Mapped[str] = mapped_column(String(8), nullable=False)
