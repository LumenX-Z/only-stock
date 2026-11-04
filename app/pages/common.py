"""页面数据装载辅助：下拉选项、分类路径、错误视图与统计。

页面只读查询集中在这里，写操作仍通过各业务服务完成，避免在模板层复制规则。
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.modules.categories.models import Category
from app.modules.documents.models import DocStatus, StockDocument
from app.modules.masterdata.registry import get_spec
from app.modules.materials.models import Material
from app.modules.users.models import User


def select_options(session: Session, key: str, *, include_inactive: bool = False) -> list[Any]:
    spec = get_spec(key)
    statement = select(spec.model).order_by(spec.model.code)
    if not include_inactive:
        statement = statement.where(spec.model.is_active.is_(True))
    return list(session.scalars(statement).all())


def category_index(session: Session) -> dict[str, Category]:
    return {row.id: row for row in session.scalars(select(Category)).all()}


def category_path(category: Category | None, categories: dict[str, Category]) -> str:
    if category is None:
        return "—"
    if category.parent_id and category.parent_id in categories:
        parent = categories[category.parent_id]
        return f"{parent.name} / {category.name}"
    return category.name


def material_options(session: Session, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    categories = category_index(session)
    statement = select(Material).order_by(Material.code)
    if not include_inactive:
        statement = statement.where(Material.is_active.is_(True))
    materials = list(session.scalars(statement).all())
    options: list[dict[str, Any]] = []
    for material in materials:
        category = categories.get(material.category_id)
        path = category_path(category, categories)
        options.append(
            {
                "id": material.id,
                "code": material.code,
                "name": material.name,
                "unit": material.unit,
                "quantity_type": material.quantity_type,
                "category": path,
                "is_active": bool(material.is_active),
                "label": f"{path} · {material.name}（{material.code}，{material.unit}）",
            }
        )
    return options


def user_options(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.login_name)).all())


def dashboard_stats(session: Session) -> dict[str, int]:
    warehouse_model = get_spec("warehouse").model
    active_materials = session.scalar(
        select(func.count()).select_from(Material).where(Material.is_active.is_(True))
    )
    active_warehouses = session.scalar(
        select(func.count()).select_from(warehouse_model).where(warehouse_model.is_active.is_(True))
    )
    drafts = session.scalar(
        select(func.count())
        .select_from(StockDocument)
        .where(StockDocument.status == DocStatus.DRAFT.value)
    )
    posted = session.scalar(
        select(func.count())
        .select_from(StockDocument)
        .where(StockDocument.status == DocStatus.POSTED.value)
    )
    return {
        "materials": int(active_materials or 0),
        "warehouses": int(active_warehouses or 0),
        "drafts": int(drafts or 0),
        "posted": int(posted or 0),
    }


def error_view(exc: AppError, request_id: str) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": exc.message,
        "request_id": request_id,
        "details": {"resource": exc.resource, "items": list(exc.items)},
    }


def validation_error_view(errors: Iterable[dict[str, Any]], request_id: str) -> dict[str, Any]:
    items = []
    for error in errors:
        location = error.get("loc") or ("request",)
        items.append(
            {
                "field": ".".join(str(part) for part in location),
                "reason": str(error.get("msg", "invalid value")),
            }
        )
    return {
        "code": "VALIDATION_ERROR",
        "message": "请求参数校验失败",
        "request_id": request_id,
        "details": {"resource": None, "items": items},
    }
