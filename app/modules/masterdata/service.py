"""六类基础资料业务服务。

依据 docs/05-数据模型与实体约束.md 6.2、6.4、6.7、6.8 与
docs/06-库存计算与单据业务流程.md 7.6、7.6.1：
- 创建、删除、停用、启用全部在 ``BEGIN IMMEDIATE`` 写事务内完成并写审计；
- 编码由服务端生成，名称按层级范围查重，删除/停用前做引用与下级检查；
- 启用下级资料要求上级已启用，停用不级联、不改历史。
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.errors import AppError
from app.modules.audit.service import record_audit
from app.modules.categories.models import CATEGORY_LEVEL_ONE, CATEGORY_LEVEL_TWO, Category
from app.modules.masterdata.base import UNIT_MAX_LENGTH
from app.modules.masterdata.codes import next_entity_code
from app.modules.masterdata.names import (
    ensure_name_available,
    find_name_conflict,
    normalize_name,
    raise_name_conflict,
)
from app.modules.masterdata.references import (
    count_document_references,
    entity_snapshot,
    warehouse_has_nonzero_stock,
)
from app.modules.masterdata.registry import EntitySpec, get_spec
from app.modules.materials.models import Material, QuantityType

CATEGORY_ACTIVE_CHILDREN = "CATEGORY_HAS_ACTIVE_CHILDREN"
CATEGORY_ACTIVE_MATERIALS = "CATEGORY_HAS_ACTIVE_MATERIALS"


@dataclass(frozen=True, slots=True)
class EntityFilters:
    code: str | None = None
    name: str | None = None
    parent_code: str | None = None
    category_code: str | None = None
    category_id: str | None = None
    is_active: bool | None = None
    include_inactive: bool = False


def create_entity(
    session_factory: sessionmaker[Session],
    key: str,
    *,
    name: str,
    actor_user_id: str | None,
    request_id: str | None,
    source: str = "API",
    api_key_id: str | None = None,
    location: str | None = None,
    parent_id: str | None = None,
    category_id: str | None = None,
    unit: str | None = None,
    quantity_type: str | None = None,
) -> object:
    """创建一类基础资料；编码、记录与审计在同一事务内提交。"""
    spec = get_spec(key)
    normalized = normalize_name(name)
    created_at = utc_now()
    extra: dict[str, object] = {}
    scoped_parent_id: str | None = None
    scoped_category_id: str | None = None

    try:
        with session_factory() as session, begin_immediate(session):
            if spec.key == "category":
                extra, scoped_parent_id = _category_create_scope(session, parent_id)
            elif spec.key == "material":
                extra, scoped_category_id = _material_create_scope(
                    session, category_id, unit, quantity_type
                )
            elif spec.key == "warehouse":
                extra["location"] = _normalize_location(location)

            ensure_name_available(
                session,
                spec,
                normalized,
                parent_id=scoped_parent_id,
                category_id=scoped_category_id,
            )
            code = next_entity_code(session, spec)
            row = spec.model(
                code=code,
                name=normalized,
                is_active=True,
                version=1,
                created_at=created_at,
                updated_at=created_at,
                **extra,
            )
            session.add(row)
            session.flush()
            record_audit(
                session,
                actor_user_id=actor_user_id,
                action=f"{key.upper()}_CREATED",
                entity_type=key,
                entity_id=row.id,
                after=entity_snapshot(row),
                request_id=request_id,
                source=source,
                api_key_id=api_key_id,
                now=created_at,
            )
            session.expunge(row)
            return row
    except IntegrityError as exc:
        _translate_integrity_error(
            exc,
            session_factory,
            spec,
            normalized,
            parent_id=scoped_parent_id,
            category_id=scoped_category_id,
        )
        raise  # pragma: no cover - 上述函数必定抛出 AppError


def set_entity_active(
    session_factory: sessionmaker[Session],
    key: str,
    entity_id: str,
    *,
    active: bool,
    actor_user_id: str | None,
    request_id: str | None,
    source: str = "API",
) -> object:
    """停用或启用；已处于目标状态时幂等成功且不重复写审计。"""
    spec = get_spec(key)
    changed_at = utc_now()
    with session_factory() as session, begin_immediate(session):
        row = _require_entity(session, spec, entity_id)
        if bool(row.is_active) == active:
            session.expunge(row)
            return row

        before = entity_snapshot(row)
        if active:
            _ensure_parents_active(session, spec, row)
        else:
            _ensure_can_disable(session, spec, row)

        row.is_active = active
        _touch(row, changed_at)
        session.flush()
        record_audit(
            session,
            actor_user_id=actor_user_id,
            action=f"{key.upper()}_{'ENABLED' if active else 'DISABLED'}",
            entity_type=key,
            entity_id=row.id,
            before=before,
            after=entity_snapshot(row),
            request_id=request_id,
            source=source,
            now=changed_at,
        )
        session.expunge(row)
        return row


def delete_entity(
    session_factory: sessionmaker[Session],
    key: str,
    entity_id: str,
    *,
    actor_user_id: str | None,
    request_id: str | None,
    source: str = "API",
) -> None:
    """按引用规则物理删除；已引用或存在下级资料时返回 409。"""
    spec = get_spec(key)
    deleted_at = utc_now()
    with session_factory() as session, begin_immediate(session):
        row = _require_entity(session, spec, entity_id)
        before = entity_snapshot(row)

        if spec.key == "category":
            _ensure_category_deletable(session, row)
        else:
            reference_count = count_document_references(session, spec, row.id)
            if reference_count > 0:
                raise _in_use_error(spec, reference_count)
            if spec.key == "warehouse" and warehouse_has_nonzero_stock(session, row.id):
                raise AppError(
                    "WAREHOUSE_HAS_STOCK",
                    "仓库当前库存非零，只能停用",
                    status_code=409,
                    resource=spec.key,
                    items=[{"code": row.code, "name": row.name}],
                )

        record_audit(
            session,
            actor_user_id=actor_user_id,
            action=f"{key.upper()}_DELETED",
            entity_type=key,
            entity_id=row.id,
            before=before,
            after=None,
            request_id=request_id,
            source=source,
            now=deleted_at,
        )
        session.delete(row)
        session.flush()


def list_entities(
    session: Session,
    key: str,
    *,
    filters: EntityFilters | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[object], int]:
    """分页查询基础资料；默认只显示启用记录。"""
    spec = get_spec(key)
    filters = filters or EntityFilters()
    conditions = _filter_conditions(spec, filters)

    total = session.scalar(select(func.count()).select_from(spec.model).where(*conditions))
    rows = list(
        session.scalars(
            select(spec.model)
            .where(*conditions)
            .order_by(spec.model.code)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return rows, int(total or 0)


def list_category_tree(
    session: Session,
    *,
    filters: EntityFilters | None = None,
) -> tuple[list[dict[str, object]], int]:
    """返回一级分类为根的完整树，并保持启用/停用过滤。"""
    filters = filters or EntityFilters()
    conditions = _filter_conditions(get_spec("category"), filters)
    rows = list(
        session.scalars(
            select(Category).where(*conditions).order_by(Category.level, Category.code)
        ).all()
    )
    selected = {row.id: row for row in rows}
    if filters.code or filters.name:
        # 过滤命中子分类时补齐其父级，保证返回结构仍是完整两层树。
        for row in list(rows):
            if row.parent_id and row.parent_id not in selected:
                parent = session.get(Category, row.parent_id)
                if parent is not None:
                    selected[parent.id] = parent

    roots: list[dict[str, object]] = []
    nodes: dict[str, dict[str, object]] = {}
    children: dict[str, list[dict[str, object]]] = {}
    for row in sorted(selected.values(), key=lambda item: (item.level, item.code)):
        node = _category_dict(row)
        node["parent_code"] = selected[row.parent_id].code if row.parent_id in selected else None
        nodes[row.id] = node
        if row.parent_id is None:
            roots.append(node)
        else:
            children.setdefault(row.parent_id, []).append(node)
    for parent_id, child_nodes in children.items():
        parent = nodes.get(parent_id)
        if parent is not None:
            parent["children"] = child_nodes
            for child in child_nodes:
                child["parent_code"] = parent["code"]
    return roots, len(roots)


def get_entity(session: Session, key: str, entity_id: str) -> object:
    spec = get_spec(key)
    return _require_entity(session, spec, entity_id)


def category_map_for_materials(session: Session, materials: list[Material]) -> dict[str, Category]:
    """批量取物资所属分类，避免逐行查询。"""
    category_ids = {material.category_id for material in materials}
    if not category_ids:
        return {}
    rows = session.scalars(select(Category).where(Category.id.in_(category_ids))).all()
    return {row.id: row for row in rows}


def _filter_conditions(spec: EntitySpec, filters: EntityFilters) -> list[object]:
    model = spec.model
    conditions: list[object] = []
    if filters.code:
        conditions.append(model.code == filters.code.strip())
    if filters.name:
        conditions.append(model.name == filters.name.strip())
    if filters.is_active is not None:
        conditions.append(model.is_active.is_(filters.is_active))
    elif not filters.include_inactive:
        conditions.append(model.is_active.is_(True))

    if spec.key == "material":
        if filters.category_id:
            conditions.append(model.category_id == filters.category_id)
        if filters.category_code:
            category_ids = select(Category.id).where(Category.code == filters.category_code.strip())
            conditions.append(model.category_id.in_(category_ids))
    if spec.key == "category" and filters.parent_code:
        parent_ids = select(Category.id).where(Category.code == filters.parent_code.strip())
        conditions.append(Category.parent_id.in_(parent_ids))
    return conditions


def _category_create_scope(
    session: Session, parent_id: str | None
) -> tuple[dict[str, object], str | None]:
    if parent_id is None:
        return {"parent_id": None, "level": CATEGORY_LEVEL_ONE}, None
    parent = session.get(Category, parent_id)
    if parent is None:
        raise AppError(
            "CATEGORY_NOT_FOUND",
            "父分类不存在",
            status_code=404,
            resource="category",
            items=[{"id": parent_id}],
        )
    if parent.level != CATEGORY_LEVEL_ONE:
        raise AppError(
            "CATEGORY_LEVEL_INVALID",
            "分类只允许两级，不能在二级分类下继续创建",
            status_code=409,
            resource="category",
            items=[{"code": parent.code, "name": parent.name, "level": parent.level}],
        )
    if not parent.is_active:
        raise _parent_inactive_error("category", parent)
    return {"parent_id": parent.id, "level": CATEGORY_LEVEL_TWO}, parent.id


def _material_create_scope(
    session: Session,
    category_id: str | None,
    unit: str | None,
    quantity_type: str | None,
) -> tuple[dict[str, object], str]:
    if not category_id:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            items=[{"field": "category_id", "reason": "物资必须归属一个分类"}],
        )
    category = session.get(Category, category_id)
    if category is None:
        raise AppError(
            "CATEGORY_NOT_FOUND",
            "所属分类不存在",
            status_code=404,
            resource="category",
            items=[{"id": category_id}],
        )
    if not category.is_active:
        raise _parent_inactive_error("category", category)
    if category.level == CATEGORY_LEVEL_TWO:
        parent = session.get(Category, category.parent_id) if category.parent_id else None
        if parent is None or not parent.is_active:
            raise _parent_inactive_error("category", parent, fallback=category)

    normalized_unit = _normalize_unit(unit)
    normalized_type = _normalize_quantity_type(quantity_type)
    return (
        {
            "category_id": category.id,
            "unit": normalized_unit,
            "quantity_type": normalized_type,
        },
        category.id,
    )


def _ensure_parents_active(session: Session, spec: EntitySpec, row: object) -> None:
    if spec.key == "category" and row.level == CATEGORY_LEVEL_TWO:
        parent_id = row.parent_id
        parent = session.get(Category, parent_id) if parent_id else None
        if parent is None or not parent.is_active:
            raise _parent_inactive_error("category", parent, fallback=row)
    elif spec.key == "material":
        category = session.get(Category, row.category_id)
        if category is None or not category.is_active:
            raise _parent_inactive_error("category", category, fallback=row)
        if category.level == CATEGORY_LEVEL_TWO:
            parent = session.get(Category, category.parent_id) if category.parent_id else None
            if parent is None or not parent.is_active:
                raise _parent_inactive_error("category", parent, fallback=category)


def _ensure_can_disable(session: Session, spec: EntitySpec, row: object) -> None:
    if spec.key == "warehouse" and warehouse_has_nonzero_stock(session, row.id):
        raise AppError(
            "WAREHOUSE_HAS_STOCK",
            "仓库当前库存非零，不能停用",
            status_code=409,
            resource=spec.key,
            items=[{"code": row.code, "name": row.name}],
        )
    if spec.key != "category":
        return

    active_children = _active_children(session, row)
    active_materials = _active_materials(session, row)
    if not active_children and not active_materials:
        return

    items: list[dict[str, object]] = [
        _blocker_item("category", child) for child in active_children
    ] + [_blocker_item("material", material) for material in active_materials]
    code = CATEGORY_ACTIVE_CHILDREN if active_children else CATEGORY_ACTIVE_MATERIALS
    message = "存在未停用的子分类，不能停用" if active_children else "存在未停用的物资，不能停用"
    raise AppError(code, message, status_code=409, resource="category", items=items)


def _ensure_category_deletable(session: Session, category: Category) -> None:
    children = list(
        session.scalars(select(Category).where(Category.parent_id == category.id)).all()
    )
    if children:
        raise AppError(
            "CATEGORY_HAS_CHILDREN",
            "分类存在子分类，不能删除",
            status_code=409,
            resource="category",
            items=[_blocker_item("category", child) for child in children],
        )

    materials = _materials_in_scope(session, category)
    if materials:
        raise AppError(
            "CATEGORY_HAS_MATERIALS",
            "分类挂有物资，不能删除",
            status_code=409,
            resource="category",
            items=[_blocker_item("material", material) for material in materials],
        )

    reference_count = count_document_references(session, get_spec("category"), category.id)
    if reference_count > 0:
        raise AppError(
            "CATEGORY_IN_USE",
            "分类已被其他数据引用，只能停用",
            status_code=409,
            resource="category",
            items=[{"code": category.code, "name": category.name, "count": reference_count}],
        )


def _active_children(session: Session, category: Category) -> list[Category]:
    if category.level != CATEGORY_LEVEL_ONE:
        return []
    return list(
        session.scalars(
            select(Category).where(
                Category.parent_id == category.id,
                Category.is_active.is_(True),
            )
        ).all()
    )


def _active_materials(session: Session, category: Category) -> list[Material]:
    return [
        material for material in _materials_in_scope(session, category) if bool(material.is_active)
    ]


def _materials_in_scope(session: Session, category: Category) -> list[Material]:
    if category.level == CATEGORY_LEVEL_ONE:
        child_ids = select(Category.id).where(Category.parent_id == category.id)
        category_ids = [category.id, *session.scalars(child_ids).all()]
    else:
        category_ids = [category.id]
    return list(
        session.scalars(select(Material).where(Material.category_id.in_(category_ids))).all()
    )


def _blocker_item(kind: str, row: object) -> dict[str, object]:
    item: dict[str, object] = {
        "kind": kind,
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "count": 1,
    }
    if hasattr(row, "level"):
        item["level"] = row.level
    return item


def _in_use_error(spec: EntitySpec, reference_count: int) -> AppError:
    return AppError(
        f"{spec.key.upper()}_IN_USE",
        f"{spec.label}已被单据或历史数据引用，只能停用",
        status_code=409,
        resource=spec.key,
        items=[{"count": reference_count}],
    )


def _parent_inactive_error(
    resource: str, parent: Category | None, *, fallback: object | None = None
) -> AppError:
    item: dict[str, object] = {
        "kind": resource,
        "reason": "parent_inactive",
        "is_active": bool(getattr(parent, "is_active", False)),
    }
    if parent is not None:
        item.update(
            {
                "id": parent.id,
                "code": parent.code,
                "name": parent.name,
                "level": parent.level,
            }
        )
    elif fallback is not None:
        item.update(
            {
                "id": getattr(fallback, "id", None),
                "name": getattr(fallback, "name", None),
            }
        )
    return AppError(
        "PARENT_INACTIVE",
        "上级分类未启用，不能启用或创建下级资料",
        status_code=409,
        resource=resource,
        items=[item],
    )


def _require_entity(session: Session, spec: EntitySpec, entity_id: str) -> object:
    row = session.get(spec.model, entity_id)
    if row is None:
        raise AppError(
            f"{spec.key.upper()}_NOT_FOUND",
            f"{spec.label}不存在",
            status_code=404,
            resource=spec.key,
            items=[{"id": entity_id}],
        )
    return row


def _touch(row: object, changed_at: datetime) -> None:
    row.version = int(row.version) + 1
    row.updated_at = changed_at


def _normalize_location(location: str | None) -> str | None:
    if location is None:
        return None
    normalized = location.strip()
    return normalized or None


def _normalize_unit(unit: str | None) -> str:
    normalized = (unit or "").strip()
    if not normalized:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            items=[{"field": "unit", "reason": "计量单位不能为空"}],
        )
    if len(normalized) > UNIT_MAX_LENGTH:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            items=[{"field": "unit", "reason": f"计量单位不能超过 {UNIT_MAX_LENGTH} 位"}],
        )
    return normalized


def _normalize_quantity_type(quantity_type: str | None) -> str:
    value = (quantity_type or "").strip()
    if value not in {kind.value for kind in QuantityType}:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            items=[{"field": "quantity_type", "reason": "数量类型必须是 INTEGER 或 DECIMAL"}],
        )
    return value


def _category_dict(row: Category) -> dict[str, object]:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "parent_id": row.parent_id,
        "parent_code": None,
        "level": row.level,
        "is_active": bool(row.is_active),
        "version": row.version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "children": [],
    }


def _translate_integrity_error(
    exc: IntegrityError,
    session_factory: sessionmaker[Session],
    spec: EntitySpec,
    name: str,
    *,
    parent_id: str | None,
    category_id: str | None,
) -> None:
    """把数据库唯一约束冲突转换为文档定义的明确错误。"""
    message = str(exc.orig)
    if "UNIQUE constraint failed" not in message:
        raise exc
    if ".code" in message:
        raise AppError(
            "CODE_CONFLICT",
            f"{spec.label}编码冲突，请重试",
            status_code=409,
            resource=spec.key,
            items=[{"name": name}],
        ) from exc
    with session_factory() as lookup:
        conflict = find_name_conflict(
            lookup, spec, name, parent_id=parent_id, category_id=category_id
        )
    if conflict is not None:
        raise_name_conflict(spec, conflict)
    raise AppError(
        "NAME_CONFLICT",
        f"{spec.label}名称已存在",
        status_code=409,
        resource=spec.key,
        items=[{"name": name}],
    ) from exc
