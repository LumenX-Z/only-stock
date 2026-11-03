"""仓库模型。"""

from sqlalchemy import CheckConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.masterdata.base import NAME_MAX_LENGTH, CodedEntity


class Warehouse(CodedEntity, Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        CheckConstraint("is_active IN (0, 1)", name="ck_warehouses_is_active"),
        CheckConstraint("version >= 1", name="ck_warehouses_version"),
        CheckConstraint(
            f"length(name) BETWEEN 1 AND {NAME_MAX_LENGTH}", name="ck_warehouses_name_length"
        ),
        Index("ux_warehouses_code", "code", unique=True),
        Index("ux_warehouses_name", "name", unique=True),
    )

    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
