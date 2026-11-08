"""MCP 工具输入与输出 JSON Schema。

依据 docs/08-MCP服务与工具.md 8.6.2、8.6.3、8.6.5：
- 13 个工具均声明明确的 JSON Schema，输入不接受任何内部 ID；
- 输出 schema 只声明文档允许的业务字段，禁止直接复用内部 ORM/API 模型。
"""

from typing import Any

_NULLABLE_STRING: dict[str, Any] = {"type": ["string", "null"]}
_TIMESTAMP: dict[str, Any] = {"type": "string", "description": "UTC RFC 3339 时间"}
_CODE: dict[str, Any] = {"type": "string", "description": "业务编码（如 CK-00001）"}

_PAGE_PROPERTIES: dict[str, Any] = {
    "page": {"type": "integer", "minimum": 1, "default": 1, "description": "页码，从 1 开始"},
    "page_size": {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 20,
        "description": "每页条数，最大 100",
    },
}

WAREHOUSE_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": _CODE,
        "name": {"type": "string"},
        "location": _NULLABLE_STRING,
        "is_active": {"type": "boolean"},
        "created_at": _TIMESTAMP,
        "updated_at": _TIMESTAMP,
    },
    "required": ["code", "name", "location", "is_active", "created_at", "updated_at"],
    "additionalProperties": False,
}

NAMED_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": _CODE,
        "name": {"type": "string"},
        "is_active": {"type": "boolean"},
        "created_at": _TIMESTAMP,
        "updated_at": _TIMESTAMP,
    },
    "required": ["code", "name", "is_active", "created_at", "updated_at"],
    "additionalProperties": False,
}

CATEGORY_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": _CODE,
        "name": {"type": "string"},
        "level": {"type": "integer", "enum": [1, 2]},
        "parent_code": _NULLABLE_STRING,
        "is_active": {"type": "boolean"},
        "created_at": _TIMESTAMP,
        "updated_at": _TIMESTAMP,
    },
    "required": [
        "code",
        "name",
        "level",
        "parent_code",
        "is_active",
        "created_at",
        "updated_at",
    ],
    "additionalProperties": False,
}

MATERIAL_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": _CODE,
        "name": {"type": "string"},
        "unit": {"type": "string"},
        "quantity_type": {"type": "string", "enum": ["INTEGER", "DECIMAL"]},
        "category_code": _CODE,
        "category_name": {"type": "string"},
        "parent_category_code": _NULLABLE_STRING,
        "parent_category_name": _NULLABLE_STRING,
        "is_active": {"type": "boolean"},
        "created_at": _TIMESTAMP,
        "updated_at": _TIMESTAMP,
    },
    "required": [
        "code",
        "name",
        "unit",
        "quantity_type",
        "category_code",
        "category_name",
        "parent_category_code",
        "parent_category_name",
        "is_active",
        "created_at",
        "updated_at",
    ],
    "additionalProperties": False,
}

PROJECT_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {"project_code": _CODE, "project_name": {"type": "string"}},
    "required": ["project_code", "project_name"],
    "additionalProperties": False,
}

DOCUMENT_LINE_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "line_no": {"type": "integer", "minimum": 1},
        "material_code": _CODE,
        "material_name": {"type": "string"},
        "unit": {"type": "string"},
        "quantity_type": {"type": "string", "enum": ["INTEGER", "DECIMAL"]},
        "category_name": {"type": "string"},
        "parent_category_name": _NULLABLE_STRING,
        "manufacturer_code": _NULLABLE_STRING,
        "manufacturer_name": _NULLABLE_STRING,
        "project_code": _NULLABLE_STRING,
        "project_name": _NULLABLE_STRING,
        "quantity": {"type": "string", "description": "不带科学计数法的十进制字符串"},
        "remark": _NULLABLE_STRING,
    },
    "required": [
        "line_no",
        "material_code",
        "material_name",
        "unit",
        "quantity_type",
        "category_name",
        "parent_category_name",
        "manufacturer_code",
        "manufacturer_name",
        "project_code",
        "project_name",
        "quantity",
        "remark",
    ],
    "additionalProperties": False,
}

