"""六类名称规范化单元测试。"""

import pytest

from app.errors import AppError
from app.modules.masterdata.names import normalize_name


def test_only_surrounding_whitespace_is_removed() -> None:
    assert normalize_name("  主仓库  ") == "主仓库"
    assert normalize_name("Alpha") == "Alpha"
    assert normalize_name("alpha") == "alpha"
    assert normalize_name("内部  空格") == "内部  空格"


@pytest.mark.parametrize("value", ["", "   ", "\t", "a" * 101, "换行\n名称"])
def test_invalid_names_are_rejected(value: str) -> None:
    with pytest.raises(AppError) as error:
        normalize_name(value)
    assert error.value.status_code == 422
    assert error.value.items[0]["field"] == "name"
