"""XLSX 模板布局、版本与安全限制。

依据 docs/07-XLSX模板、快速录入与导出.md 7.8.1、7.8.3：
- “录入数据”第 1 行为单据标题，第 2 行为仓库、管理部门表头区，
  第 3 行为明细列标题，第 4 行起为明细；
- 入库列含厂家，出库无厂家列，备注均为最后一列；
- 模板填写区除数量、备注外提供下拉选项，下拉来源写入隐藏的“下拉选项”表；
- 模板不含责任栏，单张单据导出保留 4 个空姓名责任栏；
- 单张单据导出在数量列后增加单位列，并单独适配横向 A4 打印；模板不含单位列；
- 导出的管理部门标签/值：入库 G2/H2、出库 F2/G2；批准人责任栏位于 G 列；
- 入库/出库模板版本分别为 ``INBOUND_V1`` / ``OUTBOUND_V1``；
- 上传限制 5 MiB、2,000 非空数据行，并校验解压尺寸与工作簿结构。
"""

from app.modules.documents.models import DocType

SHEET_DATA = "录入数据"
SHEET_HELP = "填写说明"
SHEET_INFO = "单据信息"
SHEET_OPTIONS = "下拉选项"

TEMPLATE_IDENTIFIER_LABEL = "模板标识"
TEMPLATE_IDENTIFIER = "ONLY-STOCK"
TEMPLATE_VERSION_LABEL = "template_version"

TEMPLATE_VERSIONS: dict[str, str] = {
    DocType.INBOUND.value: "INBOUND_V1",
    DocType.OUTBOUND.value: "OUTBOUND_V1",
}

# 标题行只写单据类型；导出时在标题中追加正式单号。
DOCUMENT_TITLE_LABELS: dict[str, str] = {
    DocType.INBOUND.value: "入库单",
    DocType.OUTBOUND.value: "出库单",
}

HEADER_WAREHOUSE_LABEL = "仓库名称"
HEADER_DEPARTMENT_LABEL = "管理部门名称"
HEADER_ROW = 2
HEADER_WAREHOUSE_LABEL_COLUMN = 1
HEADER_WAREHOUSE_VALUE_COLUMN = 2
HEADER_DEPARTMENT_LABEL_COLUMN = 5
HEADER_DEPARTMENT_VALUE_COLUMN = 6
DOCUMENT_TITLE_ROW = 1
COLUMN_TITLE_ROW = 3
FIRST_DATA_ROW = 4

PROJECT_COLUMN = "项目名称"
LEVEL1_COLUMN = "一级分类名称"
LEVEL2_COLUMN = "二级分类名称"
MATERIAL_COLUMN = "物资名称"
QUANTITY_COLUMN = "数量"
UNIT_COLUMN = "单位"
MANUFACTURER_COLUMN = "厂家名称"
REMARK_COLUMN = "备注"

INBOUND_COLUMNS: tuple[str, ...] = (
    LEVEL1_COLUMN,
    LEVEL2_COLUMN,
    MATERIAL_COLUMN,
    QUANTITY_COLUMN,
    MANUFACTURER_COLUMN,
    PROJECT_COLUMN,
    REMARK_COLUMN,
)
OUTBOUND_COLUMNS: tuple[str, ...] = (
    LEVEL1_COLUMN,
    LEVEL2_COLUMN,
    MATERIAL_COLUMN,
    QUANTITY_COLUMN,
    PROJECT_COLUMN,
    REMARK_COLUMN,
)

COLUMN_TITLES: dict[str, tuple[str, ...]] = {
    DocType.INBOUND.value: INBOUND_COLUMNS,
    DocType.OUTBOUND.value: OUTBOUND_COLUMNS,
}

# 单张单据导出在数量列后增加单位列；模板列仍使用 COLUMN_TITLES。
EXPORT_INBOUND_COLUMNS: tuple[str, ...] = (
    LEVEL1_COLUMN,
    LEVEL2_COLUMN,
    MATERIAL_COLUMN,
    QUANTITY_COLUMN,
    UNIT_COLUMN,
    MANUFACTURER_COLUMN,
    PROJECT_COLUMN,
    REMARK_COLUMN,
)
EXPORT_OUTBOUND_COLUMNS: tuple[str, ...] = (
    LEVEL1_COLUMN,
    LEVEL2_COLUMN,
    MATERIAL_COLUMN,
    QUANTITY_COLUMN,
    UNIT_COLUMN,
    PROJECT_COLUMN,
    REMARK_COLUMN,
)

EXPORT_COLUMN_TITLES: dict[str, tuple[str, ...]] = {
    DocType.INBOUND.value: EXPORT_INBOUND_COLUMNS,
    DocType.OUTBOUND.value: EXPORT_OUTBOUND_COLUMNS,
}

# 导出文件的表头区管理部门标签/值列：入库 G2/H2，出库 F2/G2。
EXPORT_HEADER_DEPARTMENT_COLUMNS: dict[str, tuple[int, int]] = {
    DocType.INBOUND.value: (7, 8),
    DocType.OUTBOUND.value: (6, 7),
}

# 导出文件保留的责任栏标签及列位置；模板不写责任栏，批准人位于 G 列。
EXPORT_RESPONSIBLE_LABELS: dict[str, tuple[tuple[int, str], ...]] = {
    DocType.INBOUND.value: (
        (1, "送货人："),
        (3, "接收人："),
        (5, "审核人："),
        (7, "批准人："),
    ),
    DocType.OUTBOUND.value: (
        (1, "领用人："),
        (3, "出库人："),
        (5, "审核人："),
        (7, "批准人："),
    ),
}

# 兼容此前导出文件：批准人位于 F 列。
LEGACY_RESPONSIBLE_LABELS: dict[str, tuple[tuple[int, str], ...]] = {
    DocType.INBOUND.value: (
        (1, "送货人："),
        (3, "接收人："),
        (5, "审核人："),
        (6, "批准人："),
    ),
    DocType.OUTBOUND.value: (
        (1, "领用人："),
        (3, "出库人："),
        (5, "审核人："),
        (6, "批准人："),
    ),
}

# 下拉字段的稳定英文后缀，用于定义名称。
OPTION_FIELDS: dict[str, str] = {
    HEADER_WAREHOUSE_LABEL: "WAREHOUSE",
    HEADER_DEPARTMENT_LABEL: "DEPARTMENT",
    PROJECT_COLUMN: "PROJECT",
    LEVEL1_COLUMN: "LEVEL1",
    LEVEL2_COLUMN: "LEVEL2",
    MATERIAL_COLUMN: "MATERIAL",
    MANUFACTURER_COLUMN: "MANUFACTURER",
}

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_DATA_ROWS = 2_000
MAX_DATA_ROW = FIRST_DATA_ROW + MAX_DATA_ROWS - 1

# 工作表 dimension 可能被恶意放大；只扫描允许范围内的数据行。
MAX_SHEET_SCAN_ROW = MAX_DATA_ROW + 100

MAX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024
MAX_ZIP_ENTRIES = 2_000
MAX_COMPRESSION_RATIO = 200
MIN_RATIO_CHECK_BYTES = 1024 * 1024
MAX_RAW_NUMERIC_CELLS = 100_000
MAX_RELATIONSHIP_BYTES = 1024 * 1024
MAX_METADATA_ROWS = 30

ALLOWED_SHEETS = frozenset({SHEET_DATA, SHEET_HELP, SHEET_INFO, SHEET_OPTIONS})

PARTIAL_NOTICE = "部分明细未填入，请核对后再保存或提交"
SUCCESS_NOTICE = "全部明细已填入，可继续编辑后保存或提交"
