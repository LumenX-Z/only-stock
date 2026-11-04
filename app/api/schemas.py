"""列表响应公共模型。

依据 docs/09-HTTP-API通用约定与健康检查.md 8.1：所有列表响应统一为
``items``、``page``、``page_size``、``total``。
"""

from pydantic import BaseModel


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
