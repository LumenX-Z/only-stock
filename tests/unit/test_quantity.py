"""数量解析、千分比转换与精度校验单元测试。"""

from decimal import Decimal

import pytest

from app.errors import AppError
from app.modules.inventory.quantity import (
    MAX_BALANCE_MILLI,
    MAX_QUANTITY_MILLI,
    ensure_quantity_matches_type,
    format_quantity_milli,
    parse_quantity,
    quantity_milli,
    quantity_to_milli,
)


@pytest.mark.parametrize(
    ("text", "milli"),
    [
        ("1", 1000),
        ("1.25", 1250),
        ("1.001", 1001),
        ("1.234", 1234),
        ("0.001", 1),
        ("999999999999999.999", MAX_QUANTITY_MILLI),
    ],
)
def test_decimal_strings_convert_exactly(text: str, milli: int) -> None:
    assert quantity_to_milli(text) == milli
    assert parse_quantity(text) == Decimal(text)


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "0.000",
        "-1",
        "1.2345",
        "1.2500",
        "1e3",
        "1E+2",
        "1.",
        ".5",
        "abc",
        "",
        "9999999999999999.999",
    ],
)
def test_invalid_quantity_strings_are_rejected(value: str) -> None:
    with pytest.raises(AppError) as error:
        quantity_to_milli(value)
    assert error.value.status_code == 422
    assert error.value.code == "VALIDATION_ERROR"


def test_floats_bools_and_scientific_decimals_are_rejected() -> None:
    for value in (1.25, True, Decimal("1E+2"), Decimal("Infinity")):
        with pytest.raises(AppError):
            parse_quantity(value)  # type: ignore[arg-type]


def test_decimal_object_with_three_places_is_accepted() -> None:
    assert quantity_milli(Decimal("1.25")) == 1250


def test_format_quantity_uses_three_places_without_scientific_notation() -> None:
    assert format_quantity_milli(1250) == "1.250"
    assert format_quantity_milli(1) == "0.001"
    assert format_quantity_milli(0) == "0.000"
    assert format_quantity_milli(-1250) == "-1.250"


def test_integer_material_rejects_fraction() -> None:
    ensure_quantity_matches_type(2000, "INTEGER")
    with pytest.raises(AppError):
        ensure_quantity_matches_type(1250, "INTEGER")
    ensure_quantity_matches_type(1250, "DECIMAL")
    with pytest.raises(AppError):
        ensure_quantity_matches_type(1000, "UNKNOWN")


def test_balance_limit_is_below_signed_64_bit_maximum() -> None:
    assert MAX_BALANCE_MILLI == 9_000_000_000_000_000_000
    assert MAX_BALANCE_MILLI < 2**63 - 1
