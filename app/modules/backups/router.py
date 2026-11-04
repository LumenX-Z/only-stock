"""备份查询与手动备份接口。

依据 docs/10-账户、基础资料与系统维护API.md 8.4、docs/13 10.6：
仅公司管理员可手动备份和查看状态；手动备份与定时备份同等计入保留集。
"""

from fastapi import APIRouter, Request

from app.api.listing import ListQuery
from app.api.responses import COMMON_ERROR_RESPONSES
from app.auth.permissions import CompanyAdmin
from app.modules.backups.schemas import BackupListResponse, BackupOut
from app.modules.backups.service import BackupInfo, create_backup, list_backups
from app.request_context import get_request_id

router = APIRouter(
    prefix="/api/v1/backups",
    tags=["backups"],
    responses=COMMON_ERROR_RESPONSES,
)


@router.get("", response_model=BackupListResponse)
def get_backups(
    _auth: CompanyAdmin,
    request: Request,
    params: ListQuery,
) -> BackupListResponse:
    items, total = list_backups(
        request.app.state.settings,
        page=params.page,
        page_size=params.page_size,
        sort=params.sort,
        order=params.order,
    )
    return BackupListResponse(
        items=[_out(item) for item in items],
        page=params.page,
        page_size=params.page_size,
        total=total,
    )


@router.post("", response_model=BackupOut, status_code=201)
def create_backup_endpoint(
    auth: CompanyAdmin,
    request: Request,
) -> BackupOut:
    info = create_backup(
        request.app.state.settings,
        request.app.state.runtime.session_factory,
        actor_user_id=auth.user.id,
        request_id=get_request_id(),
        source="API",
    )
    return _out(info)


def _out(info: BackupInfo) -> BackupOut:
    return BackupOut(
        id=info.id,
        created_at=info.created_at,
        status=info.status,
        size_bytes=info.size_bytes,
        sha256=info.sha256,
        integrity=info.integrity,
        error=info.error,
    )
