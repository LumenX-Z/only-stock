"""XLSX 解析响应模型。

依据 docs/11-单据与库存业务API.md 8.5 的部分填入响应示例：
- 成功返回 200，文件级错误返回 422；
- ``status`` 仅为本次解析的 ``SUCCESS``/``PARTIAL``，不是数据库状态；
- 响应不包含单据 ID、批次 ID、文件哈希或错误报告地址。
"""

from typing import Literal

from pydantic import BaseModel, Field


class ParseLineOut(BaseModel):
    material_id: str
    material_name: str
    unit: str
    quantity_type: str
    quantity: str
    manufacturer_id: str | None = None
    manufacturer_name: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    category_name: str
    parent_category_name: str | None = None
    remark: str | None = None


class ParseDocumentOut(BaseModel):
    warehouse_id: str
    warehouse_name: str
    department_id: str
    department_name: str
    lines: list[ParseLineOut] = Field(default_factory=list)


class ParseErrorOut(BaseModel):
    source_row_no: int
    error_field: str
    error_code: str
    message: str


class ParseResponse(BaseModel):
    doc_type: str
    status: Literal["SUCCESS", "PARTIAL"]
    notice: str
    document: ParseDocumentOut
    total_rows: int
    filled_rows: int
    skipped_rows: int
    errors: list[ParseErrorOut] = Field(default_factory=list)
