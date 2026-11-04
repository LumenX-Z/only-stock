"""我的 API Key 页面：创建、一次性展示与吊销。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.listing import ListQuery
from app.auth.api_keys import create_api_key, list_api_keys, revoke_api_key
from app.db.database import get_session
from app.errors import AppError
from app.pages.dependencies import PageForm, PageUser
from app.pages.templating import flash, redirect, remember_secret, render, take_secret
from app.request_context import get_request_id

router = APIRouter(prefix="/api-keys", include_in_schema=False)


@router.get("")
def api_keys_page(
    request: Request,
    auth: PageUser,
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
) -> Response:
    include_revoked = request.query_params.get("include_revoked") in {"1", "true", "on"}
    rows, total = list_api_keys(
        session,
        auth.user.id,
        include_revoked=include_revoked,
        page=params.page,
        page_size=params.page_size,
    )
    token = request.query_params.get("created")
    created_key = take_secret(auth, token)
    return render(
        request,
        "api_keys/list.html",
        auth=auth,
        keys=rows,
        total=total,
        page=params.page,
        page_size=params.page_size,
        include_revoked=include_revoked,
        created_key=created_key,
        created_token_missing=bool(token and created_key is None),
        mcp_url="http://服务器IP:8000/mcp",
        max_keys=10,
    )


@router.post("")
async def create_api_key_page(request: Request, auth: PageForm) -> Response:
    form = await request.form()
    try:
        _, raw_key = create_api_key(
            request.app.state.runtime.session_factory,
            auth.user.id,
            str(form.get("name", "")),
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        flash(auth, f"创建 API Key 失败：{exc.message}", level="danger")
        return redirect("/api-keys")
    token = remember_secret(auth, raw_key)
    return redirect(f"/api-keys?created={token}")


@router.post("/{key_id}/revoke")
async def revoke_api_key_page(key_id: UUID, request: Request, auth: PageForm) -> Response:
    try:
        revoke_api_key(
            request.app.state.runtime.session_factory,
            auth.user.id,
            str(key_id),
            request_id=get_request_id(),
            source="WEB",
        )
    except AppError as exc:
        flash(auth, f"吊销失败：{exc.message}", level="danger")
        return redirect("/api-keys")
    flash(auth, "API Key 已吊销", detail="已吊销的 Key 不会恢复，请更新 MCP 客户端配置。")
    return redirect("/api-keys")
