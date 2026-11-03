import pytest

from app.auth.login_names import (
    DISPLAY_NAME_MAX_LENGTH,
    LOGIN_NAME_MAX_LENGTH,
    normalize_login_name,
    validate_display_name,
    validate_login_name,
)
from app.errors import AppError


def test_login_name_normalization_strips_and_folds_case() -> None:
    assert normalize_login_name("  Admin.User  ") == "admin.user"
    assert validate_login_name("  Admin.User  ") == "admin.user"


def test_login_name_rejects_empty_whitespace_control_and_overlong() -> None:
    for value in ("   ", "bad\x00name", "a" * (LOGIN_NAME_MAX_LENGTH + 1)):
        with pytest.raises(AppError) as error:
            validate_login_name(value)
        assert error.value.status_code == 422


def test_login_name_allows_unicode_internal_spaces_and_symbols() -> None:
    assert validate_login_name("Zhang.San-01") == "zhang.san-01"
    assert validate_login_name("张三") == "张三"
    assert validate_login_name("John Doe") == "john doe"


def test_display_name_trims_and_rejects_empty_control_and_overlong() -> None:
    assert validate_display_name("  张三  ") == "张三"
    with pytest.raises(AppError):
        validate_display_name("   ")
    with pytest.raises(AppError):
        validate_display_name("bad\x00name")
    with pytest.raises(AppError):
        validate_display_name("a" * (DISPLAY_NAME_MAX_LENGTH + 1))
