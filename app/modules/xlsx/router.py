"""XLSX 模板下载、无副作用解析与单张单据导出接口。

依据 docs/11-单据与库存业务API.md 8.5：
- ``GET /document-templates/{doc_type}``：CU；
- ``POST /document-entry/parse``：CA、OP，multipart，需 CSRF，不要求 ``request_id``；
- ``GET /documents/{id}/export``：CU。

解析接口在不持有数据库写锁的情况下读取基础资料，不写单据、审计或解析记录。
"""

import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.responses import COMMON_ERROR_RESPONSES
from app.auth.permissions import RequirePasswordChanged
from app.db.database import get_session
from app.modules.documents.models import DocType
from app.modules.documents.service import get_document
from app.modules.xlsx.constants import MAX_DATA_ROWS, MAX_UPLOAD_BYTES
from app.modules.xlsx.errors import file_error
from app.modules.xlsx.export import build_export_bytes
from app.modules.xlsx.parser import ParsedDocument, parse_document_entry
from app.modules.xlsx.schemas import (
    ParseDocumentOut,
    ParseErrorOut,
    ParseLineOut,
    ParseResponse,
)
from app.modules.xlsx.templates import (
    build_template,
    content_disposition,
    export_filenames,
    template_filenames,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["xlsx"],
    responses=COMMON_ERROR_RESPONSES,
)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_UPLOAD_CHUNK = 1024 * 1024


@router.get(
    "/document-templates/{doc_type}",
    response_class=Response,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "XLSX 模板文件"}},
)
def download_document_template(
    doc_type: DocType,
    _auth: RequirePasswordChanged,
) -> Response:
    """动态生成入库或出库模板；两种角色均可下载。"""
    data = build_template(doc_type.value)
    ascii_name, chinese_name = template_filenames(doc_type.value)
    return _xlsx_response(data, ascii_name, chinese_name)


@router.post("/document-entry/parse", response_model=ParseResponse)
def parse_document_entry_endpoint(
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    doc_type: Annotated[DocType, Form(...)],
    file: Annotated[UploadFile, File(...)],
) -> ParseResponse:
    """解析 XLSX 并返回新建页表单数据；不创建或修改任何业务数据。"""
    path = _spool_upload(file)
    try:
        parsed = parse_document_entry(path, doc_type=doc_type.value, session=session)
    finally:
        path.unlink(missing_ok=True)
    return _parse_response(parsed)


@router.get(
    "/documents/{document_id}/export",
    response_class=Response,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "XLSX 单据导出文件"}},
)
def export_document(
    document_id: UUID,
    _auth: RequirePasswordChanged,
    session: Annotated[Session, Depends(get_session)],
    request: Request,
) -> Response:
    """导出单张单据；草稿取当前资料，已生效及软删除单取快照。"""
    record = get_document(session, str(document_id))
    data = build_export_bytes(
        session,
        record.document,
        record.lines,
        timezone=request.app.state.settings.timezone,
    )
    ascii_name, chinese_name = export_filenames(record.document.doc_type, record.document.doc_no)
    return _xlsx_response(
        data,
        ascii_name,
        chinese_name,
        quick_entry=len(record.lines) <= MAX_DATA_ROWS,
    )


def _parse_response(parsed: ParsedDocument) -> ParseResponse:
    return ParseResponse(
        doc_type=parsed.doc_type,
        status=parsed.status,
        notice=parsed.notice,
        document=ParseDocumentOut(
            warehouse_id=parsed.warehouse_id,
            warehouse_name=parsed.warehouse_name,
            department_id=parsed.department_id,
            department_name=parsed.department_name,
            lines=[
                ParseLineOut(
                    material_id=line.material_id,
                    material_name=line.material_name,
                    unit=line.unit,
                    quantity_type=line.quantity_type,
                    quantity=line.quantity,
                    manufacturer_id=line.manufacturer_id,
                    manufacturer_name=line.manufacturer_name,
                    project_id=line.project_id,
                    project_name=line.project_name,
                    category_name=line.category_name,
                    parent_category_name=line.parent_category_name,
                    remark=line.remark,
                )
                for line in parsed.lines
            ],
        ),
        total_rows=parsed.total_rows,
        filled_rows=len(parsed.lines),
        skipped_rows=parsed.total_rows - len(parsed.lines),
        errors=[
            ParseErrorOut(
                source_row_no=error.source_row_no,
                error_field=error.error_field,
                error_code=error.error_code,
                message=error.message,
            )
            for error in parsed.errors
        ],
    )


def _spool_upload(file: UploadFile) -> Path:
    """流式写入服务端随机临时文件（最小权限），超出限制立即拒绝。"""
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="only-stock-xlsx-", suffix=".xlsx", delete=False
        ) as handle:
            path = Path(handle.name)
            total = 0
            while True:
                chunk = file.file.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise file_error(
                        "XLSX_FILE_TOO_LARGE",
                        f"XLSX 文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB，已拒绝",
                        items=[
                            {
                                "field": "file",
                                "limit_bytes": MAX_UPLOAD_BYTES,
                            }
                        ],
                    )
                handle.write(chunk)
        return path
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise


def _xlsx_response(
    data: bytes,
    ascii_name: str,
    chinese_name: str,
    *,
    quick_entry: bool | None = None,
) -> Response:
    headers = {
        "Content-Disposition": content_disposition(ascii_name, chinese_name),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if quick_entry is not None:
        headers["X-XLSX-Quick-Entry"] = "enabled" if quick_entry else "disabled"
    return Response(content=data, media_type=XLSX_MEDIA_TYPE, headers=headers)
