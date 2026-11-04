"""库存与流水响应模型。

依据 docs/11-单据与库存业务API.md 8.5：库存项返回仓库、物资、单位、数量类型与
可用量；流水按明细行返回方向、数量、单号、生效时间和逐行项目信息；所有数量为
不带科学计数法的十进制字符串。
"""

from datetime import datetime

from pydantic import BaseModel


class InventoryItemOut(BaseModel):
    warehouse_id: str
    warehouse_code: str
    warehouse_name: str
    material_id: str
    code: str
    material_name: str
    unit: str
    quantity_type: str
    balance: str


class InventoryListResponse(BaseModel):
    items: list[InventoryItemOut]
    page: int
    page_size: int
    total: int


class InventoryTransactionOut(BaseModel):
    id: str
    line_no: int
    doc_no: str
    direction: str
    quantity: str
    warehouse_id: str
    warehouse_code: str
    warehouse_name: str
    department_id: str
    department_code: str
    department_name: str
    material_id: str
    material_code: str
    material_name: str
    unit: str
    project_id: str | None = None
    project_code: str | None = None
    project_name: str | None = None
    manufacturer_id: str | None = None
    manufacturer_code: str | None = None
    manufacturer_name: str | None = None
    submitted_at: datetime


class InventoryTransactionsResponse(BaseModel):
    items: list[InventoryTransactionOut]
    page: int
    page_size: int
    total: int


__all__ = [
    "InventoryItemOut",
    "InventoryListResponse",
    "InventoryTransactionOut",
    "InventoryTransactionsResponse",
]
