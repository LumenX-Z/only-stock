import logging

from app.logging import RequestIdFilter, UtcFormatter
from app.request_context import reset_request_id, set_request_id

_FORMAT = "%(asctime)s %(levelname)s request_id=%(request_id)s %(message)s"


def _record() -> logging.LogRecord:
    return logging.LogRecord("app.test", logging.INFO, __file__, 10, "hello", None, None)


def test_formatter_uses_utc_and_includes_request_id() -> None:
    record = _record()
    record.request_id = "request-1"

    output = UtcFormatter(_FORMAT).format(record)

    assert output.endswith("hello")
    assert " request_id=request-1 " in output
    assert "Z " in output


def test_filter_uses_current_request_context() -> None:
    token = set_request_id("request-2")
    try:
        record = _record()
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "request-2"
    finally:
        reset_request_id(token)


def test_filter_keeps_explicit_request_id() -> None:
    record = _record()
    record.request_id = "explicit"

    assert RequestIdFilter().filter(record) is True
    assert record.request_id == "explicit"
