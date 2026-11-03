"""六类实体编码前缀与固定 5 位格式单元测试。"""

import pytest

from app.modules.masterdata.codes import MAX_CODE_SEQUENCE, format_code
from app.modules.masterdata.registry import SPECS, get_spec


def test_every_entity_uses_documented_prefix() -> None:
    prefixes = {spec.key: spec.prefix for spec in SPECS}
    assert prefixes == {
        "warehouse": "CK",
        "department": "BM",
        "category": "FL",
        "material": "WZ",
        "project": "XM",
        "manufacturer": "CJ",
    }


@pytest.mark.parametrize(
    ("prefix", "value", "expected"),
    [
        ("CK", 1, "CK-00001"),
        ("WZ", 42, "WZ-00042"),
        ("FL", 99999, "FL-99999"),
    ],
)
def test_format_code_pads_to_five_digits(prefix: str, value: int, expected: str) -> None:
    assert format_code(prefix, value) == expected


@pytest.mark.parametrize("value", [0, -1, MAX_CODE_SEQUENCE + 1])
def test_format_code_rejects_out_of_range_sequence(value: int) -> None:
    with pytest.raises(ValueError):
        format_code("CK", value)


def test_unknown_entity_type_is_programming_error() -> None:
    with pytest.raises(ValueError):
        get_spec("unknown")
