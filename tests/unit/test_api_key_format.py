import hashlib

import pytest

from app.auth.api_keys import (
    API_KEY_PREFIX,
    MAX_ACTIVE_API_KEYS,
    generate_api_key,
    validate_api_key_name,
)
from app.errors import AppError


def test_generated_key_has_prefix_hash_and_suffix() -> None:
    raw_key, key_hash, key_suffix = generate_api_key()

    assert raw_key.startswith(API_KEY_PREFIX)
    assert len(raw_key) >= len(API_KEY_PREFIX) + 43
    assert key_hash == hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    assert key_suffix == raw_key[-4:]
    assert raw_key not in key_hash


def test_generated_keys_are_unique() -> None:
    keys = {generate_api_key()[0] for _ in range(25)}

    assert len(keys) == 25


def test_key_name_is_trimmed_and_length_checked() -> None:
    assert validate_api_key_name("  MCP client  ") == "MCP client"
    with pytest.raises(AppError) as empty_error:
        validate_api_key_name("   ")
    assert empty_error.value.status_code == 422
    with pytest.raises(AppError) as long_error:
        validate_api_key_name("a" * 51)
    assert long_error.value.status_code == 422
    assert MAX_ACTIVE_API_KEYS == 10
