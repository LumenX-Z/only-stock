"""SQLAlchemy 声明式基类；模型在 P2 及后续阶段注册到同一 metadata。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
