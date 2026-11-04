"""单据、库存相关页面：列表、新建、编辑、详情、提交与软删除。"""

from datetime import date
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.api.listing import ListQuery
from app.db.database import get_session
from app.errors import AppError
from app.modules.documents.inputs import DocumentInput, DocumentLineInput
from app.modules.documents.models import DocStatus, DocType
from app.modules.documents.presenter import detail_payload, summary_payload
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
from app.modules.xlsx.parser import ParsedDocument, parse_document_entry
from app.modules.xlsx.uploads import spool_upload
from app.pages import common
from app.pages.dependencies import PageForm, PageUser
from app.pages.templating import flash, redirect, render
from app.request_context import get_request_id

router = APIRouter(prefix="/documents", include_in_schema=False)


def _doc_type(value: str | None, default: str = "INBOUND") -> str:
    return value if value in {item.value for item in DocType} else default


def _uuid_text(value: Any, default: str | None = None) -> str | None:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        return default


def _line_from_form(
    form: Any,
    index: int,
) -> DocumentLineInput:
    def value(name: str) -> str:
        values = form.getlist(name)
        return str(values[index]) if index < len(values) else ""

    return DocumentLineInput(
        material_id=value("line_material_id"),
        quantity=value("line_quantity"),
        manufacturer_id=value("line_manufacturer_id") or None,
        project_id=value("line_project_id") or None,
        remark=value("line_remark") or None,
    )


def _lines_from_form(form: Any) -> tuple[list[DocumentLineInput], list[dict[str, str]]]:
    materials = form.getlist("line_material_id")
    lines: list[DocumentLineInput] = []
    rows: list[dict[str, str]] = []
    for index in range(len(materials)):
        row = {
            "material_id": str(materials[index]),
            "quantity": _form_value(form, "line_quantity", index),
            "manufacturer_id": _form_value(form, "line_manufacturer_id", index),
            "project_id": _form_value(form, "line_project_id", index),
            "remark": _form_value(form, "line_remark", index),
        }
        if not any(row.values()):
            continue
        lines.append(_line_from_form(form, index))
        rows.append(row)
    if not rows:
        rows.append(_blank_line())
    return lines, rows


def _form_value(form: Any, name: str, index: int) -> str:
    values = form.getlist(name)
    return str(values[index]) if index < len(values) else ""


def _blank_line() -> dict[str, str]:
    return {
        "material_id": "",
        "quantity": "",
        "manufacturer_id": "",
        "project_id": "",
        "remark": "",
    }


def _form_options(
    session: Session,
    *,
    include_inactive: bool = False,
    include_inactive_materials: bool = False,
) -> dict[str, Any]:
    return {
        "warehouses": common.select_options(
            session, "warehouse", include_inactive=include_inactive
        ),
        "departments": common.select_options(
            session, "department", include_inactive=include_inactive
        ),
        "projects": common.select_options(session, "project", include_inactive=include_inactive),
        "manufacturers": common.select_options(
            session, "manufacturer", include_inactive=include_inactive
        ),
        "materials": common.material_options(session, include_inactive=include_inactive_materials),
    }


