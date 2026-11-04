"""XLSX 文件级错误构造。"""

from collections.abc import Sequence
from typing import Any

from app.errors import AppError


def file_error(
    code: str,
    message: str,
    *,
    resource: str = "xlsx",
    items: Sequence[dict[str, Any]] | None = None,
) -> AppError:
    """文件级错误整份拒绝，按 docs/11 8.5 返回 422。"""
    return AppError(
        code,
        message,
        status_code=422,
        resource=resource,
        items=items,
    )