DOCUMENT_SUMMARY_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_no": {"type": "string", "description": "系统生成的单号"},
        "doc_type": {"type": "string", "enum": ["INBOUND", "OUTBOUND"]},
        "status": {"type": "string", "enum": ["DRAFT", "POSTED"]},
        "warehouse_code": _CODE,
        "warehouse_name": {"type": "string"},
        "department_code": _CODE,
        "department_name": {"type": "string"},
        "projects": {"type": "array", "items": PROJECT_OUTPUT},
        "created_by_name": {"type": "string"},
        "created_at": _TIMESTAMP,
        "deleted": {"type": "boolean"},
    },
    "required": [
        "doc_no",
        "doc_type",
        "status",
        "warehouse_code",
        "warehouse_name",
        "department_code",
        "department_name",
        "projects",
        "created_by_name",
        "created_at",
        "deleted",
    ],
    "additionalProperties": False,
}

DOCUMENT_DETAIL_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        **DOCUMENT_SUMMARY_OUTPUT["properties"],
        "submitted_at": _NULLABLE_STRING,
        "submitted_by_name": _NULLABLE_STRING,
        "deleted_at": _NULLABLE_STRING,
        "lines": {"type": "array", "items": DOCUMENT_LINE_OUTPUT},
    },
    "required": [
        *DOCUMENT_SUMMARY_OUTPUT["required"],
        "submitted_at",
        "submitted_by_name",
        "deleted_at",
        "lines",
    ],
    "additionalProperties": False,
}

DOCUMENT_CREATE_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        **DOCUMENT_DETAIL_OUTPUT["properties"],
        "deduplicated": {
            "type": "boolean",
            "description": "true 表示 300 秒内已存在内容相同的 MCP 单据，本次未新建",
        },
    },
    "required": [*DOCUMENT_DETAIL_OUTPUT["required"], "deduplicated"],
    "additionalProperties": False,
}

INVENTORY_ITEM_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "warehouse_code": _CODE,
        "warehouse_name": {"type": "string"},
        "material_code": _CODE,
        "material_name": {"type": "string"},
        "unit": {"type": "string"},
        "quantity_type": {"type": "string", "enum": ["INTEGER", "DECIMAL"]},
        "balance": {"type": "string", "description": "不带科学计数法的十进制字符串"},
    },
    "required": [
        "warehouse_code",
        "warehouse_name",
        "material_code",
        "material_name",
        "unit",
        "quantity_type",
        "balance",
    ],
    "additionalProperties": False,
}


def list_or_single_output(
    item_schema: dict[str, Any], single_schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """查询工具可能返回分页列表，也可能按业务编码返回单条完整对象。

    MCP 要求 ``outputSchema`` 是顶层 ``type=object`` 的 schema，因此把单条字段与
    分页字段声明在同一个对象 schema 中；两种结果形态都能通过校验，且仍禁止任何
    未声明的内部字段。
    """
    single = single_schema or item_schema
    properties: dict[str, Any] = dict(single.get("properties", {}))
    properties.update(
        {
            "items": {"type": "array", "items": item_schema},
            "page": {"type": "integer"},
            "page_size": {"type": "integer"},
            "total": {"type": "integer"},
        }
    )
    return {"type": "object", "properties": properties, "additionalProperties": False}


def list_output(item_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": item_schema},
            "page": {"type": "integer"},
            "page_size": {"type": "integer"},
            "total": {"type": "integer"},
        },
        "required": ["items", "page", "page_size", "total"],
        "additionalProperties": False,
    }


def _code_filter(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


INVENTORY_LIST_OUTPUT: dict[str, Any] = list_output(INVENTORY_ITEM_OUTPUT)


WAREHOUSE_LIST_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": _code_filter("按仓库编码精确定位单条记录；传此参数时忽略分页与其他筛选"),
        "name": _code_filter("按仓库名称精确筛选"),
        "include_inactive": {
            "type": "boolean",
            "default": False,
            "description": "为 true 时同时返回已停用记录",
        },
        **_PAGE_PROPERTIES,
    },
    "additionalProperties": False,
}

NAMED_LIST_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": _code_filter("按业务编码精确定位单条记录；传此参数时忽略分页与其他筛选"),
        "name": {"type": "string", "description": "按名称精确筛选"},
        "include_inactive": {
            "type": "boolean",
            "default": False,
            "description": "为 true 时同时返回已停用记录",
        },
        **_PAGE_PROPERTIES,
    },
    "additionalProperties": False,
}

