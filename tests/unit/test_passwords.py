import pytest

from app.auth.passwords import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    hash_password,
    password_needs_rehash,
    validate_password,
    verify_dummy_password,
    verify_password,
)
from app.errors import AppError


def test_password_hash_is_argon2id_and_verifiable() -> None:
    password = "correct-horse-battery"
    password_hash = hash_password(password)

    assert password_hash.startswith("$argon2id$")
    assert password not in password_hash
    assert verify_password(password_hash, password) is True
    assert verify_password(password_hash, "wrong-password-123") is False
    assert password_needs_rehash(password_hash) is False


def test_password_minimum_and_maximum_length() -> None:
    assert PASSWORD_MIN_LENGTH == 12
    with pytest.raises(AppError) as short_error:
        validate_password("a" * (PASSWORD_MIN_LENGTH - 1))
    assert short_error.value.status_code == 422
    assert short_error.value.items[0]["field"] == "password"

    validate_password("a" * PASSWORD_MIN_LENGTH)
    validate_password("a" * PASSWORD_MAX_LENGTH)
    with pytest.raises(AppError):
        validate_password("a" * (PASSWORD_MAX_LENGTH + 1))


def test_password_error_does_not_echo_password() -> None:
    password = "short"
    with pytest.raises(AppError) as error:
        validate_password(password)

    assert password not in str(error.value)
    assert password not in str(error.value.items)


def test_verify_invalid_or_corrupt_hash_returns_false() -> None:
    assert verify_password("not-a-hash", "any-password-123") is False


def test_dummy_verification_accepts_any_password_without_raising() -> None:
    verify_dummy_password("unknown-account-password")
