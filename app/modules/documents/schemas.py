"""单据 HTTP 请求与响应模型。

依据 docs/11-单据与库存业务API.md 8.5 与 docs/09-HTTP-API通用约定与健康检查.md 8.1：
创建请求必须携带客户端 ``request_id``，``project_id`` 与 ``remark`` 只允许出现在
``lines[]``；请求模型 ``extra="forbid"``，单据头出现 ``project_id``、``remark``
或携带未知字段返回 422；数量使用十进制字符串，响应中的数量固定为三位小数且
不使用科学计数法。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.schemas import PageMeta
from app.modules.documents.models import REMARK_MAX_LENGTH

MAX_REQUEST_LINES = 2000


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentLineIn(_StrictRequest):
    material_id: UUID
    quantity: str = Field(min_length=1, max_length=32)
    manufacturer_id: UUID | None = None
    project_id: UUID | None = None
    remark: str | None = Field(default=None, max_length=REMARK_MAX_LENGTH)

    @field_validator("remark", mode="before")
    @classmethod
    def _strip_remark(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class DocumentCreate(_StrictRequest):
    request_id: UUID
    doc_type: Literal["INBOUND", "OUTBOUND"]
    warehouse_id: UUID
    department_id: UUID
    lines: list[DocumentLineIn] = Field(min_length=1, max_length=MAX_REQUEST_LINES)


class DocumentUpdate(_StrictRequest):
    version: int = Field(ge=1)
    warehouse_id: UUID | None = None
    department_id: UUID | None = None
    lines: list[DocumentLineIn] | None = Field(
        default=None, min_length=1, max_length=MAX_REQUEST_LINES
    )


class DocumentVersionRequest(_StrictRequest):
    version: int = Field(ge=1)


class ProjectSummaryOut(BaseModel):
    project_id: str
    project_code: str | None = None
    project_name: str | None = None


class DocumentLineOut(BaseModel):
    id: str
    line_no: int
    material_id: str
    material_code: str
    material_name: str
    unit: str
    quantity_type: str
    quantity: str
    manufacturer_id: str | None = None
    manufacturer_code: str | None = None
    manufacturer_name: str | None = None
    project_id: str | None = None
    project_code: str | None = None
    project_name: str | None = None
    remark: str | None = None
    category_code: str
    category_name: str
    parent_category_code: str | None = None
    parent_category_name: str | None = None


class DocumentSummaryOut(BaseModel):
    id: str
    doc_no: str
    doc_type: str
    status: str
    warehouse_id: str
    warehouse_code: str
    warehouse_name: str
    department_id: str
    department_code: str
    department_name: str
    version: int
    created_source: str
    created_by: str
    created_by_name: str
    created_at: datetime
    submitted_by: str | None = None
    submitted_by_name: str | None = None
    submitted_at: datetime | None = None
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    deleted: bool
    projects: list[ProjectSummaryOut] = Field(default_factory=list)


class DocumentDetailOut(DocumentSummaryOut):
    request_id: str
    lines: list[DocumentLineOut] = Field(default_factory=list)


class DocumentListResponse(PageMeta):
    items: list[DocumentSummaryOut]
