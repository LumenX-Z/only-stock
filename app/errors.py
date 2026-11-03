"""应用错误与统一错误响应结构。

依据 docs/09-HTTP-API通用约定与健康检查.md 8.1：错误体固定为
``{"error": {"code", "message", "request_id", "details"}}``，其中
``details`` 必须是对象并至少包含 ``resource`` 与 ``items``。
"""

from collections.abc import Sequence
from typing import Any


class AppError(Exception):
    """可安全返回给客户端的业务/协议错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        resource: str | None = None,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.resource = resource
        self.items: list[dict[str, Any]] = list(items or [])


class DatabaseBusyError(AppError):
    """SQLITE_BUSY/SQLITE_LOCKED 的统一映射，文档要求返回 409 写锁冲突。"""

    def __init__(self) -> None:
        super().__init__(
            "WRITE_CONFLICT",
            "数据库正在处理其他写入，请稍后重试",
            status_code=409,
        )


def error_payload(
    *,
    code: str,
    message: str,
    request_id: str,
    resource: str | None = None,
    items: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": {
                "resource": resource,
                "items": list(items or []),
            },
        }
    }
