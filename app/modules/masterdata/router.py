"""六类基础资料 HTTP 接口。

依据 docs/10-账户、基础资料与系统维护API.md 8.4：查询对所有启用用户开放，
创建/删除/停用/启用仅公司管理员；创建请求不接受 ``code``，成功返回系统生成的
``code``；错误统一使用 ``details.resource`` / ``details.items``。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.auth.permissions import CompanyAdmin, RequirePasswordChanged
from app.db.database import get_session
from app.modules.categories.models import Category
from app.modules.masterdata.schemas import (
    CategoryCreate,
    CategoryListResponse,
    CategoryOut,
    DepartmentCreate,
    ManufacturerCreate,
    MaterialCreate,
    MaterialListResponse,
    MaterialOut,
    NamedEntityListResponse,
    NamedEntityOut,
    ProjectCreate,
    WarehouseCreate,
    WarehouseListResponse,
    WarehouseOut,
)
from app.modules.masterdata.service import (
    EntityFilters,
    category_map_for_materials,
    create_entity,
    delete_entity,
    list_category_tree,
    list_entities,
    set_entity_active,
)
from app.modules.materials.models import Material
from app.request_context import get_request_id

router = APIRouter(prefix="/api/v1", tags=["master-data"])

PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def _lists(
    session: Session,
    key: str,
    *,
    code: str | None,
    name: str | None,
    is_active: bool | None,
    include_inactive: bool,
    category_code: str | None = None,
    category_id: str | None = None,
    page: int,
    page_size: int,
) -> tuple[list[object], int]:
    filters = EntityFilters(
        code=code,
        name=name,
        category_code=category_code,
        category_id=category_id,
        is_active=is_active,
        include_inactive=include_inactive,
    )
    return list_entities(session, key, filters=filters, page=page, page_size=page_size)


# --------------------------------------------------------------------------- 仓库


@router.get("/warehouses", response_model=WarehouseListResponse)
def list_warehouses(
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    page: PageNumber = 1,
    page_size: PageSize = 20,
    code: str | None = None,
    name: str | None = None,
    is_active: bool | None = None,
    include_inactive: bool = False,
) -> WarehouseListResponse:
    rows, total = _lists(
        session,
        "warehouse",
        code=code,
        name=name,
        is_active=is_active,
        include_inactive=include_inactive,
        page=page,
        page_size=page_size,
    )
    return WarehouseListResponse(
        items=[WarehouseOut.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/warehouses", response_model=WarehouseOut, status_code=201)
def create_warehouse(
    payload: WarehouseCreate,
    auth: CompanyAdmin,
    request: Request,
) -> WarehouseOut:
    row = create_entity(
        request.app.state.runtime.session_factory,
        "warehouse",
        name=payload.name,
        location=payload.location,
        actor_user_id=auth.user.id,
        request_id=get_request_id(),
    )
    return WarehouseOut.model_validate(row)


@router.delete("/warehouses/{entity_id}", status_code=204)
def delete_warehouse(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> Response:
    _delete(request, "warehouse", entity_id, auth.user.id)
    return Response(status_code=204)


@router.post("/warehouses/{entity_id}/disable", response_model=WarehouseOut)
def disable_warehouse(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> WarehouseOut:
    row = _set_active(request, "warehouse", entity_id, False, auth.user.id)
    return WarehouseOut.model_validate(row)


@router.post("/warehouses/{entity_id}/enable", response_model=WarehouseOut)
def enable_warehouse(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> WarehouseOut:
    row = _set_active(request, "warehouse", entity_id, True, auth.user.id)
    return WarehouseOut.model_validate(row)


# --------------------------------------------------------------------------- 部门


@router.get("/departments", response_model=NamedEntityListResponse)
def list_departments(
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    page: PageNumber = 1,
    page_size: PageSize = 20,
    code: str | None = None,
    name: str | None = None,
    is_active: bool | None = None,
    include_inactive: bool = False,
) -> NamedEntityListResponse:
    rows, total = _lists(
        session,
        "department",
        code=code,
        name=name,
        is_active=is_active,
        include_inactive=include_inactive,
        page=page,
        page_size=page_size,
    )
    return NamedEntityListResponse(
        items=[NamedEntityOut.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/departments", response_model=NamedEntityOut, status_code=201)
def create_department(
    payload: DepartmentCreate,
    auth: CompanyAdmin,
    request: Request,
) -> NamedEntityOut:
    row = create_entity(
        request.app.state.runtime.session_factory,
        "department",
        name=payload.name,
        actor_user_id=auth.user.id,
        request_id=get_request_id(),
    )
    return NamedEntityOut.model_validate(row)


@router.delete("/departments/{entity_id}", status_code=204)
def delete_department(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> Response:
    _delete(request, "department", entity_id, auth.user.id)
    return Response(status_code=204)


@router.post("/departments/{entity_id}/disable", response_model=NamedEntityOut)
def disable_department(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> NamedEntityOut:
    row = _set_active(request, "department", entity_id, False, auth.user.id)
    return NamedEntityOut.model_validate(row)


@router.post("/departments/{entity_id}/enable", response_model=NamedEntityOut)
def enable_department(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> NamedEntityOut:
    row = _set_active(request, "department", entity_id, True, auth.user.id)
    return NamedEntityOut.model_validate(row)


# --------------------------------------------------------------------------- 项目


@router.get("/projects", response_model=NamedEntityListResponse)
def list_projects(
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    page: PageNumber = 1,
    page_size: PageSize = 20,
    code: str | None = None,
    name: str | None = None,
    is_active: bool | None = None,
    include_inactive: bool = False,
) -> NamedEntityListResponse:
    rows, total = _lists(
        session,
        "project",
        code=code,
        name=name,
        is_active=is_active,
        include_inactive=include_inactive,
        page=page,
        page_size=page_size,
    )
    return NamedEntityListResponse(
        items=[NamedEntityOut.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/projects", response_model=NamedEntityOut, status_code=201)
def create_project(
    payload: ProjectCreate,
    auth: CompanyAdmin,
    request: Request,
) -> NamedEntityOut:
    row = create_entity(
        request.app.state.runtime.session_factory,
        "project",
        name=payload.name,
        actor_user_id=auth.user.id,
        request_id=get_request_id(),
    )
    return NamedEntityOut.model_validate(row)


@router.delete("/projects/{entity_id}", status_code=204)
def delete_project(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> Response:
    _delete(request, "project", entity_id, auth.user.id)
    return Response(status_code=204)


@router.post("/projects/{entity_id}/disable", response_model=NamedEntityOut)
def disable_project(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> NamedEntityOut:
    row = _set_active(request, "project", entity_id, False, auth.user.id)
    return NamedEntityOut.model_validate(row)


@router.post("/projects/{entity_id}/enable", response_model=NamedEntityOut)
def enable_project(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> NamedEntityOut:
    row = _set_active(request, "project", entity_id, True, auth.user.id)
    return NamedEntityOut.model_validate(row)


# --------------------------------------------------------------------------- 厂家


@router.get("/manufacturers", response_model=NamedEntityListResponse)
def list_manufacturers(
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    page: PageNumber = 1,
    page_size: PageSize = 20,
    code: str | None = None,
    name: str | None = None,
    is_active: bool | None = None,
    include_inactive: bool = False,
) -> NamedEntityListResponse:
    rows, total = _lists(
        session,
        "manufacturer",
        code=code,
        name=name,
        is_active=is_active,
        include_inactive=include_inactive,
        page=page,
        page_size=page_size,
    )
    return NamedEntityListResponse(
        items=[NamedEntityOut.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/manufacturers", response_model=NamedEntityOut, status_code=201)
def create_manufacturer(
    payload: ManufacturerCreate,
    auth: CompanyAdmin,
    request: Request,
) -> NamedEntityOut:
    row = create_entity(
        request.app.state.runtime.session_factory,
        "manufacturer",
        name=payload.name,
        actor_user_id=auth.user.id,
        request_id=get_request_id(),
    )
    return NamedEntityOut.model_validate(row)


@router.delete("/manufacturers/{entity_id}", status_code=204)
def delete_manufacturer(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> Response:
    _delete(request, "manufacturer", entity_id, auth.user.id)
    return Response(status_code=204)


@router.post("/manufacturers/{entity_id}/disable", response_model=NamedEntityOut)
def disable_manufacturer(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> NamedEntityOut:
    row = _set_active(request, "manufacturer", entity_id, False, auth.user.id)
    return NamedEntityOut.model_validate(row)


@router.post("/manufacturers/{entity_id}/enable", response_model=NamedEntityOut)
def enable_manufacturer(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> NamedEntityOut:
    row = _set_active(request, "manufacturer", entity_id, True, auth.user.id)
    return NamedEntityOut.model_validate(row)


# --------------------------------------------------------------------------- 分类


@router.get("/categories", response_model=CategoryListResponse)
def list_categories(
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    page: PageNumber = 1,
    page_size: PageSize = 20,
    code: str | None = None,
    name: str | None = None,
    is_active: bool | None = None,
    include_inactive: bool = False,
) -> CategoryListResponse:
    filters = EntityFilters(
        code=code,
        name=name,
        is_active=is_active,
        include_inactive=include_inactive,
    )
    roots, total = list_category_tree(session, filters=filters)
    start = (page - 1) * page_size
    return CategoryListResponse(
        items=[CategoryOut.model_validate(node) for node in roots[start : start + page_size]],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(
    payload: CategoryCreate,
    auth: CompanyAdmin,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> CategoryOut:
    row = create_entity(
        request.app.state.runtime.session_factory,
        "category",
        name=payload.name,
        parent_id=str(payload.parent_id) if payload.parent_id else None,
        actor_user_id=auth.user.id,
        request_id=get_request_id(),
    )
    return _category_out(session, row)


@router.delete("/categories/{entity_id}", status_code=204)
def delete_category(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> Response:
    _delete(request, "category", entity_id, auth.user.id)
    return Response(status_code=204)


@router.post("/categories/{entity_id}/disable", response_model=CategoryOut)
def disable_category(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> CategoryOut:
    row = _set_active(request, "category", entity_id, False, auth.user.id)
    return _category_out(session, row)


@router.post("/categories/{entity_id}/enable", response_model=CategoryOut)
def enable_category(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> CategoryOut:
    row = _set_active(request, "category", entity_id, True, auth.user.id)
    return _category_out(session, row)


# --------------------------------------------------------------------------- 物资


@router.get("/materials", response_model=MaterialListResponse)
def list_materials(
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    page: PageNumber = 1,
    page_size: PageSize = 20,
    code: str | None = None,
    name: str | None = None,
    category_code: str | None = None,
    category_id: UUID | None = None,
    is_active: bool | None = None,
    include_inactive: bool = False,
) -> MaterialListResponse:
    rows, total = _lists(
        session,
        "material",
        code=code,
        name=name,
        is_active=is_active,
        include_inactive=include_inactive,
        category_code=category_code,
        category_id=str(category_id) if category_id else None,
        page=page,
        page_size=page_size,
    )
    materials = [row for row in rows if isinstance(row, Material)]
    categories = category_map_for_materials(session, materials)
    items = [
        _material_out(material, categories.get(material.category_id), session)
        for material in materials
    ]
    return MaterialListResponse(items=items, page=page, page_size=page_size, total=total)


@router.post("/materials", response_model=MaterialOut, status_code=201)
def create_material(
    payload: MaterialCreate,
    auth: CompanyAdmin,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> MaterialOut:
    row = create_entity(
        request.app.state.runtime.session_factory,
        "material",
        name=payload.name,
        category_id=str(payload.category_id),
        unit=payload.unit,
        quantity_type=payload.quantity_type,
        actor_user_id=auth.user.id,
        request_id=get_request_id(),
    )
    assert isinstance(row, Material)
    return _material_out(row, session.get(Category, row.category_id), session)


@router.delete("/materials/{entity_id}", status_code=204)
def delete_material(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
) -> Response:
    _delete(request, "material", entity_id, auth.user.id)
    return Response(status_code=204)


@router.post("/materials/{entity_id}/disable", response_model=MaterialOut)
def disable_material(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> MaterialOut:
    row = _set_active(request, "material", entity_id, False, auth.user.id)
    assert isinstance(row, Material)
    return _material_out(row, session.get(Category, row.category_id), session)


@router.post("/materials/{entity_id}/enable", response_model=MaterialOut)
def enable_material(
    entity_id: UUID,
    auth: CompanyAdmin,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> MaterialOut:
    row = _set_active(request, "material", entity_id, True, auth.user.id)
    assert isinstance(row, Material)
    return _material_out(row, session.get(Category, row.category_id), session)


# --------------------------------------------------------------------------- 公共辅助


def _set_active(
    request: Request, key: str, entity_id: UUID, active: bool, actor_user_id: str
) -> object:
    return set_entity_active(
        request.app.state.runtime.session_factory,
        key,
        str(entity_id),
        active=active,
        actor_user_id=actor_user_id,
        request_id=get_request_id(),
    )


def _delete(request: Request, key: str, entity_id: UUID, actor_user_id: str) -> None:
    delete_entity(
        request.app.state.runtime.session_factory,
        key,
        str(entity_id),
        actor_user_id=actor_user_id,
        request_id=get_request_id(),
    )


def _category_out(session: Session, row: object) -> CategoryOut:
    assert isinstance(row, Category)
    parent = session.get(Category, row.parent_id) if row.parent_id else None
    return CategoryOut.model_validate(
        {
            "id": row.id,
            "code": row.code,
            "name": row.name,
            "parent_id": row.parent_id,
            "parent_code": parent.code if parent else None,
            "level": row.level,
            "is_active": row.is_active,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "children": [],
        }
    )


def _material_out(material: Material, category: Category | None, session: Session) -> MaterialOut:
    parent = (
        session.get(Category, category.parent_id)
        if category is not None and category.parent_id
        else None
    )
    return MaterialOut(
        id=material.id,
        code=material.code,
        name=material.name,
        is_active=material.is_active,
        version=material.version,
        created_at=material.created_at,
        updated_at=material.updated_at,
        category_id=material.category_id,
        category_code=category.code if category else "",
        category_name=category.name if category else "",
        parent_category_code=parent.code if parent else None,
        parent_category_name=parent.name if parent else None,
        unit=material.unit,
        quantity_type=material.quantity_type,
    )
