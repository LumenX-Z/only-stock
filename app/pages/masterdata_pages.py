"""六类基础资料页面：列表、创建、停用、启用与删除。

页面不提供 ``code`` 输入；编码由服务端生成并在列表中只读展示（docs/12 9.6）。
"""

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.listing import ListQuery
from app.db.database import get_session
from app.errors import AppError
from app.modules.categories.models import Category
from app.modules.masterdata.service import (
    EntityFilters,
    category_disable_blockers,
    create_entity,
    delete_entity,
    list_category_tree,
    list_entities,
    set_entity_active,
)
from app.modules.materials.models import Material
from app.pages import common
from app.pages.dependencies import PageAdminForm, PageUser
from app.pages.templating import flash, redirect, render
from app.request_context import get_request_id


@dataclass(frozen=True, slots=True)
class ResourcePage:
    path: str
    key: str
    label: str
    description: str


RESOURCES: dict[str, ResourcePage] = {
    "warehouses": ResourcePage(
        "warehouses", "warehouse", "仓库", "名称全表唯一；创建后可停用或删除。"
    ),
    "departments": ResourcePage(
        "departments", "department", "部门", "名称全表唯一；用于入库、出库单据头。"
    ),
    "projects": ResourcePage(
        "projects", "project", "项目", "名称全表唯一；明细逐行选择，允许留空。"
    ),
    "manufacturers": ResourcePage(
        "manufacturers", "manufacturer", "厂家", "名称全表唯一；仅入库明细必填。"
    ),
    "categories": ResourcePage(
        "categories", "category", "分类", "严格两级；一级名称全局唯一，二级名称在父级内唯一。"
    ),
    "materials": ResourcePage(
        "materials", "material", "物资", "名称在直接所属分类内唯一；编码由系统生成。"
    ),
}

router = APIRouter(prefix="/masterdata", include_in_schema=False)


def _resource(resource: str) -> ResourcePage:
    page = RESOURCES.get(resource)
    if page is None:
        raise AppError("NOT_FOUND", "基础资料页面不存在", status_code=404)
    return page


def _filters(request: Request) -> dict[str, Any]:
    return {
        "code": (request.query_params.get("code") or "").strip() or None,
        "name": (request.query_params.get("name") or "").strip() or None,
        "include_inactive": request.query_params.get("include_inactive") in {"1", "true", "on"},
        "category_id": (request.query_params.get("category_id") or "").strip() or None,
    }


def _category_options(categories: dict[str, Category]) -> list[dict[str, Any]]:
    options = []
    for category in sorted(categories.values(), key=lambda item: (item.level, item.code)):
        if not category.is_active:
            continue
        if category.level == 2:
            parent = categories.get(category.parent_id or "")
            if parent is None or not parent.is_active:
                continue
        options.append(
            {
                "id": category.id,
                "label": common.category_path(category, categories),
                "code": category.code,
                "level": category.level,
            }
        )
    return options


def _render_list(
    request: Request,
    auth: Any,
    session: Session,
    params: Any,
    resource: ResourcePage,
    *,
    error: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    status_code: int = 200,
) -> Response:
    filters = _filters(request)
    categories = common.category_index(session)
    blockers: dict[str, list[dict[str, Any]]] = {}
    if resource.key == "category":
        roots, total = list_category_tree(
            session,
            filters=EntityFilters(
                code=filters["code"],
                name=filters["name"],
                include_inactive=filters["include_inactive"],
            ),
        )
        start = (params.page - 1) * params.page_size
        rows = roots[start : start + params.page_size]
        for root in rows:
            blockers[str(root["id"])] = category_disable_blockers(
                session, session.get(Category, str(root["id"]))
            )
            for child in root.get("children", []):
                blockers[str(child["id"])] = category_disable_blockers(
                    session, session.get(Category, str(child["id"]))
                )
    else:
        rows, total = list_entities(
            session,
            resource.key,
            filters=EntityFilters(
                code=filters["code"],
                name=filters["name"],
                category_id=filters["category_id"],
                include_inactive=filters["include_inactive"],
            ),
            page=params.page,
            page_size=params.page_size,
            sort="code",
        )
    material_categories: dict[str, Category] = {}
    if resource.key == "material":
        material_categories = {
            str(material.id): categories[material.category_id]
            for material in rows
            if isinstance(material, Material) and material.category_id in categories
        }
    return render(
        request,
        "masterdata/list.html",
        auth=auth,
        status_code=status_code,
        resource=resource,
        rows=rows,
        total=total,
        page=params.page,
        page_size=params.page_size,
        filters=filters,
        categories=categories,
        category_options=_category_options(categories),
        all_categories=sorted(categories.values(), key=lambda item: (item.level, item.code)),
        material_categories=material_categories,
        blockers=blockers,
        form=form or {},
        error=error,
        can_manage=auth.user.role == "COMPANY_ADMIN",
    )


