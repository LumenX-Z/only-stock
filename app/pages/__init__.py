"""服务端渲染页面路由聚合。"""

from fastapi import APIRouter

from app.pages.api_key_pages import router as api_key_router
from app.pages.audit_backup_pages import router as audit_backup_router
from app.pages.auth_pages import router as auth_page_router
from app.pages.document_pages import router as document_page_router
from app.pages.inventory_pages import router as inventory_page_router
from app.pages.masterdata_pages import router as masterdata_page_router
from app.pages.partials import router as partials_router
from app.pages.users_pages import router as users_page_router

pages_router = APIRouter(include_in_schema=False)
for _router in (
    auth_page_router,
    users_page_router,
    masterdata_page_router,
    document_page_router,
    inventory_page_router,
    api_key_router,
    audit_backup_router,
    partials_router,
):
    pages_router.include_router(_router)
