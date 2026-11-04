"""单据与库存 HTTP 接口。

依据 docs/11-单据与库存业务API.md 8.5 与 docs/09-HTTP-API通用约定与健康检查.md 8.1：
- 查询接口对所有启用用户开放，创建草稿对两种角色开放；
- 修改/删除草稿按“管理员全部、操作员仅本人”在服务端校验；
- 提交允许任意启用用户提交普通草稿；
- 单据路径与响应中的 ID 使用 UUID 字符串，数量为十进制字符串。
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.auth.permissions import RequirePasswordChanged
from app.db.database import get_session
from app.modules.documents.inputs import DocumentInput, DocumentLineInput
from app.modules.documents.models import DocStatus, DocType
from app.modules.documents.presenter import detail_payload, summary_payload
from app.modules.documents.schemas import (
    DocumentCreate,
    DocumentDetailOut,
    DocumentLineIn,
    DocumentListResponse,
    DocumentSummaryOut,
    DocumentUpdate,
    DocumentVersionRequest,
)
from app.modules.documents.service import (
    DocumentFilters,
    DocumentPatch,
    create_draft,
    delete_draft,
    get_document,
    list_documents,
    soft_delete_document,
    submit_document,
    update_draft,
)
from app.modules.inventory.service import local_day_bounds
from app.request_context import get_request_id

router = APIRouter(prefix="/api/v1", tags=["documents"])

PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("/documents", response_model=DocumentListResponse)
def list_documents_endpoint(
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    request: Request,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    doc_type: DocType | None = None,
    status: DocStatus | None = None,
    warehouse_id: UUID | None = None,
    department_id: UUID | None = None,
    project_id: UUID | None = None,
    manufacturer_id: UUID | None = None,
    doc_no: str | None = None,
    deleted: bool | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DocumentListResponse:
    start_at, end_before = local_day_bounds(
        start_date, end_date, request.app.state.settings.timezone
    )
    rows, projects, total = list_documents(
        session,
        filters=DocumentFilters(
            doc_type=None if doc_type is None else doc_type.value,
            status=None if status is None else status.value,
            warehouse_id=None if warehouse_id is None else str(warehouse_id),
            department_id=None if department_id is None else str(department_id),
            project_id=None if project_id is None else str(project_id),
            manufacturer_id=None if manufacturer_id is None else str(manufacturer_id),
            doc_no=doc_no.strip() if doc_no else None,
            deleted=deleted,
            start_at=start_at,
            end_before=end_before,
        ),
        page=page,
        page_size=page_size,
    )
    return DocumentListResponse(
        items=[
            DocumentSummaryOut.model_validate(summary_payload(row, projects.get(row.id, [])))
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/documents",
    response_model=DocumentDetailOut,
    status_code=201,
    responses={
        200: {
            "model": DocumentDetailOut,
            "description": "同一客户端 request_id 的幂等重放，返回原单",
        }
    },
)
def create_document_endpoint(
    payload: DocumentCreate,
    auth: RequirePasswordChanged,
    request: Request,
    response: Response,
) -> DocumentDetailOut:
    # 创建单据的客户端 request_id 同时作为错误、日志与审计关联标识。
    request.state.request_id = str(payload.request_id)
    record = create_draft(
        request.app.state.runtime.session_factory,
        _document_input(payload),
        actor=auth.user,
        request_id=str(payload.request_id),
        timezone=request.app.state.settings.timezone,
        source="API",
    )
    if record.replayed:
        response.status_code = 200
    return DocumentDetailOut.model_validate(detail_payload(record.document, record.lines))


@router.get("/documents/{document_id}", response_model=DocumentDetailOut)
def get_document_endpoint(
    document_id: UUID,
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
) -> DocumentDetailOut:
    record = get_document(session, str(document_id))
    return DocumentDetailOut.model_validate(detail_payload(record.document, record.lines))


@router.patch("/documents/{document_id}", response_model=DocumentDetailOut)
def update_document_endpoint(
    document_id: UUID,
    payload: DocumentUpdate,
    auth: RequirePasswordChanged,
    request: Request,
) -> DocumentDetailOut:
    record = update_draft(
        request.app.state.runtime.session_factory,
        str(document_id),
        DocumentPatch(
            version=payload.version,
            warehouse_id=None if payload.warehouse_id is None else str(payload.warehouse_id),
            department_id=None if payload.department_id is None else str(payload.department_id),
            remark=payload.remark,
            remark_provided="remark" in payload.model_fields_set,
            lines=(
                None
                if payload.lines is None
                else tuple(_line_input(line) for line in payload.lines)
            ),
        ),
        actor=auth.user,
        source="API",
        request_id=get_request_id(),
    )
    return DocumentDetailOut.model_validate(detail_payload(record.document, record.lines))


@router.delete("/documents/{document_id}", status_code=204)
def delete_document_endpoint(
    document_id: UUID,
    payload: DocumentVersionRequest,
    auth: RequirePasswordChanged,
    request: Request,
) -> Response:
    delete_draft(
        request.app.state.runtime.session_factory,
        str(document_id),
        version=payload.version,
        actor=auth.user,
        source="API",
        request_id=get_request_id(),
    )
    return Response(status_code=204)


@router.post("/documents/{document_id}/submit", response_model=DocumentDetailOut)
def submit_document_endpoint(
    document_id: UUID,
    payload: DocumentVersionRequest,
    auth: RequirePasswordChanged,
    request: Request,
) -> DocumentDetailOut:
    record = submit_document(
        request.app.state.runtime.session_factory,
        str(document_id),
        version=payload.version,
        actor=auth.user,
        source="API",
        request_id=get_request_id(),
    )
    return DocumentDetailOut.model_validate(detail_payload(record.document, record.lines))


@router.post("/documents/{document_id}/delete", response_model=DocumentDetailOut)
def soft_delete_document_endpoint(
    document_id: UUID,
    auth: RequirePasswordChanged,
    request: Request,
) -> DocumentDetailOut:
    record = soft_delete_document(
        request.app.state.runtime.session_factory,
        str(document_id),
        actor=auth.user,
        source="API",
        request_id=get_request_id(),
    )
    return DocumentDetailOut.model_validate(detail_payload(record.document, record.lines))


def _document_input(payload: DocumentCreate) -> DocumentInput:
    return DocumentInput(
        doc_type=payload.doc_type,
        warehouse_id=str(payload.warehouse_id),
        department_id=str(payload.department_id),
        remark=payload.remark,
        lines=tuple(_line_input(line) for line in payload.lines),
    )


def _line_input(line: DocumentLineIn) -> DocumentLineInput:
    return DocumentLineInput(
        material_id=str(line.material_id),
        quantity=line.quantity,
        manufacturer_id=(None if line.manufacturer_id is None else str(line.manufacturer_id)),
        project_id=None if line.project_id is None else str(line.project_id),
    )
