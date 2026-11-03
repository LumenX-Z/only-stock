"""厂家模型。"""

from sqlalchemy import CheckConstraint, Index

from app.db.base import Base
from app.modules.masterdata.base import NAME_MAX_LENGTH, CodedEntity


class Manufacturer(CodedEntity, Base):
    __tablename__ = "manufacturers"
    __table_args__ = (
        CheckConstraint("is_active IN (0, 1)", name="ck_manufacturers_is_active"),
        CheckConstraint("version >= 1", name="ck_manufacturers_version"),
        CheckConstraint(
            f"length(name) BETWEEN 1 AND {NAME_MAX_LENGTH}", name="ck_manufacturers_name_length"
        ),
        Index("ux_manufacturers_code", "code", unique=True),
        Index("ux_manufacturers_name", "name", unique=True),
    )
