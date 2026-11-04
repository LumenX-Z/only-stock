"""MCP 工具参数校验。

依据 docs/08-MCP服务与工具.md 8.6.1、8.6.3、8.6.5：
- 工具输入一律不接受 ``id``、``*_id``、内部 UUID 或 ``request_id``，传入按参数
  校验错误拒绝，不得静默忽略；
- 基础资料创建工具不接受 ``code``；分页默认 ``1``/``20``，最大 ``100``；
- 校验失败统一抛出 ``VALIDATION_ERROR``，``details.items`` 只包含字段与原因。
"""

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from app.errors import AppError

_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def require_object(arguments: object) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        raise _invalid("arguments", "工具参数必须是 JSON 对象")
    return dict(arguments)


def reject_unknown(
    arguments: Mapping[str, Any],
    allowed: Collection[str],
) -> None:
    """拒绝未声明字段；内部 ID 与 ``request_id`` 优先给出明确原因。"""
    items: list[dict[str, object]] = []
    for key in arguments:
        if key in allowed:
            continue
        if key == "request_id":
            items.append(_item(key, "工具不接受 request_id，由服务端生成"))
        elif key == "id" or key.endswith("_id"):
            items.append(_item(key, "工具不接受内部 ID 参数"))
        else:
            items.append(_item(key, "未知参数"))
    if items:
        raise _invalid_items(items)


def optional_string(
    arguments: Mapping[str, Any],
    name: str,
    *,
    max_length: int | None = None,
    strip: bool = False,
) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid(name, "必须是字符串")
    if max_length is not None and len(value) > max_length:
        raise _invalid(name, f"不能超过 {max_length} 字符")
    return value.strip() if strip else value


def required_string(
    arguments: Mapping[str, Any],
    name: str,
    *,
    max_length: int | None = None,
    strip: bool = False,
) -> str:
    value = optional_string(arguments, name, max_length=max_length, strip=strip)
    if value is None or value == "":
        raise _invalid(name, "不能为空")
    return value


def optional_bool(arguments: Mapping[str, Any], name: str, default: bool = False) -> bool:
    value = arguments.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _invalid(name, "必须是布尔值")
    return value


def optional_code(arguments: Mapping[str, Any], name: str) -> str | None:
    """关联引用编码：不接受内部 UUID 形式的取值。"""
    value = optional_string(arguments, name, max_length=64)
    if value is None:
        return None
    if value == "":
        raise _invalid(name, "提供时不能为空字符串")
    _reject_uuid_value(name, value)
    return value


def required_code(arguments: Mapping[str, Any], name: str) -> str:
    value = optional_code(arguments, name)
    if not value:
        raise _invalid(name, "不能为空")
    return value


def page_parameters(arguments: Mapping[str, Any]) -> tuple[int, int]:
    page = _positive_int(arguments, "page", DEFAULT_PAGE, maximum=None)
    page_size = _positive_int(arguments, "page_size", DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_SIZE)
    return page, page_size


def reject_duplicate_line_keys(
    pairs: Sequence[tuple[str, str | None]],
) -> None:
    """同一物资与项目组合不得重复；项目为空视为同一取值。"""
    seen: dict[tuple[str, str | None], int] = {}
    items: list[dict[str, object]] = []
    for line_no, key in enumerate(pairs, start=1):
        if key in seen:
            items.append(
                {
                    "field": f"lines[{line_no}]",
                    "line_no": line_no,
                    "first_line_no": seen[key],
                    "material_code": key[0],
                    "project_code": key[1],
                    "reason": "同一物资在同一项目下只能出现一行",
                }
            )
        else:
            seen[key] = line_no
    if items:
        raise _invalid_items(items)


def invalid(field: str, reason: str) -> AppError:
    return _invalid(field, reason)


def invalid_items(items: Sequence[Mapping[str, object]]) -> AppError:
    return _invalid_items(items)


def _reject_uuid_value(field: str, value: str) -> None:
    if _UUID_PATTERN.fullmatch(value):
        raise _invalid(field, "只接受业务编码，不接受内部 UUID")


def _positive_int(
    arguments: Mapping[str, Any],
    name: str,
    default: int,
    *,
    maximum: int | None,
) -> int:
    value = arguments.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(name, "必须是整数")
    if value < 1:
        raise _invalid(name, "必须大于等于 1")
    if maximum is not None and value > maximum:
        raise _invalid(name, f"必须小于等于 {maximum}")
    return value


def _invalid(field: str, reason: str) -> AppError:
    return _invalid_items([_item(field, reason)])


def _invalid_items(items: Sequence[Mapping[str, object]]) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "请求参数校验失败",
        status_code=422,
        resource="tool",
        items=[dict(item) for item in items],
    )


def _item(field: str, reason: str) -> dict[str, object]:
    return {"field": field, "reason": reason}
