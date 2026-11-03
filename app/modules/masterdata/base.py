"""六类基础资料共用的声明式字段与编码计数器。

依据 docs/05-数据模型与实体约束.md 6.2：六类实体主键为 UUID 字符串，``code``
由系统生成且唯一不可修改，全部记录只停用/删除、不改写历史；``code_counters``
按实体类型保存下一个可用序号，只在写事务内更新。
"""

from datetime import datetime

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime, new_uuid, utc_now

CODE_MAX_LENGTH = 16
NAME_MAX_LENGTH = 100
UNIT_MAX_LENGTH = 20
ENTITY_KEY_MAX_LENGTH = 32


class CodedEntity:
    """六类基础资料共有字段（声明式 mixin）。"""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(CODE_MAX_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class CodeCounter(Base):
    """六类实体各自的编码序号计数器；主键为实体类型。"""

    __tablename__ = "code_counters"

    entity_type: Mapped[str] = mapped_column(String(ENTITY_KEY_MAX_LENGTH), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
