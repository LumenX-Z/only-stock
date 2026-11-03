"""六类基础资料名称规范化与唯一性校验。

依据 docs/05-数据模型与实体约束.md 6.4、6.7、6.8：
- 仅去除首尾空白后保存和精确比较，不折叠大小写、不删除内部空白；
- 停用记录仍参与查重并占用名称；
- 一级分类名称全局唯一，二级分类名称在父分类内唯一，物资名称在直接所属分类内唯一；
- 服务层查重与数据库唯一索引共同兜底并发创建，重复返回 409 ``NAME_CONFLICT``。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.errors import AppError
from app.modules.masterdata.base import NAME_MAX_LENGTH
from app.modules.masterdata.registry import (
    NAME_SCOPE_CATEGORY,
    NAME_SCOPE_GLOBAL,
    NAME_SCOPE_MATERIAL,
    EntitySpec,
)


def normalize_name(value: str, *, field: str = "name") -> str:
    """去首尾空白后校验；不做其他字符变换。"""
    normalized = value.strip()
    if not normalized:
        reason = "名称不能为空"
    elif len(normalized) > NAME_MAX_LENGTH:
        reason = f"名称不能超过 {NAME_MAX_LENGTH} 位"
    elif _contains_control(normalized):
        reason = "名称不能包含控制字符"
    else:
        return normalized
    raise AppError(
        "VALIDATION_ERROR",
        "请求参数校验失败",
        status_code=422,
        items=[{"field": field, "reason": reason}],
    )


def find_name_conflict(
    session: Session,
    spec: EntitySpec,
    name: str,
    *,
    parent_id: str | None = None,
    category_id: str | None = None,
) -> Base | None:
    """按名称唯一范围查找冲突记录；停用记录同样参与。"""
    model = spec.model
    condition = model.name == name
    if spec.name_scope == NAME_SCOPE_GLOBAL:
        return session.scalar(select(model).where(condition))
    if spec.name_scope == NAME_SCOPE_CATEGORY:
        parent_column = model.parent_id
        scoped = parent_column.is_(None) if parent_id is None else parent_column == parent_id
        return session.scalar(select(model).where(condition, scoped))
    if spec.name_scope == NAME_SCOPE_MATERIAL:
        category_column = model.category_id
        return session.scalar(select(model).where(condition, category_column == category_id))
    raise ValueError(f"未知名称范围：{spec.name_scope}")


def ensure_name_available(
    session: Session,
    spec: EntitySpec,
    name: str,
    *,
    parent_id: str | None = None,
    category_id: str | None = None,
) -> None:
    conflict = find_name_conflict(session, spec, name, parent_id=parent_id, category_id=category_id)
    if conflict is not None:
        raise_name_conflict(spec, conflict)


def raise_name_conflict(spec: EntitySpec, conflict: Base) -> None:
    item: dict[str, object] = {
        "id": conflict.id,
        "code": conflict.code,
        "name": conflict.name,
        "is_active": bool(conflict.is_active),
        "resource": spec.key,
    }
    if spec.name_scope == NAME_SCOPE_CATEGORY:
        item["level"] = conflict.level
        item["scope"] = "global" if conflict.parent_id is None else "parent"
    elif spec.name_scope == NAME_SCOPE_MATERIAL:
        item["scope"] = "category"
        item["category_id"] = conflict.category_id
    raise AppError(
        "NAME_CONFLICT",
        f"{spec.label}名称已存在",
        status_code=409,
        resource=spec.key,
        items=[item],
    )


def _contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
