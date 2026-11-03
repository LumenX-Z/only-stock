"""分类模型。

依据 docs/05-数据模型与实体约束.md 6.4、6.8：分类严格两级；一级名称全局唯一，
二级名称在同一父分类内唯一。SQLite 唯一索引把多个 NULL 视为不同值，因此一级
名称必须使用 ``parent_id IS NULL`` 的部分唯一索引，二级使用 ``parent_id IS NOT NULL``。
"""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.masterdata.base import NAME_MAX_LENGTH, CodedEntity

CATEGORY_LEVEL_ONE = 1
CATEGORY_LEVEL_TWO = 2


class Category(CodedEntity, Base):
    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint("level IN (1, 2)", name="ck_categories_level"),
        CheckConstraint(
            "(level = 1 AND parent_id IS NULL) OR (level = 2 AND parent_id IS NOT NULL)",
            name="ck_categories_parent_level",
        ),
        CheckConstraint("is_active IN (0, 1)", name="ck_categories_is_active"),
        CheckConstraint("version >= 1", name="ck_categories_version"),
        CheckConstraint(
            f"length(name) BETWEEN 1 AND {NAME_MAX_LENGTH}", name="ck_categories_name_length"
        ),
        Index("ux_categories_code", "code", unique=True),
        Index(
            "ux_categories_name_level1",
            "name",
            unique=True,
            sqlite_where=text("parent_id IS NULL"),
        ),
        Index(
            "ux_categories_parent_name_level2",
            "parent_id",
            "name",
            unique=True,
            sqlite_where=text("parent_id IS NOT NULL"),
        ),
        Index("ix_categories_parent_id", "parent_id"),
    )

    parent_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("categories.id", name="fk_categories_parent_id", ondelete="RESTRICT"),
        nullable=True,
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)
