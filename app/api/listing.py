"""统一的列表分页与排序契约。

依据 docs/09-HTTP-API通用约定与健康检查.md 8.1：列表统一使用
``page``、``page_size``、``sort``、``order``；默认 ``page=1``、
``page_size=20``、最大 100；``sort`` 只接受各资源声明的白名单字段，
未知字段返回 422 ``VALIDATION_ERROR``，不得把客户端输入直接拼入 SQL。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends, Query

from app.errors import AppError


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True, slots=True)
class ListParams:
    page: int
    page_size: int
    sort: str | None
    order: SortOrder | None


def list_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=64,
            description="排序字段；只接受该列表文档声明的白名单字段",
        ),
    ] = None,
    order: Annotated[
        SortOrder | None,
        Query(description="排序方向；缺省使用该列表的默认方向"),
    ] = None,
) -> ListParams:
    """解析统一列表参数；字段白名单由各服务在查询前校验。"""
    return ListParams(page=page, page_size=page_size, sort=sort, order=order)


ListQuery = Annotated[ListParams, Depends(list_params)]


def resolve_sort(
    params: ListParams,
    *,
    fields: Mapping[str, Any],
    default: str,
    default_order: SortOrder,
    resource: str,
) -> tuple[Any, SortOrder]:
    """把 ``sort``/``order`` 解析为白名单列与方向；未知字段返回 422。"""
    if params.sort is None:
        column = fields[default]
    else:
        column = fields.get(params.sort.strip())
        if column is None:
            raise AppError(
                "VALIDATION_ERROR",
                "请求参数校验失败",
                status_code=422,
                resource=resource,
                items=[
                    {
                        "field": "sort",
                        "value": params.sort,
                        "reason": "不支持的排序字段",
                        "allowed": sorted(fields),
                    }
                ],
            )
    return column, params.order or default_order


def order_by(column: Any, order: SortOrder) -> Any:
    """按方向生成排序表达式。"""
    return column.desc() if order == SortOrder.DESC else column.asc()
