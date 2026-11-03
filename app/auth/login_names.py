"""登录名与显示名规范化。

依据 docs/04-认证、会话与凭证安全.md 5.4：登录名规范化后不区分大小写，并在
全系统唯一。规范化只去除首尾空白并折叠大小写，不删除内部字符。
"""

from app.errors import AppError

LOGIN_NAME_MAX_LENGTH = 64
DISPLAY_NAME_MAX_LENGTH = 100


def normalize_login_name(value: str) -> str:
    return value.strip().casefold()


def validate_login_name(value: str, *, field: str = "login_name") -> str:
    normalized = normalize_login_name(value)
    reason = _login_name_reason(normalized)
    if reason is not None:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            items=[{"field": field, "reason": reason}],
        )
    return normalized


def validate_display_name(value: str, *, field: str = "display_name") -> str:
    normalized = value.strip()
    if not normalized:
        reason = "显示名不能为空"
    elif len(normalized) > DISPLAY_NAME_MAX_LENGTH:
        reason = f"显示名不能超过 {DISPLAY_NAME_MAX_LENGTH} 位"
    elif _contains_control(normalized):
        reason = "显示名不能包含控制字符"
    else:
        return normalized
    raise AppError(
        "VALIDATION_ERROR",
        "请求参数校验失败",
        status_code=422,
        items=[{"field": field, "reason": reason}],
    )


def _login_name_reason(normalized: str) -> str | None:
    if not normalized:
        return "登录名不能为空"
    if len(normalized) > LOGIN_NAME_MAX_LENGTH:
        return f"登录名不能超过 {LOGIN_NAME_MAX_LENGTH} 位"
    if _contains_control(normalized):
        return "登录名不能包含控制字符"
    return None


def _contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
