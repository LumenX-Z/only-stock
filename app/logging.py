"""结构化日志配置。

依据 docs/14-安全、可靠性与性能要求.md 11.2：单条日志包含时间、级别、
请求 ID、模块和错误摘要；时间以 UTC 存储，页面展示本地时区。
日志不记录密码、会话、完整 API Key、Authorization 头或请求体。
"""

import logging
from datetime import UTC, datetime

from app.request_context import get_request_id

_FORMAT = "%(asctime)s %(levelname)s request_id=%(request_id)s logger=%(name)s %(message)s"
_CONFIGURED = False


class UtcFormatter(logging.Formatter):
    """ISO 8601 UTC 时间戳，便于采集与排序。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC)
        return timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RequestIdFilter(logging.Filter):
    """把当前请求 ID 注入日志记录；显式传入的 request_id 优先。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id() or "-"
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """幂等地配置根日志器；不覆盖 Uvicorn 自己的日志配置。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(UtcFormatter(_FORMAT))
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    _CONFIGURED = True
