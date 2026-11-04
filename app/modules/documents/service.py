"""单据草稿、提交与软删除业务服务。

依据 docs/06-库存计算与单据业务流程.md 7.2–7.5、7.7 与
docs/05-数据模型与实体约束.md 6.5：
- 草稿创建、修改、删除不影响库存；客户端 ``request_id`` 幂等，同键不同内容 409；
- 提交在 ``BEGIN IMMEDIATE`` 写事务内重新校验全部资料状态、数量与余额；
- 已生效单据不可修改，只允许软删除并退出库存汇总；
- 全部业务写入与审计在同一事务内提交，任一步失败整单回滚。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.listing import ListParams, SortOrder, order_by, resolve_sort
from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.errors import AppError
from app.modules.audit.service import record_audit
from app.modules.categories.models import Category
from app.modules.departments.models import Department
from app.modules.documents.counters import next_doc_no
from app.modules.documents.fingerprint import compute_request_fingerprint
from app.modules.documents.inputs import (
    DocumentInput,
    DocumentLineInput,
    PreparedDocument,
    PreparedLine,
    prepare_document,
    prepare_lines,
)
from app.modules.documents.models import (
    CreatedSource,
    DocStatus,
    DocType,
    StockDocument,
    StockDocumentLine,
)
from app.modules.inventory.quantity import ensure_quantity_matches_type
from app.modules.inventory.service import (
    ensure_removal_balances,
    ensure_submit_balances,
)
from app.modules.manufacturers.models import Manufacturer
from app.modules.materials.models import Material
from app.modules.projects.models import Project
from app.modules.users.models import Role, User
from app.modules.warehouses.models import Warehouse

MCP_DEDUP_WINDOW_SECONDS = 300
"""MCP 建单去重窗口：docs/08-MCP服务与工具.md 7.9 第 6 条硬编码为 300 秒。"""


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """已加载的单据头、明细与是否命中创建幂等。"""

    document: StockDocument
    lines: list[StockDocumentLine]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class DocumentCreation:
    """MCP 建单结果：是否命中 300 秒窗口去重。"""

    document: StockDocument
    lines: list[StockDocumentLine]
    deduplicated: bool = False


@dataclass(frozen=True, slots=True)
class DocumentPatch:
    """草稿修改的可编辑字段；明细整体替换时逐行备注随之更新。"""

    version: int
    warehouse_id: str | None = None
    department_id: str | None = None
    lines: tuple[DocumentLineInput, ...] | None = None


@dataclass(frozen=True, slots=True)
class DocumentFilters:
    doc_type: str | None = None
    status: str | None = None
    warehouse_id: str | None = None
    department_id: str | None = None
    project_id: str | None = None
    manufacturer_id: str | None = None
    doc_no: str | None = None
    deleted: bool | None = None
    start_at: datetime | None = None
    end_before: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResolvedLine:
    material: Material
    category: Category
    parent_category: Category | None
    manufacturer: Manufacturer | None
    project: Project | None
    quantity_milli: int
    remark: str | None = None


def create_draft(
    session_factory: sessionmaker[Session],
    data: DocumentInput,
    *,
    actor: User,
    request_id: str,
    timezone: ZoneInfo,
    source: str = CreatedSource.API.value,
    api_key_id: str | None = None,
    now: datetime | None = None,
) -> DocumentRecord:
    """创建普通出入库草稿；同 ``request_id`` 重放返回原单。"""
    created_at = now or utc_now()
    canonical_request_id = _canonical_request_id(request_id)
    prepared = prepare_document(data)
    fingerprint = compute_request_fingerprint(prepared, submit=False)
    source = _normalize_source(source)
    biz_date = created_at.astimezone(timezone).date()

    try:
        with session_factory() as session, begin_immediate(session):
            existing = _find_by_request_id(session, actor.id, canonical_request_id)
            if existing is not None:
                return _replay(existing, fingerprint, canonical_request_id)

            document = _build_document(
                session,
                prepared,
                actor=actor,
                source=source,
                request_id=canonical_request_id,
                fingerprint=fingerprint,
                created_at=created_at,
                biz_date=biz_date,
                api_key_id=api_key_id,
            )
            lines = list(document.lines)
            session.expunge_all()
            return DocumentRecord(document=document, lines=lines)
    except IntegrityError as exc:
        return _resolve_integrity_conflict(
            session_factory,
            exc,
            actor_id=actor.id,
            request_id=canonical_request_id,
            fingerprint=fingerprint,
        )


def create_mcp_document(
    session_factory: sessionmaker[Session],
    prepare: Callable[[Session], PreparedDocument],
    *,
    submit: bool,
    actor: User,
    request_id: str,
    api_key_id: str,
    timezone: ZoneInfo,
    now: datetime | None = None,
) -> DocumentCreation:
    """MCP 建单：``BEGIN IMMEDIATE`` 内完成编码解析、去重、建单与可选提交。

    依据 docs/08-MCP服务与工具.md 7.9：
    - ``prepare`` 在同一写事务内把业务编码解析为内部引用并返回规范化输入；
    - 窗口查询、建单与提交共用一个写事务，命中窗口时原样返回且不写任何记录；
    - ``submit=true`` 时在同一事务内校验库存并置为 ``POSTED``，失败整单回滚。
    """
    created_at = now or utc_now()
    canonical_request_id = _canonical_request_id(request_id)
    source = CreatedSource.MCP.value
    biz_date = created_at.astimezone(timezone).date()

    try:
        with session_factory() as session, begin_immediate(session):
            prepared = prepare(session)
            fingerprint = compute_request_fingerprint(prepared, submit=submit)
            duplicate = _find_mcp_duplicate(session, actor.id, fingerprint, created_at)
            if duplicate is not None:
                lines = list(duplicate.lines)
                session.expunge_all()
                return DocumentCreation(document=duplicate, lines=lines, deduplicated=True)

            document = _build_document(
                session,
                prepared,
                actor=actor,
                source=source,
                request_id=canonical_request_id,
                fingerprint=fingerprint,
                created_at=created_at,
                biz_date=biz_date,
                api_key_id=api_key_id,
            )
            if submit:
                _post_document_locked(
                    session,
                    document,
                    actor=actor,
                    submitted_at=created_at,
                    request_id=canonical_request_id,
                    source=source,
                    api_key_id=api_key_id,
                )
            lines = list(document.lines)
            session.expunge_all()
            return DocumentCreation(document=document, lines=lines)
    except IntegrityError as exc:
        _raise_mcp_integrity_error(exc)


def _build_document(
    session: Session,
    prepared: PreparedDocument,
    *,
    actor: User,
    source: str,
    request_id: str,
    fingerprint: str,
    created_at: datetime,
    biz_date: date,
    api_key_id: str | None = None,
) -> StockDocument:
    """在已取得的写事务内解析关联资料、生成单号并写入草稿及创建审计。"""
    warehouse, department = _resolve_header(session, prepared.warehouse_id, prepared.department_id)
    resolved_lines = _resolve_lines(session, prepared.doc_type, prepared.lines)
    doc_no = next_doc_no(session, prepared.doc_type, biz_date)
    document = StockDocument(
        doc_no=doc_no,
        doc_type=prepared.doc_type,
        status=DocStatus.DRAFT.value,
        warehouse_id=warehouse.id,
        warehouse_code=warehouse.code,
        warehouse_name=warehouse.name,
        department_id=department.id,
        department_code=department.code,
        department_name=department.name,
        created_source=source,
        request_id=request_id,
        request_fingerprint=fingerprint,
        version=1,
        created_by=actor.id,
        created_by_name=actor.display_name,
        created_at=created_at,
        updated_at=created_at,
    )
    document.lines = [
        _line_model(resolved, line_no) for line_no, resolved in enumerate(resolved_lines, start=1)
    ]
    session.add(document)
    session.flush()
    record_audit(
        session,
        actor_user_id=actor.id,
        action="DOCUMENT_CREATED",
        entity_type="document",
        entity_id=document.id,
        after=_document_snapshot(document),
        request_id=request_id,
        source=source,
        api_key_id=api_key_id,
        now=created_at,
    )
    return document


def _post_document_locked(
    session: Session,
    document: StockDocument,
    *,
    actor: User,
    submitted_at: datetime,
    request_id: str | None,
    source: str,
    api_key_id: str | None = None,
) -> None:
    """在已取得的写事务内重新校验并置为 ``POSTED``；库存不足整单回滚。"""
    before = _document_snapshot(document)
    warehouse, department = _resolve_header(session, document.warehouse_id, document.department_id)
    prepared_lines = _prepared_from_existing(document)
    resolved_lines = _resolve_lines(session, document.doc_type, prepared_lines)
    totals = _totals_from_resolved(resolved_lines)
    materials = {line.material.id: line.material for line in resolved_lines}
    ensure_submit_balances(
        session,
        warehouse_id=document.warehouse_id,
        doc_type=document.doc_type,
        totals=totals,
        materials=materials,
    )

    _apply_header(document, warehouse, department)
    document.status = DocStatus.POSTED.value
    document.submitted_by = actor.id
    document.submitted_by_name = actor.display_name
    document.submitted_at = submitted_at
    document.version = int(document.version) + 1
    document.updated_at = submitted_at
    session.flush()
    record_audit(
        session,
        actor_user_id=actor.id,
        action="DOCUMENT_SUBMITTED",
        entity_type="document",
        entity_id=document.id,
        before=before,
        after=_document_snapshot(document),
        request_id=request_id,
        source=source,
        api_key_id=api_key_id,
        now=submitted_at,
    )


def _find_mcp_duplicate(
    session: Session,
    actor_id: str,
    fingerprint: str,
    now: datetime,
) -> StockDocument | None:
    """窗口内查询同一创建者、相同指纹且未软删除的 MCP 单据。"""
    cutoff = now - timedelta(seconds=MCP_DEDUP_WINDOW_SECONDS)
    return session.scalar(
        select(StockDocument)
        .where(
            StockDocument.created_by == actor_id,
            StockDocument.created_source == CreatedSource.MCP.value,
            StockDocument.request_fingerprint == fingerprint,
            StockDocument.deleted_at.is_(None),
            StockDocument.created_at >= cutoff,
        )
        .order_by(StockDocument.created_at.desc(), StockDocument.doc_no.desc())
        .limit(1)
    )


def _raise_mcp_integrity_error(exc: IntegrityError) -> None:
    """把 MCP 建单的唯一约束冲突转换为明确业务错误。"""
    message = str(exc.orig)
    if "stock_documents.doc_no" in message:
        raise AppError(
            "DOCUMENT_NUMBER_CONFLICT",
            "单号生成冲突，请重试",
            status_code=409,
            resource="document",
        ) from exc
    raise AppError(
        "DOCUMENT_WRITE_CONFLICT",
        "单据写入冲突，请重试",
        status_code=409,
        resource="document",
    ) from exc


def update_draft(
    session_factory: sessionmaker[Session],
    document_id: str,
    patch: DocumentPatch,
    *,
    actor: User,
    source: str = CreatedSource.API.value,
    request_id: str | None = None,
    now: datetime | None = None,
) -> DocumentRecord:
    """修改普通草稿；仅管理员或本人可修改，并校验乐观锁版本。"""
    changed_at = now or utc_now()
    source = _normalize_source(source)
    with session_factory() as session, begin_immediate(session):
        document = _require_document(session, document_id)
        _ensure_can_modify(document, actor)
        _ensure_editable(document)
        _ensure_version(document, patch.version)

        before = _document_snapshot(document)
        warehouse, department = _resolve_header(
            session,
            patch.warehouse_id or document.warehouse_id,
            patch.department_id or document.department_id,
        )
        if patch.lines is not None:
            prepared_lines = prepare_lines(document.doc_type, patch.lines)
        else:
            prepared_lines = _prepared_from_existing(document)
        resolved_lines = _resolve_lines(session, document.doc_type, prepared_lines)

        if patch.lines is not None or len(document.lines) != len(resolved_lines):
            _replace_lines(session, document, resolved_lines)
        else:
            # 草稿每次保存刷新项目等显示快照；名称本身不可修改，此处仅保持一致性。
            _refresh_line_snapshots(document, resolved_lines)

        _apply_header(document, warehouse, department)
        document.version = int(document.version) + 1
        document.updated_at = changed_at
        session.flush()
        record_audit(
            session,
            actor_user_id=actor.id,
            action="DOCUMENT_UPDATED",
            entity_type="document",
            entity_id=document.id,
            before=before,
            after=_document_snapshot(document),
            request_id=request_id,
            source=source,
            now=changed_at,
        )
        lines = list(document.lines)
        session.expunge_all()
        return DocumentRecord(document=document, lines=lines)


def delete_draft(
    session_factory: sessionmaker[Session],
    document_id: str,
    *,
    version: int,
    actor: User,
    source: str = CreatedSource.API.value,
    request_id: str | None = None,
    now: datetime | None = None,
) -> None:
    """物理删除普通草稿；已生效单据只能软删除。"""
    deleted_at = now or utc_now()
    source = _normalize_source(source)
    with session_factory() as session, begin_immediate(session):
        document = _require_document(session, document_id)
        _ensure_can_modify(document, actor)
        _ensure_editable(document)
        _ensure_version(document, version)

        before = _document_snapshot(document)
        record_audit(
            session,
            actor_user_id=actor.id,
            action="DOCUMENT_DELETED",
            entity_type="document",
            entity_id=document.id,
            before=before,
            after=None,
            request_id=request_id,
            source=source,
            now=deleted_at,
        )
        session.delete(document)
        session.flush()


def submit_document(
    session_factory: sessionmaker[Session],
    document_id: str,
    *,
    version: int,
    actor: User,
    source: str = CreatedSource.API.value,
    request_id: str | None = None,
    api_key_id: str | None = None,
    now: datetime | None = None,
) -> DocumentRecord:
    """提交任意普通草稿；在写锁内重新校验全部规则与库存。"""
    submitted_at = now or utc_now()
    source = _normalize_source(source)
    with session_factory() as session, begin_immediate(session):
        document = _require_document(session, document_id)
        if document.deleted_at is not None:
            raise AppError(
                "DOCUMENT_ALREADY_DELETED",
                "单据已软删除，不能提交",
                status_code=409,
                resource="document",
                items=[{"doc_no": document.doc_no}],
            )
        if document.status == DocStatus.POSTED.value:
            raise AppError(
                "DOCUMENT_ALREADY_POSTED",
                "单据已生效，请勿重复提交",
                status_code=409,
                resource="document",
                items=[{"doc_no": document.doc_no, "status": document.status}],
            )
        _ensure_version(document, version)

        _post_document_locked(
            session,
            document,
            actor=actor,
            submitted_at=submitted_at,
            request_id=request_id,
            source=source,
            api_key_id=api_key_id,
        )
        lines = list(document.lines)
        session.expunge_all()
        return DocumentRecord(document=document, lines=lines)


def soft_delete_document(
    session_factory: sessionmaker[Session],
    document_id: str,
    *,
    actor: User,
    source: str = CreatedSource.API.value,
    request_id: str | None = None,
    now: datetime | None = None,
) -> DocumentRecord:
    """软删除已生效单据；移除本单后余额必须仍在合法区间。"""
    deleted_at = now or utc_now()
    source = _normalize_source(source)
    with session_factory() as session, begin_immediate(session):
        document = _require_document(session, document_id)
        _ensure_can_modify(document, actor)
        if document.deleted_at is not None:
            raise AppError(
                "DOCUMENT_ALREADY_DELETED",
                "单据已软删除，请勿重复删除",
                status_code=409,
                resource="document",
                items=[{"doc_no": document.doc_no}],
            )
        if document.status != DocStatus.POSTED.value:
            raise AppError(
                "DOCUMENT_NOT_POSTED",
                "草稿不能软删除，请使用草稿删除接口",
                status_code=409,
                resource="document",
                items=[{"doc_no": document.doc_no, "status": document.status}],
            )

        before = _document_snapshot(document)
        prepared_lines = _prepared_from_existing(document)
        material_ids = list(dict.fromkeys(line.material_id for line in prepared_lines))
        materials = _load_materials(session, material_ids)
        missing = [material_id for material_id in material_ids if material_id not in materials]
        if missing:
            raise AppError(
                "MATERIAL_NOT_FOUND",
                "物资不存在",
                status_code=404,
                resource="material",
                items=[{"id": material_id} for material_id in missing],
            )
        totals = _totals_from_prepared(prepared_lines)
        ensure_removal_balances(
            session,
            warehouse_id=document.warehouse_id,
            doc_type=document.doc_type,
            totals=totals,
            materials=materials,
        )

        document.deleted_at = deleted_at
        document.deleted_by = actor.id
        document.version = int(document.version) + 1
        document.updated_at = deleted_at
        session.flush()
        record_audit(
            session,
            actor_user_id=actor.id,
            action="DOCUMENT_SOFT_DELETED",
            entity_type="document",
            entity_id=document.id,
            before=before,
            after=_document_snapshot(document),
            request_id=request_id,
            source=source,
            now=deleted_at,
        )
        lines = list(document.lines)
        session.expunge_all()
        return DocumentRecord(document=document, lines=lines)


def get_document(session: Session, document_id: str) -> DocumentRecord:
    """读取单据及全部明细快照。"""
    document = _require_document(session, document_id)
    return DocumentRecord(document=document, lines=list(document.lines))


def list_documents(
    session: Session,
    *,
    filters: DocumentFilters | None = None,
    page: int = 1,
    page_size: int = 20,
    sort: str | None = None,
    order: SortOrder | None = None,
) -> tuple[list[StockDocument], dict[str, list[dict[str, object]]], int]:
    """分页查询单据，并按明细聚合去重项目摘要。"""
    filters = filters or DocumentFilters()
    conditions: list[object] = []
    if filters.doc_type:
        conditions.append(StockDocument.doc_type == filters.doc_type)
    if filters.status:
        conditions.append(StockDocument.status == filters.status)
    if filters.warehouse_id:
        conditions.append(StockDocument.warehouse_id == filters.warehouse_id)
    if filters.department_id:
        conditions.append(StockDocument.department_id == filters.department_id)
    if filters.doc_no:
        conditions.append(StockDocument.doc_no == filters.doc_no)
    if filters.deleted is True:
        conditions.append(StockDocument.deleted_at.is_not(None))
    elif filters.deleted is False:
        conditions.append(StockDocument.deleted_at.is_(None))
    if filters.start_at is not None:
        conditions.append(StockDocument.created_at >= filters.start_at)
    if filters.end_before is not None:
        conditions.append(StockDocument.created_at < filters.end_before)

    line_match = select(StockDocumentLine.id).where(
        StockDocumentLine.document_id == StockDocument.id
    )
    if filters.project_id:
        conditions.append(
            exists(line_match.where(StockDocumentLine.project_id == filters.project_id))
        )
    if filters.manufacturer_id:
        conditions.append(StockDocument.doc_type == DocType.INBOUND.value)
        conditions.append(
            exists(line_match.where(StockDocumentLine.manufacturer_id == filters.manufacturer_id))
        )

    total = int(
        session.scalar(select(func.count()).select_from(StockDocument).where(*conditions)) or 0
    )
    params = ListParams(page=page, page_size=page_size, sort=sort, order=order)
    column, direction = resolve_sort(
        params,
        fields={
            "created_at": StockDocument.created_at,
            "doc_no": StockDocument.doc_no,
            "submitted_at": StockDocument.submitted_at,
            "status": StockDocument.status,
            "doc_type": StockDocument.doc_type,
        },
        default="created_at",
        default_order=SortOrder.DESC,
        resource="document",
    )
    rows = list(
        session.scalars(
            select(StockDocument)
            .where(*conditions)
            .order_by(order_by(column, direction), StockDocument.doc_no.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    projects = _projects_by_document(session, [row.id for row in rows])
    return rows, projects, total


def _projects_by_document(
    session: Session, document_ids: Sequence[str]
) -> dict[str, list[dict[str, object]]]:
    if not document_ids:
        return {}
    rows = session.execute(
        select(
            StockDocumentLine.document_id,
            StockDocumentLine.project_id,
            StockDocumentLine.project_code,
            StockDocumentLine.project_name,
        )
        .where(
            StockDocumentLine.document_id.in_(list(document_ids)),
            StockDocumentLine.project_id.is_not(None),
        )
        .order_by(StockDocumentLine.document_id, StockDocumentLine.line_no)
    ).all()
    summaries: dict[str, list[dict[str, object]]] = {}
    seen: dict[str, set[str]] = {}
    for row in rows:
        document_id = str(row.document_id)
        project_id = str(row.project_id)
        if project_id in seen.setdefault(document_id, set()):
            continue
        seen[document_id].add(project_id)
        summaries.setdefault(document_id, []).append(
            {
                "project_id": project_id,
                "project_code": None if row.project_code is None else str(row.project_code),
                "project_name": None if row.project_name is None else str(row.project_name),
            }
        )
    return summaries


def _resolve_header(
    session: Session, warehouse_id: str, department_id: str
) -> tuple[Warehouse, Department]:
    warehouse = session.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise AppError(
            "WAREHOUSE_NOT_FOUND",
            "仓库不存在",
            status_code=404,
            resource="warehouse",
            items=[{"id": warehouse_id}],
        )
    if not warehouse.is_active:
        raise AppError(
            "WAREHOUSE_INACTIVE",
            "仓库已停用，不能用于新建或提交单据",
            status_code=409,
            resource="warehouse",
            items=[{"code": warehouse.code, "name": warehouse.name}],
        )
    department = session.get(Department, department_id)
    if department is None:
        raise AppError(
            "DEPARTMENT_NOT_FOUND",
            "部门不存在",
            status_code=404,
            resource="department",
            items=[{"id": department_id}],
        )
    if not department.is_active:
        raise AppError(
            "DEPARTMENT_INACTIVE",
            "部门已停用，不能用于新建或提交单据",
            status_code=409,
            resource="department",
            items=[{"code": department.code, "name": department.name}],
        )
    return warehouse, department


def _resolve_lines(
    session: Session,
    doc_type: str,
    prepared_lines: Sequence[PreparedLine],
) -> list[ResolvedLine]:
    """批量加载关联资料并逐行校验；任何一行失败即整单拒绝。"""
    duplicate_items = _duplicate_line_items(prepared_lines)
    if duplicate_items:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            resource="document",
            items=duplicate_items,
        )

    material_ids = list(dict.fromkeys(line.material_id for line in prepared_lines))
    materials = _load_materials(session, material_ids)
    category_ids = list(dict.fromkeys(material.category_id for material in materials.values()))
    categories = {
        row.id: row
        for row in session.scalars(select(Category).where(Category.id.in_(category_ids))).all()
    }
    parent_ids = list(
        dict.fromkeys(
            category.parent_id for category in categories.values() if category.parent_id is not None
        )
    )
    parents = (
        {
            row.id: row
            for row in session.scalars(select(Category).where(Category.id.in_(parent_ids))).all()
        }
        if parent_ids
        else {}
    )
    project_ids = list(
        dict.fromkeys(line.project_id for line in prepared_lines if line.project_id is not None)
    )
    projects = (
        {
            row.id: row
            for row in session.scalars(select(Project).where(Project.id.in_(project_ids))).all()
        }
        if project_ids
        else {}
    )
    manufacturer_ids = list(
        dict.fromkeys(
            line.manufacturer_id for line in prepared_lines if line.manufacturer_id is not None
        )
    )
    manufacturers = (
        {
            row.id: row
            for row in session.scalars(
                select(Manufacturer).where(Manufacturer.id.in_(manufacturer_ids))
            ).all()
        }
        if manufacturer_ids
        else {}
    )

    missing_manufacturer: list[dict[str, object]] = []
    inactive_projects: list[dict[str, object]] = []
    inactive_manufacturers: list[dict[str, object]] = []
    inactive_materials: list[dict[str, object]] = []
    inactive_categories: list[dict[str, object]] = []
    inactive_parents: list[dict[str, object]] = []
    resolved: list[ResolvedLine] = []

    for line_no, line in enumerate(prepared_lines, start=1):
        material = materials.get(line.material_id)
        if material is None:
            raise AppError(
                "MATERIAL_NOT_FOUND",
                "物资不存在",
                status_code=404,
                resource="material",
                items=[{"line_no": line_no, "id": line.material_id}],
            )
        category = categories.get(material.category_id)
        if category is None:  # pragma: no cover - 外键保证分类存在
            raise AppError(
                "CATEGORY_NOT_FOUND",
                "物资所属分类不存在",
                status_code=404,
                resource="category",
                items=[{"line_no": line_no, "id": material.category_id}],
            )
        parent = parents.get(category.parent_id) if category.parent_id else None

        project: Project | None = None
        if line.project_id is not None:
            project = projects.get(line.project_id)
            if project is None:
                raise AppError(
                    "PROJECT_NOT_FOUND",
                    "项目不存在",
                    status_code=404,
                    resource="project",
                    items=[{"line_no": line_no, "id": line.project_id}],
                )
            if not project.is_active:
                inactive_projects.append(
                    {
                        "line_no": line_no,
                        "code": project.code,
                        "name": project.name,
                    }
                )

        manufacturer: Manufacturer | None = None
        if doc_type == DocType.INBOUND.value:
            if line.manufacturer_id is None:
                missing_manufacturer.append(
                    {
                        "line_no": line_no,
                        "field": f"lines[{line_no}].manufacturer_id",
                        "reason": "入库明细必须填写厂家",
                    }
                )
            else:
                manufacturer = manufacturers.get(line.manufacturer_id)
                if manufacturer is None:
                    raise AppError(
                        "MANUFACTURER_NOT_FOUND",
                        "厂家不存在",
                        status_code=404,
                        resource="manufacturer",
                        items=[{"line_no": line_no, "id": line.manufacturer_id}],
                    )
                if not manufacturer.is_active:
                    inactive_manufacturers.append(
                        {
                            "line_no": line_no,
                            "code": manufacturer.code,
                            "name": manufacturer.name,
                        }
                    )
            if not material.is_active:
                inactive_materials.append(
                    {"line_no": line_no, "code": material.code, "name": material.name}
                )
            if not category.is_active:
                inactive_categories.append(
                    {"line_no": line_no, "code": category.code, "name": category.name}
                )
            elif parent is not None and not parent.is_active:
                inactive_parents.append(
                    {
                        "line_no": line_no,
                        "code": parent.code,
                        "name": parent.name,
                        "level": parent.level,
                    }
                )

        ensure_quantity_matches_type(
            line.quantity_milli,
            material.quantity_type,
            field=f"lines[{line_no}].quantity",
        )
        resolved.append(
            ResolvedLine(
                material=material,
                category=category,
                parent_category=parent,
                manufacturer=manufacturer,
                project=project,
                quantity_milli=line.quantity_milli,
                remark=line.remark,
            )
        )

    if missing_manufacturer:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            resource="document",
            items=missing_manufacturer,
        )
    if inactive_projects:
        raise AppError(
            "PROJECT_INACTIVE",
            "明细引用了已停用项目，保存或提交未执行",
            status_code=409,
            resource="project",
            items=inactive_projects,
        )
    if inactive_manufacturers:
        raise AppError(
            "MANUFACTURER_INACTIVE",
            "明细引用了已停用厂家，保存或提交未执行",
            status_code=409,
            resource="manufacturer",
            items=inactive_manufacturers,
        )
    if inactive_materials:
        raise AppError(
            "MATERIAL_INACTIVE",
            "入库明细引用了已停用物资，保存或提交未执行",
            status_code=409,
            resource="material",
            items=inactive_materials,
        )
    if inactive_categories:
        raise AppError(
            "CATEGORY_INACTIVE",
            "入库明细引用了已停用分类，保存或提交未执行",
            status_code=409,
            resource="category",
            items=inactive_categories,
        )
    if inactive_parents:
        raise AppError(
            "PARENT_INACTIVE",
            "入库明细引用了上级分类已停用的物资，保存或提交未执行",
            status_code=409,
            resource="category",
            items=inactive_parents,
        )
    return resolved


def _duplicate_line_items(prepared_lines: Sequence[PreparedLine]) -> list[dict[str, object]]:
    seen: dict[tuple[str, str | None], int] = {}
    duplicates: list[dict[str, object]] = []
    for line_no, line in enumerate(prepared_lines, start=1):
        key = (line.material_id, line.project_id)
        if key in seen:
            duplicates.append(
                {
                    "line_no": line_no,
                    "first_line_no": seen[key],
                    "material_id": line.material_id,
                    "project_id": line.project_id,
                }
            )
        else:
            seen[key] = line_no
    return duplicates


def _load_materials(session: Session, material_ids: Sequence[str]) -> dict[str, Material]:
    ids = list(dict.fromkeys(material_ids))
    if not ids:
        return {}
    rows = session.scalars(select(Material).where(Material.id.in_(ids))).all()
    return {row.id: row for row in rows}


def _totals_from_prepared(prepared_lines: Sequence[PreparedLine]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for line in prepared_lines:
        totals[line.material_id] = totals.get(line.material_id, 0) + line.quantity_milli
    return totals


def _line_model(resolved: ResolvedLine, line_no: int) -> StockDocumentLine:
    category = resolved.category
    parent = resolved.parent_category
    manufacturer = resolved.manufacturer
    project = resolved.project
    return StockDocumentLine(
        line_no=line_no,
        material_id=resolved.material.id,
        material_code=resolved.material.code,
        material_name=resolved.material.name,
        unit=resolved.material.unit,
        quantity_type=resolved.material.quantity_type,
        category_code=category.code,
        category_name=category.name,
        parent_category_code=None if parent is None else parent.code,
        parent_category_name=None if parent is None else parent.name,
        manufacturer_id=None if manufacturer is None else manufacturer.id,
        manufacturer_code=None if manufacturer is None else manufacturer.code,
        manufacturer_name=None if manufacturer is None else manufacturer.name,
        project_id=None if project is None else project.id,
        project_code=None if project is None else project.code,
        project_name=None if project is None else project.name,
        quantity_milli=resolved.quantity_milli,
        remark=resolved.remark,
    )


def _replace_lines(
    session: Session, document: StockDocument, resolved_lines: Sequence[ResolvedLine]
) -> None:
    document.lines.clear()
    session.flush()
    document.lines = [
        _line_model(resolved, line_no) for line_no, resolved in enumerate(resolved_lines, start=1)
    ]
    session.flush()


def _refresh_line_snapshots(
    document: StockDocument, resolved_lines: Sequence[ResolvedLine]
) -> None:
    for line, resolved in zip(document.lines, resolved_lines, strict=True):
        line.material_code = resolved.material.code
        line.material_name = resolved.material.name
        line.unit = resolved.material.unit
        line.quantity_type = resolved.material.quantity_type
        line.category_code = resolved.category.code
        line.category_name = resolved.category.name
        line.parent_category_code = (
            None if resolved.parent_category is None else resolved.parent_category.code
        )
        line.parent_category_name = (
            None if resolved.parent_category is None else resolved.parent_category.name
        )
        if resolved.project is not None:
            line.project_code = resolved.project.code
            line.project_name = resolved.project.name


def _apply_header(
    document: StockDocument,
    warehouse: Warehouse,
    department: Department,
) -> None:
    document.warehouse_id = warehouse.id
    document.warehouse_code = warehouse.code
    document.warehouse_name = warehouse.name
    document.department_id = department.id
    document.department_code = department.code
    document.department_name = department.name


def _prepared_from_existing(document: StockDocument) -> tuple[PreparedLine, ...]:
    lines = tuple(
        PreparedLine(
            material_id=line.material_id,
            manufacturer_id=line.manufacturer_id,
            project_id=line.project_id,
            quantity_milli=line.quantity_milli,
            remark=line.remark,
        )
        for line in document.lines
    )
    if not lines:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            resource="document",
            items=[{"field": "lines", "reason": "单据至少需要一条明细"}],
        )
    return lines


def _require_document(session: Session, document_id: str) -> StockDocument:
    document = session.get(StockDocument, document_id)
    if document is None:
        raise AppError(
            "DOCUMENT_NOT_FOUND",
            "单据不存在",
            status_code=404,
            resource="document",
            items=[{"id": document_id}],
        )
    return document


def _find_by_request_id(session: Session, actor_id: str, request_id: str) -> StockDocument | None:
    return session.scalar(
        select(StockDocument).where(
            StockDocument.created_by == actor_id,
            StockDocument.request_id == request_id,
        )
    )


def _replay(document: StockDocument, fingerprint: str, request_id: str) -> DocumentRecord:
    if document.request_fingerprint != fingerprint:
        raise AppError(
            "REQUEST_ID_CONFLICT",
            "同一 request_id 已用于不同内容的建单请求",
            status_code=409,
            resource="document",
            items=[{"request_id": request_id, "doc_no": document.doc_no}],
        )
    lines = list(document.lines)
    return DocumentRecord(document=document, lines=lines, replayed=True)


def _resolve_integrity_conflict(
    session_factory: sessionmaker[Session],
    exc: IntegrityError,
    *,
    actor_id: str,
    request_id: str,
    fingerprint: str,
) -> DocumentRecord:
    message = str(exc.orig)
    if "stock_documents.created_by" in message and "stock_documents.request_id" in message:
        with session_factory() as session:
            existing = _find_by_request_id(session, actor_id, request_id)
            if existing is not None:
                return _replay(existing, fingerprint, request_id)
        raise AppError(
            "REQUEST_ID_CONFLICT",
            "同一 request_id 已用于不同内容的建单请求",
            status_code=409,
            resource="document",
            items=[{"request_id": request_id}],
        ) from exc
    if "stock_documents.doc_no" in message:
        raise AppError(
            "DOCUMENT_NUMBER_CONFLICT",
            "单号生成冲突，请重试",
            status_code=409,
            resource="document",
            items=[{"doc_no": None}],
        ) from exc
    raise AppError(
        "DOCUMENT_WRITE_CONFLICT",
        "单据写入冲突，请重试",
        status_code=409,
        resource="document",
    ) from exc


def _ensure_can_modify(document: StockDocument, actor: User) -> None:
    if actor.role == Role.COMPANY_ADMIN.value:
        return
    if document.created_by != actor.id:
        raise AppError(
            "FORBIDDEN",
            "只能修改或删除本人创建的单据",
            status_code=403,
            resource="document",
            items=[{"doc_no": document.doc_no}],
        )


def _ensure_editable(document: StockDocument) -> None:
    if document.deleted_at is not None:
        raise AppError(
            "DOCUMENT_ALREADY_DELETED",
            "单据已软删除，不能修改或删除",
            status_code=409,
            resource="document",
            items=[{"doc_no": document.doc_no}],
        )
    if document.status != DocStatus.DRAFT.value:
        raise AppError(
            "DOCUMENT_NOT_DRAFT",
            "已生效单据不能修改或删除，只能软删除",
            status_code=409,
            resource="document",
            items=[{"doc_no": document.doc_no, "status": document.status}],
        )


def _ensure_version(document: StockDocument, version: int) -> None:
    if int(document.version) != int(version):
        raise AppError(
            "VERSION_CONFLICT",
            "单据已被其他人修改，请刷新后重试",
            status_code=409,
            resource="document",
            items=[
                {
                    "doc_no": document.doc_no,
                    "expected": int(version),
                    "current": int(document.version),
                }
            ],
        )


def _document_snapshot(document: StockDocument) -> dict[str, object]:
    return {
        "doc_no": document.doc_no,
        "doc_type": document.doc_type,
        "status": document.status,
        "version": document.version,
        "warehouse_code": document.warehouse_code,
        "department_code": document.department_code,
        "line_count": len(document.lines),
        "deleted": document.deleted_at is not None,
        "submitted_at": (
            None if document.submitted_at is None else document.submitted_at.isoformat()
        ),
        "deleted_at": (None if document.deleted_at is None else document.deleted_at.isoformat()),
    }


def _canonical_request_id(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError) as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            resource="document",
            items=[{"field": "request_id", "reason": "request_id 必须是 UUID"}],
        ) from exc


def _normalize_source(source: str) -> str:
    try:
        return CreatedSource(source).value
    except ValueError as exc:  # pragma: no cover - 调用方固定传值
        raise ValueError(f"未知建单来源：{source}") from exc


def _totals_from_resolved(resolved_lines: Sequence[ResolvedLine]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for line in resolved_lines:
        material_id = line.material.id
        totals[material_id] = totals.get(material_id, 0) + line.quantity_milli
    return totals
