from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.db.types import UtcDateTime, new_uuid, utc_now

_COLUMN = UtcDateTime()


def test_epoch_milliseconds_roundtrip_in_utc() -> None:
    value = datetime(2026, 11, 3, 12, 34, 56, 789000, tzinfo=UTC)
    expected_ms = 1_793_709_296_789

    stored = _COLUMN.process_bind_param(value, None)
    restored = _COLUMN.process_result_value(stored, None)

    assert stored == expected_ms
    assert restored == value
    assert restored is not None
    assert restored.tzinfo is UTC


def test_sub_millisecond_precision_is_truncated() -> None:
    value = datetime(2026, 11, 3, 12, 34, 56, 789456, tzinfo=UTC)

    stored = _COLUMN.process_bind_param(value, None)
    restored = _COLUMN.process_result_value(stored, None)

    assert restored == value.replace(microsecond=789000)


def test_timezone_is_converted_to_utc_before_storage() -> None:
    shanghai = timezone(timedelta(hours=8))
    value = datetime(2026, 11, 4, 4, 34, 56, tzinfo=shanghai)

    stored = _COLUMN.process_bind_param(value, None)
    restored = _COLUMN.process_result_value(stored, None)

    assert restored == datetime(2026, 11, 3, 20, 34, 56, tzinfo=UTC)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError):
        _COLUMN.process_bind_param(datetime(2026, 11, 3, 12, 0, 0), None)


def test_utc_now_is_aware_and_millisecond_precision() -> None:
    now = utc_now()

    assert now.tzinfo is UTC
    assert now.microsecond % 1000 == 0


def test_new_uuid_returns_uuid_string() -> None:
    value = new_uuid()

    assert str(UUID(value)) == value
    assert new_uuid() != value
