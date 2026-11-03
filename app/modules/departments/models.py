"""部门模型。"""

from sqlalchemy import CheckConstraint, Index

from app.db.base import Base
from app.modules.masterdata.base import NAME_MAX_LENGTH, CodedEntity


class Department(CodedEntity, Base):
    __tablename__ = "departments"
    __table_args__ = (
        CheckConstraint("is_active IN (0, 1)", name="ck_departments_is_active"),
        CheckConstraint("version >= 1", name="ck_departments_version"),
        CheckConstraint(
            f"length(name) BETWEEN 1 AND {NAME_MAX_LENGTH}", name="ck_departments_name_length"
        ),
        Index("ux_departments_code", "code", unique=True),
        Index("ux_departments_name", "name", unique=True),
    )
