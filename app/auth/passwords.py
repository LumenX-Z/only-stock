"""密码哈希与密码规则。

依据 docs/04-认证、会话与凭证安全.md 5.4：密码使用 Argon2id 哈希；至少 12 位；
初始密码与重置密码首次登录必须修改。密码明文不得落库或进入日志。
"""

import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

from app.errors import AppError

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256

_hasher = PasswordHasher(type=Type.ID)
_dummy_hash: str | None = None
_dummy_lock = threading.Lock()


def validate_password(password: str, *, field: str = "password") -> None:
    """校验密码长度；失败返回 422，不回显密码内容。"""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            items=[{"field": field, "reason": f"密码至少 {PASSWORD_MIN_LENGTH} 位"}],
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            items=[{"field": field, "reason": f"密码不能超过 {PASSWORD_MAX_LENGTH} 位"}],
        )


def hash_password(password: str) -> str:
    """校验规则后生成 Argon2id 哈希；调用方不得记录原始密码。"""
    validate_password(password)
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """常量时间校验密码，任何格式或校验异常均视为不匹配。"""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_dummy_password(password: str) -> None:
    """不存在的账户也执行一次哈希校验，降低按响应时间枚举账户的可能。"""
    global _dummy_hash
    if _dummy_hash is None:
        with _dummy_lock:
            if _dummy_hash is None:
                _dummy_hash = _hasher.hash("only-stock-dummy-password")
    verify_password(_dummy_hash, password)


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
