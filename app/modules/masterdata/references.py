"""基础资料引用检查与仓库库存检查。

依据 docs/05-数据模型与实体约束.md 6.7：六类基础资料“已引用”包含被草稿、已生效、
已软删除单据或其他主数据引用；本模块在 P3 先通过表存在性探测兼容后续 P4/P5 的
单据表与库存视图，避免在 P3 误判可删除。
"""

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.modules.masterdata.registry import EntitySpec

_STOCK_DOCUMENTS = "stock_documents"
_STOCK_DOCUMENT_LINES = "stock_document_lines"
_INVENTORY_VIEW = "v_inventory_balance"


def has_table(session: Session, name: str) -> bool:
    return inspect(session.connection()).has_table(name)


def count_document_references(session: Session, spec: EntitySpec, entity_id: str) -> int:
    """统计单据头/明细对该实体的引用数量；表尚未创建时返回 0。"""
    if spec.key == "warehouse" and has_table(session, _STOCK_DOCUMENTS):
        return _scalar(
            session,
            f"SELECT COUNT(*) FROM {_STOCK_DOCUMENTS} WHERE warehouse_id = :entity_id",
            entity_id,
        )
    if spec.key == "department" and has_table(session, _STOCK_DOCUMENTS):
        return _scalar(
            session,
            f"SELECT COUNT(*) FROM {_STOCK_DOCUMENTS} WHERE department_id = :entity_id",
            entity_id,
        )
    if spec.key == "project" and has_table(session, _STOCK_DOCUMENT_LINES):
        return _scalar(
            session,
            f"SELECT COUNT(*) FROM {_STOCK_DOCUMENT_LINES} WHERE project_id = :entity_id",
            entity_id,
        )
    if spec.key == "manufacturer" and has_table(session, _STOCK_DOCUMENT_LINES):
        return _scalar(
            session,
            f"SELECT COUNT(*) FROM {_STOCK_DOCUMENT_LINES} WHERE manufacturer_id = :entity_id",
            entity_id,
        )
    if spec.key == "material" and has_table(session, _STOCK_DOCUMENT_LINES):
        return _scalar(
            session,
            f"SELECT COUNT(*) FROM {_STOCK_DOCUMENT_LINES} WHERE material_id = :entity_id",
            entity_id,
        )
    return 0


def warehouse_has_nonzero_stock(session: Session, warehouse_id: str) -> bool:
    """有库存视图时检查该仓库是否存在非零余额；P3 尚无视图时返回 False。"""
    if not has_table(session, _INVENTORY_VIEW):
        return False
    count = _scalar(
        session,
        f"SELECT COUNT(*) FROM {_INVENTORY_VIEW} "
        "WHERE warehouse_id = :entity_id AND balance_milli <> 0",
        warehouse_id,
    )
    return count > 0


def _scalar(session: Session, statement: str, entity_id: str) -> int:
    value = session.execute(text(statement), {"entity_id": entity_id}).scalar()
    return int(value or 0)


def entity_snapshot(entity: Base) -> dict[str, object]:
    """审计用安全快照：只包含业务标识与状态，不含凭证或敏感字段。"""
    snapshot: dict[str, object] = {
        "id": getattr(entity, "id", None),
        "code": getattr(entity, "code", None),
        "name": getattr(entity, "name", None),
        "is_active": getattr(entity, "is_active", None),
        "version": getattr(entity, "version", None),
    }
    for attribute in ("location", "parent_id", "level", "category_id", "unit", "quantity_type"):
        if hasattr(entity, attribute):
            snapshot[attribute] = getattr(entity, attribute)
    return snapshot