def _render_list(
    request: Request,
    auth: Any,
    session: Session,
    params: Any,
    *,
    error: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    query = request.query_params
    deleted_param = query.get("deleted") or ""
    deleted = True if deleted_param == "yes" else False if deleted_param == "no" else None
    start_date = _date_or_none(query.get("start_date"))
    end_date = _date_or_none(query.get("end_date"))
    start_at, end_before = local_day_bounds(
        start_date, end_date, request.app.state.settings.timezone
    )
    rows, projects, total = list_documents(
        session,
        filters=DocumentFilters(
            doc_type=(query.get("doc_type") or None),
            status=(query.get("status") or None),
            warehouse_id=_uuid_text(query.get("warehouse_id")),
            department_id=_uuid_text(query.get("department_id")),
            project_id=_uuid_text(query.get("project_id")),
            manufacturer_id=_uuid_text(query.get("manufacturer_id")),
            doc_no=(query.get("doc_no") or "").strip() or None,
            deleted=deleted,
            start_at=start_at,
            end_before=end_before,
        ),
        page=params.page,
        page_size=params.page_size,
    )
    options = _form_options(session, include_inactive=True)
    return render(
        request,
        "documents/list.html",
        auth=auth,
        status_code=status_code,
        items=[summary_payload(row, projects.get(row.id, [])) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
        **options,
        filters={
            "doc_type": query.get("doc_type") or "",
            "status": query.get("status") or "",
            "warehouse_id": query.get("warehouse_id") or "",
            "department_id": query.get("department_id") or "",
            "project_id": query.get("project_id") or "",
            "manufacturer_id": query.get("manufacturer_id") or "",
            "doc_no": query.get("doc_no") or "",
            "deleted": deleted_param,
            "start_date": query.get("start_date") or "",
            "end_date": query.get("end_date") or "",
        },
        error=error,
    )


@router.get("")
def documents_page(
    request: Request,
    auth: PageUser,
    session: Annotated[Session, Depends(get_session)],
    params: ListQuery,
) -> Response:
    return _render_list(request, auth, session, params)


def _render_form(
    request: Request,
    auth: Any,
    session: Session,
    *,
    mode: str,
    doc_type: str,
    warehouse_id: str,
    department_id: str,
    lines: list[dict[str, str]],
    request_id: str,
    document: Any = None,
    version: int = 0,
    error: dict[str, Any] | None = None,
    parse_result: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    include_inactive = mode == "edit"
    include_inactive_materials = mode == "edit" or doc_type == DocType.OUTBOUND.value
    options = _form_options(
        session,
        include_inactive=include_inactive,
        include_inactive_materials=include_inactive_materials,
    )
    return render(
        request,
        "documents/form.html",
        auth=auth,
        status_code=status_code,
        mode=mode,
        doc_type=doc_type,
        document=document,
        version=version,
        warehouse_id=warehouse_id,
        department_id=department_id,
        lines=lines,
        request_id=request_id,
        **options,
        error=error,
        parse_result=parse_result,
        blank_line=_blank_line(),
    )


@router.get("/new")
def new_document_page(
    request: Request,
    auth: PageUser,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    doc_type = _doc_type(request.query_params.get("doc_type"))
    return _render_form(
        request,
        auth,
        session,
        mode="new",
        doc_type=doc_type,
        warehouse_id="",
        department_id="",
        lines=[_blank_line()],
        request_id=str(uuid4()),
    )


@router.post("/new")
async def create_document_page(
    request: Request,
    auth: PageForm,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    form = await request.form()
    doc_type = _doc_type(str(form.get("doc_type", "")))
    request_id = _uuid_text(form.get("request_id"), str(uuid4())) or str(uuid4())
    warehouse_id = str(form.get("warehouse_id", ""))
    department_id = str(form.get("department_id", ""))
    action = str(form.get("action", "save"))
    lines, rows = _lines_from_form(form)
    try:
        record = create_draft(
            request.app.state.runtime.session_factory,
            DocumentInput(
                doc_type=doc_type,
                warehouse_id=warehouse_id,
                department_id=department_id,
                lines=tuple(lines),
            ),
            actor=auth.user,
            request_id=request_id,
            timezone=request.app.state.settings.timezone,
            source="WEB",
        )
    except AppError as exc:
        return _render_form(
            request,
            auth,
            session,
            mode="new",
            doc_type=doc_type,
            warehouse_id=warehouse_id,
            department_id=department_id,
            lines=rows,
            request_id=request_id,
            error=common.error_view(exc, get_request_id() or ""),
            status_code=exc.status_code,
        )

    if action == "submit":
        try:
            submitted = submit_document(
                request.app.state.runtime.session_factory,
                record.document.id,
                version=record.document.version,
                actor=auth.user,
                source="WEB",
                request_id=get_request_id(),
            )
        except AppError as exc:
            return _render_detail(
                request,
                auth,
                submitted_record=record,
                error=common.error_view(exc, get_request_id() or ""),
                created_draft=True,
                status_code=exc.status_code,
            )
        flash(auth, f"单据 {submitted.document.doc_no} 已提交生效", detail="库存已更新。")
        return redirect(f"/documents/{submitted.document.id}")
    flash(auth, "草稿已保存", detail="草稿不计入库存，可稍后编辑或提交。")
    return redirect(f"/documents/{record.document.id}")


@router.get("/{document_id}")
def document_page(
    document_id: UUID,
    request: Request,
    auth: PageUser,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    record = get_document(session, str(document_id))
    return _render_detail(request, auth, record=record)


@router.get("/{document_id}/edit")
def edit_document_page(
    document_id: UUID,
    request: Request,
    auth: PageUser,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    record = get_document(session, str(document_id))
    if record.document.status != DocStatus.DRAFT.value or record.document.deleted_at is not None:
        flash(auth, "只有未删除的草稿可以编辑", level="warning")
        return redirect(f"/documents/{record.document.id}")
    if auth.user.role != "COMPANY_ADMIN" and record.document.created_by != auth.user.id:
        raise AppError("FORBIDDEN", "只能编辑本人创建的草稿", status_code=403, resource="document")
    payload = detail_payload(record.document, record.lines)
    lines = [
        {
            "material_id": line["material_id"],
            "quantity": line["quantity"],
            "manufacturer_id": line["manufacturer_id"] or "",
            "project_id": line["project_id"] or "",
            "remark": line["remark"] or "",
        }
        for line in payload["lines"]
    ]
    return _render_form(
        request,
        auth,
        session,
        mode="edit",
        doc_type=payload["doc_type"],
        warehouse_id=payload["warehouse_id"],
        department_id=payload["department_id"],
        lines=lines or [_blank_line()],
        request_id=payload["request_id"],
        document=payload,
        version=payload["version"],
    )


@router.post("/{document_id}/edit")
async def update_document_page(
    document_id: UUID,
    request: Request,
    auth: PageForm,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    form = await request.form()
    doc_type = _doc_type(str(form.get("doc_type", "INBOUND")))
    warehouse_id = str(form.get("warehouse_id", ""))
    department_id = str(form.get("department_id", ""))
    action = str(form.get("action", "save"))
    version = int(str(form.get("version", "0")) or "0")
    lines, rows = _lines_from_form(form)
    try:
        record = update_draft(
            request.app.state.runtime.session_factory,
            str(document_id),
            DocumentPatch(
                version=version,
                warehouse_id=warehouse_id,
                department_id=department_id,
                lines=tuple(lines),
            ),
            actor=auth.user,
            source="WEB",
            request_id=get_request_id(),
        )
    except AppError as exc:
        current = get_document(session, str(document_id))
        return _render_form(
            request,
            auth,
            session,
            mode="edit",
            doc_type=doc_type,
            warehouse_id=warehouse_id,
            department_id=department_id,
            lines=rows,
            request_id=current.document.request_id,
            document=detail_payload(current.document, current.lines),
            version=current.document.version,
            error=common.error_view(exc, get_request_id() or ""),
            status_code=exc.status_code,
        )

    if action == "submit":
        try:
            submitted = submit_document(
                request.app.state.runtime.session_factory,
                record.document.id,
                version=record.document.version,
                actor=auth.user,
                source="WEB",
                request_id=get_request_id(),
            )
        except AppError as exc:
            return _render_detail(
                request,
                auth,
                submitted_record=record,
                error=common.error_view(exc, get_request_id() or ""),
                created_draft=False,
                status_code=exc.status_code,
            )
        flash(auth, f"单据 {submitted.document.doc_no} 已提交生效")
        return redirect(f"/documents/{submitted.document.id}")
    flash(auth, "草稿已保存", detail="草稿不计入库存。")
    return redirect(f"/documents/{record.document.id}")


def _render_detail(
    request: Request,
    auth: Any,
    *,
    record: Any = None,
    submitted_record: Any = None,
    error: dict[str, Any] | None = None,
    created_draft: bool = False,
    status_code: int = 200,
) -> Response:
    record = record or submitted_record
    payload = detail_payload(record.document, record.lines)
    is_admin = auth.user.role == "COMPANY_ADMIN"
    is_owner = record.document.created_by == auth.user.id
    is_draft = record.document.status == DocStatus.DRAFT.value
    is_deleted = record.document.deleted_at is not None
    return render(
        request,
        "documents/detail.html",
        auth=auth,
        status_code=status_code,
        document=payload,
        lines=payload["lines"],
        error=error,
        created_draft=created_draft,
        can_edit=is_draft and not is_deleted and (is_admin or is_owner),
        can_submit=is_draft and not is_deleted,
        can_discard=is_draft and not is_deleted and (is_admin or is_owner),
        can_soft_delete=not is_draft and not is_deleted and (is_admin or is_owner),
        can_export=True,
    )


@router.post("/{document_id}/submit")
async def submit_document_page(
    document_id: UUID,
    request: Request,
    auth: PageForm,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    form = await request.form()
    try:
        version = int(str(form.get("version", "0")) or "0")
    except ValueError:
        version = 0
    try:
        record = submit_document(
            request.app.state.runtime.session_factory,
            str(document_id),
            version=version,
            actor=auth.user,
            source="WEB",
            request_id=get_request_id(),
        )
    except AppError as exc:
        current = get_document(session, str(document_id))
        return _render_detail(
            request,
            auth,
            record=current,
            error=common.error_view(exc, get_request_id() or ""),
            status_code=exc.status_code,
        )
    flash(auth, f"单据 {record.document.doc_no} 已生效", detail="库存已更新。")
    return redirect(f"/documents/{record.document.id}")


@router.post("/{document_id}/discard")
async def discard_document_page(
    document_id: UUID,
    request: Request,
    auth: PageForm,
) -> Response:
    form = await request.form()
    try:
        version = int(str(form.get("version", "0")) or "0")
    except ValueError:
        version = 0
    try:
        delete_draft(
            request.app.state.runtime.session_factory,
            str(document_id),
            version=version,
            actor=auth.user,
            source="WEB",
            request_id=get_request_id(),
        )
    except AppError as exc:
        flash(auth, f"删除草稿失败：{exc.message}", level="danger")
        return redirect(f"/documents/{document_id}")
    flash(auth, "草稿已删除")
    return redirect("/documents")


@router.post("/{document_id}/delete")
async def soft_delete_document_page(
    document_id: UUID,
    request: Request,
    auth: PageForm,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    try:
        record = soft_delete_document(
            request.app.state.runtime.session_factory,
            str(document_id),
            actor=auth.user,
            source="WEB",
            request_id=get_request_id(),
        )
    except AppError as exc:
        current = get_document(session, str(document_id))
        return _render_detail(
            request,
            auth,
            record=current,
            error=common.error_view(exc, get_request_id() or ""),
            status_code=exc.status_code,
        )
    flash(auth, f"单据 {record.document.doc_no} 已软删除", detail="该单据已退出库存汇总。")
    return redirect(f"/documents/{record.document.id}")


@router.post("/parse")
async def parse_document_page(
    request: Request,
    auth: PageForm,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    form = await request.form()
    doc_type = _doc_type(str(form.get("doc_type", "")))
    warehouse_id = str(form.get("warehouse_id", ""))
    department_id = str(form.get("department_id", ""))
    request_id = _uuid_text(form.get("request_id"), str(uuid4())) or str(uuid4())
    _, rows = _lines_from_form(form)
    upload = form.get("file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return _render_form(
            request,
            auth,
            session,
            mode="new",
            doc_type=doc_type,
            warehouse_id=warehouse_id,
            department_id=department_id,
            lines=rows,
            request_id=request_id,
            error=common.error_view(
                AppError("XLSX_FILE_REQUIRED", "请选择要解析的 XLSX 文件", status_code=422),
                get_request_id() or "",
            ),
            status_code=422,
        )
    path = spool_upload(upload)
    try:
        parsed = parse_document_entry(path, doc_type=doc_type, session=session)
    except AppError as exc:
        return _render_form(
            request,
            auth,
            session,
            mode="new",
            doc_type=doc_type,
            warehouse_id=warehouse_id,
            department_id=department_id,
            lines=rows,
            request_id=request_id,
            error=common.error_view(exc, get_request_id() or ""),
            status_code=exc.status_code,
        )
    finally:
        path.unlink(missing_ok=True)

    lines = [
        {
            "material_id": line.material_id,
            "quantity": line.quantity,
            "manufacturer_id": line.manufacturer_id or "",
            "project_id": line.project_id or "",
            "remark": line.remark or "",
        }
        for line in parsed.lines
    ]
    return _render_form(
        request,
        auth,
        session,
        mode="new",
        doc_type=parsed.doc_type,
        warehouse_id=parsed.warehouse_id,
        department_id=parsed.department_id,
        lines=lines or [_blank_line()],
        request_id=request_id,
        parse_result=_parse_result(parsed),
    )


def _parse_result(parsed: ParsedDocument) -> dict[str, Any]:
    return {
        "status": parsed.status,
        "notice": parsed.notice,
        "total_rows": parsed.total_rows,
        "filled_rows": len(parsed.lines),
        "skipped_rows": parsed.total_rows - len(parsed.lines),
        "errors": [
            {
                "source_row_no": error.source_row_no,
                "error_field": error.error_field,
                "error_code": error.error_code,
                "message": error.message,
            }
            for error in parsed.errors
        ],
    }


def _date_or_none(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
