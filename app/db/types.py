"""统一的时间与主键存储约定。

依据 docs/05-数据模型与实体约束.md 6.1：所有主键使用 UUID 字符串；时间在
整个代码库统一使用 epoch 毫秒存储，对外按 UTC RFC 3339 输出。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Integer
from sqlalchemy.types import TypeDecorator

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def utc_now() -> datetime:
    """当前 UTC 时间，精度截断到毫秒，与数据库存储精度一致。"""
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def new_uuid() -> str:
    """新的 UUID 字符串主键。"""
    return str(uuid4())


class UtcDateTime(TypeDecorator[datetime]):
    """以 epoch 毫秒 INTEGER 保存、以 aware UTC datetime 读出的时间类型。"""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> int | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError("时间字段必须是 datetime")
        if value.tzinfo is None:
            raise ValueError("时间字段必须带时区")
        utc_value = value.astimezone(UTC)
        delta = utc_value - _EPOCH
        return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000

    def process_result_value(self, value: int | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return _EPOCH + timedelta(milliseconds=int(value))
