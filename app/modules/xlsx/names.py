"""快速录入的批量名称解析。

依据 docs/07-XLSX模板、快速录入与导出.md 7.8.2 与 docs/05-数据模型与实体约束.md 6.8：
- 名称仅在去除首尾空白后精确匹配，不折叠大小写、不删除内部空白；
- 仓库与管理部门在表头区各解析一次；
- 项目、厂家、分类、物资按文件批量查询，禁止逐行发起可避免的数据库查询；
- 不自动创建或修改任何基础资料。
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.categories.models import CATEGORY_LEVEL_ONE, CATEGORY_LEVEL_TWO, Category
from app.modules.manufacturers.models import Manufacturer
from app.modules.materials.models import Material
from app.modules.projects.models import Project


def find_by_name(session: Session, model: type, name: str) -> object | None:
    """按名称唯一索引查询一条记录；名称已由调用方去除首尾空白。"""
    return session.scalars(select(model).where(model.name == name).limit(2)).first()


def load_projects(session: Session, names: Iterable[str]) -> dict[str, Project]:
    return {row.name: row for row in _load_by_names(session, Project, names)}


def load_manufacturers(session: Session, names: Iterable[str]) -> dict[str, Manufacturer]:
    return {row.name: row for row in _load_by_names(session, Manufacturer, names)}


def load_level1_categories(session: Session, names: Iterable[str]) -> dict[str, Category]:
    clean = _clean_names(names)
    if not clean:
        return {}
    rows = session.scalars(
        select(Category).where(
            Category.level == CATEGORY_LEVEL_ONE,
            Category.parent_id.is_(None),
            Category.name.in_(clean),
        )
    ).all()
    return {row.name: row for row in rows}


def load_level2_categories(
    session: Session, pairs: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], Category]:
    clean = {(parent_id, name) for parent_id, name in pairs if parent_id and name}
    if not clean:
        return {}
    parent_ids = sorted({parent_id for parent_id, _ in clean})
    names = sorted({name for _, name in clean})
    rows = session.scalars(
        select(Category).where(
            Category.level == CATEGORY_LEVEL_TWO,
            Category.name.in_(names),
            Category.parent_id.in_(parent_ids),
        )
    ).all()
    return {(str(row.parent_id), row.name): row for row in rows if row.parent_id is not None}


def load_materials(
    session: Session,
    names: Iterable[str],
    category_ids: Iterable[str],
) -> dict[tuple[str, str], list[Material]]:
    clean_names = _clean_names(names)
    clean_categories = _clean_ids(category_ids)
    if not clean_names or not clean_categories:
        return {}
    rows = session.scalars(
        select(Material).where(
            Material.name.in_(clean_names),
            Material.category_id.in_(clean_categories),
        )
    ).all()
    materials: dict[tuple[str, str], list[Material]] = {}
    for row in rows:
        materials.setdefault((row.category_id, row.name), []).append(row)
    return materials


def _load_by_names(session: Session, model: type, names: Iterable[str]) -> list:
    clean = _clean_names(names)
    if not clean:
        return []
    return list(session.scalars(select(model).where(model.name.in_(clean))).all())


def _clean_names(names: Iterable[str]) -> list[str]:
    return sorted({name for name in names if name})


def _clean_ids(ids: Iterable[str]) -> list[str]:
    return sorted({value for value in ids if value})