@router.get("/{resource}")
def masterdata_page(
    resource: str,
    request: Request,
    auth: PageUser,
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
) -> Response:
    return _render_list(request, auth, session, params, _resource(resource))


@router.post("/{resource}")
async def create_masterdata_page(
    resource: str,
    request: Request,
    auth: PageAdminForm,
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
) -> Response:
    page = _resource(resource)
    form = await request.form()
    values = {key: str(value) for key, value in form.items() if key != "_csrf"}
    try:
        row = create_entity(
            request.app.state.runtime.session_factory,
            page.key,
            name=values.get("name", ""),
            location=values.get("location") or None,
            parent_id=values.get("parent_id") or None,
            category_id=values.get("category_id") or None,
            unit=values.get("unit") or None,
            quantity_type=values.get("quantity_type") or None,
            actor_user_id=auth.user.id,
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        return _render_list(
            request,
            auth,
            session,
            params,
            page,
            error=common.error_view(exc, get_request_id() or ""),
            form=values,
            status_code=exc.status_code,
        )
    flash(auth, f"{page.label} {row.name} 已创建", detail=f"系统编码：{row.code}（只读）")
    return redirect(f"/masterdata/{page.path}")


@router.post("/{resource}/{entity_id}/disable")
async def disable_masterdata_page(
    resource: str,
    entity_id: UUID,
    request: Request,
    auth: PageAdminForm,
) -> Response:
    page = _resource(resource)
    try:
        set_entity_active(
            request.app.state.runtime.session_factory,
            page.key,
            str(entity_id),
            active=False,
            actor_user_id=auth.user.id,
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        flash(auth, f"{page.label}停用失败：{exc.message}", level="danger", detail=_detail(exc))
        return redirect(f"/masterdata/{page.path}")
    flash(auth, f"{page.label}已停用", detail="可随时重新启用，不影响历史单据与库存。")
    return redirect(f"/masterdata/{page.path}")


@router.post("/{resource}/{entity_id}/enable")
async def enable_masterdata_page(
    resource: str,
    entity_id: UUID,
    request: Request,
    auth: PageAdminForm,
) -> Response:
    page = _resource(resource)
    try:
        set_entity_active(
            request.app.state.runtime.session_factory,
            page.key,
            str(entity_id),
            active=True,
            actor_user_id=auth.user.id,
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        link_url = None
        link_label = None
        if exc.code == "PARENT_INACTIVE":
            parent = next((item for item in exc.items if item.get("id")), None)
            if parent is not None:
                link_url = f"/masterdata/categories?include_inactive=1#category-{parent['id']}"
                link_label = f"前往启用上级分类 {parent.get('code', '')}"
        flash(
            auth,
            f"{page.label}启用失败：{exc.message}",
            level="danger",
            detail=_detail(exc),
            link_url=link_url,
            link_label=link_label,
        )
        return redirect(f"/masterdata/{page.path}")
    flash(auth, f"{page.label}已启用")
    return redirect(f"/masterdata/{page.path}")


@router.post("/{resource}/{entity_id}/delete")
async def delete_masterdata_page(
    resource: str,
    entity_id: UUID,
    request: Request,
    auth: PageAdminForm,
) -> Response:
    page = _resource(resource)
    try:
        delete_entity(
            request.app.state.runtime.session_factory,
            page.key,
            str(entity_id),
            actor_user_id=auth.user.id,
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        flash(auth, f"{page.label}删除失败：{exc.message}", level="danger", detail=_detail(exc))
        return redirect(f"/masterdata/{page.path}")
    flash(auth, f"{page.label}已删除")
    return redirect(f"/masterdata/{page.path}")


def _detail(exc: AppError) -> str:
    parts = []
    for item in exc.items:
        label = item.get("code") or item.get("name")
        if label:
            parts.append(str(label))
    return "、".join(parts) if parts else "请查看列表中的阻碍项。"
