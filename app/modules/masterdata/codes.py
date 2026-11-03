"""六类实体统一编码生成。

依据 docs/05-数据模型与实体约束.md 6.2：编码为“前缀-固定 5 位序号”，创建事务内
生成；序号达到 99999 后不得回绕，创建失败；编码、业务记录与计数器在同一
``BEGIN IMMEDIATE`` 事务内提交，任一失败整笔回滚。
"""

from sqlalchemy.orm import Session

from app.errors import AppError
from app.modules.masterdata.base import CodeCounter
from app.modules.masterdata.registry import EntitySpec

CODE_SEQUENCE_WIDTH = 5
MAX_CODE_SEQUENCE = 99_999


def format_code(prefix: str, value: int) -> str:
    """把序号格式化为 ``前缀-00001``；非法序号直接拒绝。"""
    if value < 1 or value > MAX_CODE_SEQUENCE:
        raise ValueError("编码序号必须在 1 到 99999 之间")
    return f"{prefix}-{value:0{CODE_SEQUENCE_WIDTH}d}"


def next_entity_code(session: Session, spec: EntitySpec) -> str:
    """在调用方写事务内取下一个编码。

    计数器不存在时从 1 开始；序号 99999 仍可使用，下一次创建因超过上限失败，
    且不会修改计数器。任一步失败由调用方事务整体回滚。
    """
    counter = session.get(CodeCounter, spec.key)
    if counter is None:
        value = 1
        counter = CodeCounter(entity_type=spec.key, next_value=value + 1)
        session.add(counter)
    else:
        value = counter.next_value
        if value > MAX_CODE_SEQUENCE:
            raise AppError(
                "CODE_SEQUENCE_EXHAUSTED",
                f"{spec.label}编码序号已达到 {MAX_CODE_SEQUENCE}，无法继续创建",
                status_code=409,
                resource=spec.key,
                items=[{"limit": MAX_CODE_SEQUENCE, "prefix": spec.prefix}],
            )
        counter.next_value = value + 1
        session.add(counter)
    session.flush()
    return format_code(spec.prefix, value)
