"""数量精确存储与校验。

依据 docs/05-数据模型与实体约束.md 6.6：
- 输入先按十进制字符串解析为 ``Decimal``，禁止浮点直接传入；
- ``quantity_milli = round_half_up(decimal_quantity * 1000)``，最多三位小数；
- 整数物资要求 ``quantity_milli % 1000 == 0``；
- 实际数量大于 0，最大 ``999999999999999.999``；
- 库存余额上限 ``MAX_BALANCE_MILLI`` 低于 SQLite 有符号 64 位上限。
"""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum

from app.errors import AppError

QUANTITY_SCALE = 1000
QUANTITY_MAX_DECIMALS = 3
MAX_QUANTITY_MILLI = 999_999_999_999_999_999
MAX_BALANCE_MILLI = 9_000_000_000_000_000_000

_QUANTITY_PATTERN = re.compile(r"^\d+(?:\.\d{1,3})?$")


class QuantityKind(StrEnum):
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"


def parse_quantity(value: str | int | Decimal, *, field: str = "quantity") -> Decimal:
    """把严格十进制表示解析为 ``Decimal``；拒绝浮点、科学计数法和超精度。"""
    if isinstance(value, bool):
        raise _invalid_quantity(field, "数量必须是十进制字符串")
    if isinstance(value, float):
        raise _invalid_quantity(field, "数量必须按字符串传入，禁止浮点")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, Decimal):
        if not value.is_finite() or value.as_tuple().exponent > 0:
            raise _invalid_quantity(field, "数量不允许科学计数法")
        text = format(value, "f")
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise _invalid_quantity(field, "数量必须是十进制字符串")

    if not _QUANTITY_PATTERN.fullmatch(text):
        raise _invalid_quantity(field, "数量必须是最多三位小数的正十进制数")
    try:
        quantity = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - 正则已限制
        raise _invalid_quantity(field, "数量不是有效十进制数") from exc

    if quantity <= 0:
        raise _invalid_quantity(field, "数量必须大于零")
    milli = quantity_to_milli(quantity, field=field)
    if milli > MAX_QUANTITY_MILLI:
        raise _invalid_quantity(field, "数量超过允许的最大值")
    return quantity


def quantity_to_milli(value: str | int | Decimal, *, field: str = "quantity") -> int:
    """转换为千分比整数；四舍五入仅在合法三位小数内发生。"""
    quantity = value if isinstance(value, Decimal) else parse_quantity(value, field=field)
    if not quantity.is_finite() or quantity.as_tuple().exponent < -QUANTITY_MAX_DECIMALS:
        raise _invalid_quantity(field, "数量最多三位小数")
    scaled = (quantity * QUANTITY_SCALE).to_integral_value(rounding=ROUND_HALF_UP)
    return int(scaled)


def quantity_milli(value: Decimal) -> int:
    """已解析 ``Decimal`` 的千分比转换，供仍持有 Decimal 的业务调用。"""
    if not value.is_finite() or value.as_tuple().exponent < -QUANTITY_MAX_DECIMALS:
        raise _invalid_quantity("quantity", "数量最多三位小数")
    return int((value * QUANTITY_SCALE).to_integral_value(rounding=ROUND_HALF_UP))


def format_quantity_milli(milli: int) -> str:
    """千分比整数转为不带科学计数法的十进制字符串，如 ``1.250``。"""
    sign = "-" if milli < 0 else ""
    absolute = abs(milli)
    return f"{sign}{absolute // QUANTITY_SCALE}.{absolute % QUANTITY_SCALE:03d}"


def ensure_quantity_matches_type(
    milli: int,
    quantity_type: str,
    *,
    field: str = "quantity",
) -> None:
    """``INTEGER`` 物资只允许整数数量；``DECIMAL`` 最多三位小数已在解析时保证。"""
    if quantity_type == QuantityKind.INTEGER.value and milli % QUANTITY_SCALE != 0:
        raise _invalid_quantity(field, "整数物资数量必须为整数")
    if quantity_type not in {kind.value for kind in QuantityKind}:
        raise _invalid_quantity(field, "未知数量类型")


def _invalid_quantity(field: str, reason: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "请求参数校验失败",
        status_code=422,
        items=[{"field": field, "reason": reason}],
    )
