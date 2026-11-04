"""六类基础资料的请求与响应模型。

请求模型统一 ``extra="forbid"``：六类创建接口不接受 ``code``（docs/10 8.4），
携带编码或其他未知字段返回 422，避免静默忽略后创建成功。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas import PageMeta


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WarehouseCreate(_StrictRequest):
    name: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)


class DepartmentCreate(_StrictRequest):
    name: str = Field(min_length=1, max_length=200)


class ProjectCreate(_StrictRequest):
    name: str = Field(min_length=1, max_length=200)


class ManufacturerCreate(_StrictRequest):
    name: str = Field(min_length=1, max_length=200)


class CategoryCreate(_StrictRequest):
    name: str = Field(min_length=1, max_length=200)
    parent_id: UUID | None = None


class MaterialCreate(_StrictRequest):
    category_id: UUID
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=50)
    quantity_type: Literal["INTEGER", "DECIMAL"]


class NamedEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class WarehouseOut(NamedEntityOut):
    location: str | None = None


class CategoryOut(NamedEntityOut):
    parent_id: str | None
    parent_code: str | None = None
    level: int
    children: list["CategoryOut"] = Field(default_factory=list)


CategoryOut.model_rebuild()


class MaterialOut(NamedEntityOut):
    category_id: str
    category_code: str
    category_name: str
    parent_category_code: str | None = None
    parent_category_name: str | None = None
    unit: str
    quantity_type: str


class NamedEntityListResponse(PageMeta):
    items: list[NamedEntityOut]


class WarehouseListResponse(PageMeta):
    items: list[WarehouseOut]


class CategoryListResponse(PageMeta):
    items: list[CategoryOut]


class MaterialListResponse(PageMeta):
    items: list[MaterialOut]
