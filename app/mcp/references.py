"""MCP 业务编码解析。

依据 docs/08-MCP服务与工具.md 7.9 第 1 条与 8.6.3：关联引用参数一律按目标实体
``code`` 精确解析为内部 UUID，不存在返回 ``RESOURCE_NOT_FOUND``，不得自动创建或
按名称模糊匹配；解析结果只在业务服务内部使用。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.modules.masterdata.registry import SPEC_BY_KEY

_LABELS = {spec.key: spec.label for spec in SPEC_BY_KEY.values()}


def find_by_code(session: Session, model: type, code: str) -> object | None:
    """按业务编码精确查询；停用记录同样返回。"""
    return session.scalar(select(model).where(model.code == code))


def id_for_code(session: Session, model: type, code: str) -> str | None:
    """筛选场景：编码不存在时返回 ``None``，由调用方给出空列表。"""
    row = find_by_code(session, model, code)
    return None if row is None else str(row.id)


def require_by_code(
    session: Session,
    model: type,
    code: str,
    *,
    resource: str,
    field: str,
    line_no: int | None = None,
) -> object:
    """按编码定位单条记录；不存在时返回不含内部 ID 的 ``RESOURCE_NOT_FOUND``。"""
    row = find_by_code(session, model, code)
    if row is not None:
        return row
    item: dict[str, object] = {"field": field, "value": code}
    if line_no is not None:
        item["line_no"] = line_no
    raise AppError(
        "RESOURCE_NOT_FOUND",
        f"{_LABELS.get(resource, resource)}编码不存在",
        status_code=404,
        resource=resource,
        items=[item],
    )