CATEGORY_LIST_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": _code_filter("按分类编码精确定位单条记录；传此参数时忽略分页与其他筛选"),
        "name": {"type": "string", "description": "按分类名称精确筛选"},
        "parent_code": _code_filter("按父分类编码筛选二级分类"),
        "include_inactive": {
            "type": "boolean",
            "default": False,
            "description": "为 true 时同时返回已停用记录",
        },
        **_PAGE_PROPERTIES,
    },
    "additionalProperties": False,
}

MATERIAL_LIST_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": _code_filter("按物资编码精确定位单条记录；传此参数时忽略分页与其他筛选"),
        "name": {"type": "string", "description": "按物资名称精确筛选"},
        "category_code": _code_filter("按物资直接所属分类编码筛选"),
        "include_inactive": {
            "type": "boolean",
            "default": False,
            "description": "为 true 时同时返回已停用记录",
        },
        **_PAGE_PROPERTIES,
    },
    "additionalProperties": False,
}

DOCUMENT_LIST_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_no": {
            "type": "string",
            "description": "按单号精确定位单条单据；传此参数时忽略分页与其他筛选",
        },
        "doc_type": {"type": "string", "enum": ["INBOUND", "OUTBOUND"]},
        "status": {"type": "string", "enum": ["DRAFT", "POSTED"]},
        "warehouse_code": _code_filter("按单据头仓库编码筛选"),
        "department_code": _code_filter("按单据头部门编码筛选"),
        "project_code": _code_filter("按明细项目编码筛选，任一行匹配即命中"),
        "manufacturer_code": _code_filter("按入库明细厂家编码筛选，仅匹配入库单"),
        "start_date": {"type": "string", "format": "date", "description": "创建日期下限（含）"},
        "end_date": {"type": "string", "format": "date", "description": "创建日期上限（含）"},
        "deleted": {
            "type": "boolean",
            "description": "true 只返回已软删除单据，false 只返回未删除单据，省略返回全部",
        },
        **_PAGE_PROPERTIES,
    },
    "additionalProperties": False,
}

INVENTORY_LIST_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "warehouse_code": _code_filter("按仓库编码精确筛选；省略时返回全部仓库"),
        "material_code": _code_filter("按物资编码精确筛选；省略时返回全部物资"),
        "include_zero": {
            "type": "boolean",
            "default": False,
            "description": "为 true 时补齐零余额的仓库与物资组合",
        },
        **_PAGE_PROPERTIES,
    },
    "additionalProperties": False,
}

WAREHOUSE_CREATE_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "location": {"type": ["string", "null"], "maxLength": 200},
    },
    "required": ["name"],
    "additionalProperties": False,
}

DEPARTMENT_CREATE_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 200}},
    "required": ["name"],
    "additionalProperties": False,
}

CATEGORY_CREATE_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "parent_code": _code_filter("父分类编码；省略时创建一级分类"),
    },
    "required": ["name"],
    "additionalProperties": False,
}

MATERIAL_CREATE_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category_code": _code_filter("物资直接所属的一级或二级分类编码"),
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "unit": {"type": "string", "minLength": 1, "maxLength": 50},
        "quantity_type": {"type": "string", "enum": ["INTEGER", "DECIMAL"]},
    },
    "required": ["category_code", "name", "unit", "quantity_type"],
    "additionalProperties": False,
}

DOCUMENT_CREATE_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": ["INBOUND", "OUTBOUND"]},
        "submit": {
            "type": "boolean",
            "default": False,
            "description": "false 只创建草稿；true 在同一写事务内创建并提交生效",
        },
        "warehouse_code": _code_filter("启用仓库编码"),
        "department_code": _code_filter("启用部门编码"),
        "lines": {
            "type": "array",
            "minItems": 1,
            "maxItems": 2000,
            "items": {
                "type": "object",
                "properties": {
                    "material_code": _code_filter("物资编码"),
                    "quantity": {
                        "type": "string",
                        "description": "十进制字符串，最多三位小数，数量大于零",
                    },
                    "project_code": _code_filter("启用项目编码；可空，同一单据各行可不同"),
                    "manufacturer_code": _code_filter("启用厂家编码；入库明细必填，出库不得提供"),
                    "remark": {
                        "type": ["string", "null"],
                        "maxLength": 500,
                        "description": "逐行备注，最多 500 字符",
                    },
                },
                "required": ["material_code", "quantity"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["doc_type", "warehouse_code", "department_code", "lines"],
    "additionalProperties": False,
}
