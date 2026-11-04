"""HTMX 局部片段：出库行可用量提示。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db.database import get_session
from app.modules.inventory.service import list_inventory
from app.pages.dependencies import PageUser
from app.pages.templating import render_partial

router = APIRouter(prefix="/partials", include_in_schema=False)


@router.get("/inventory-balance")
def inventory_balance(
    request: Request,
    _auth: PageUser,
    session: Annotated[Session, Depends(get_session)],
    warehouse_id: str = "",
    line_material_id: str = "",
    material_id: str = "",
) -> Response:
    material = material_id or line_material_id
    if not warehouse_id or not material:
        return render_partial(request, "partials/inventory_balance.html", item=None)
    rows, _ = list_inventory(
        session,
        warehouse_id=warehouse_id,
        material_id=material,
        include_zero=True,
        page=1,
        page_size=1,
    )
    return render_partial(
        request,
        "partials/inventory_balance.html",
        item=rows[0] if rows else None,
    )
