# only-stock·单据与库存业务 API

> 文档版本：1.0 ｜ 文档状态：现行有效
> 所属系统：only-stock（单一公司内网；Python 全栈、单一 SQLite、Docker Compose）
> 本文职责：规定单据与库存查询、创建、修改、提交、软删除及导出接口。
> 导航：[文档索引](00-文档索引与阅读指南.md) ｜ [术语与总体约束](01-项目概览与范围.md#14-关键术语)

---

## 8.5 单据与库存接口

| 方法与路径                             | 用途                                                 | 主要输入                                                 | 主要输出                                 | 权限                     |
| -------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------- | ---------------------------------------- | ------------------------ |
| `GET /documents`                     | 分页查询单据                                         | 类型、状态、仓库、部门、项目（任一行匹配）、厂家 `manufacturer_id`（仅匹配入库明细，任一行匹配）、日期、单号                 | 单据摘要及软删除状态                     | CU                       |
| `POST /documents`                    | 创建入库或出库草稿                                   | `request_id`, `doc_type`, 仓库、部门、明细（各行可选 `project_id`、`remark`） | 草稿和版本                               | CA、OP                   |
| `GET /document-templates/{doc_type}` | 下载入库或出库 XLSX 模板                             | `INBOUND/OUTBOUND`                                     | 对应版本的 XLSX                          | CU                       |
| `POST /document-entry/parse`         | 解析 XLSX 并返回新建页表单数据，不创建或修改业务数据 | `multipart`：`doc_type`, `file`                    | 单据头（表头区）及有效明细、行数统计、行级错误摘要 | CA、OP；CSRF             |
| `GET /documents/{id}`                | 查询单据及全部明细                                   | 路径 ID                                                  | 单据、明细、快照（含项目/厂家）、软删除状态        | CU                       |
| `GET /documents/{id}/export`         | 导出单张单据详情                                     | 路径 ID                                                  | 含录入数据、填写说明和单据信息的 XLSX    | CU                       |
| `PATCH /documents/{id}`              | 修改普通草稿                                         | `version` 及可编辑字段                                 | 更新后的草稿                             | CA：全部；OP：仅本人创建 |
| `DELETE /documents/{id}`             | 删除普通草稿                                         | `version`                                              | 204                                      | CA：全部；OP：仅本人创建 |
| `POST /documents/{id}/submit`        | 提交任意普通草稿并立即计入库存                       | `version`                                              | 已生效单据、生效时间                     | CU                       |
| `POST /documents/{id}/delete`        | 软删除已生效出入库单并退出库存汇总                   | 路径 ID                                                  | 软删除状态                               | CA：全部；OP：仅本人创建 |
| `GET /inventory`                     | 查询实时库存视图                                     | 仓库、物资、编码/名称、是否含零库存、分页                | 仓库、物资、单位、数量类型、可用量       | CU                       |
| `GET /inventory/transactions`        | 查询有效单据流水                                     | 仓库、物资、项目、部门、日期、分页                       | 方向、数量、单号、生效时间               | CU                       |

单据列表与详情中的项目按明细返回：明细包含 `project_id` 与 `project_name`（快照），列表摘要以去重项目列表 `projects` 表达（元素含 `project_id` 与 `project_name`），不再返回单据头单值项目。

系统不提供 XLSX 直接创建或修改单据、解析批次查询、解析结果查询或逐行错误报告接口。`POST /document-entry/parse` 成功返回 200，文件结构或表头区等文件级错误返回 422，未认证、无权限和 CSRF 失败仍按通用约定处理。其响应不包含单据 ID、批次 ID、文件哈希、错误报告 URL 或可用于绕过普通单据校验的标记。

创建入库单示例：

```json
{
  "request_id": "77d37c1e-bf4c-4a6a-9504-66bd2da7a354",
  "doc_type": "INBOUND",
  "warehouse_id": "2c6e09be-1a64-4e8f-97fd-8ec64f28f529",
  "department_id": "f835cefd-e08a-4f62-a65f-fcb59157e571",
  "lines": [
    {
      "material_id": "edeb31d2-b99f-4bf5-b0bf-96382052b7f6",
      "quantity": "0.750",
      "manufacturer_id": "3e80d7db-f74c-447e-a8f8-67240cb43139",
      "project_id": "c74bb5e0-e992-408d-92e8-b51ed26c6ce6",
      "remark": "采购批次 A"
    },
    {
      "material_id": "edeb31d2-b99f-4bf5-b0bf-96382052b7f6",
      "quantity": "0.500",
      "manufacturer_id": "3e80d7db-f74c-447e-a8f8-67240cb43139",
      "project_id": null,
      "remark": null
    },
    {
      "material_id": "9be9d8f8-681d-4f19-938c-f71e6a90c7f0",
      "quantity": "2",
      "manufacturer_id": "3e80d7db-f74c-447e-a8f8-67240cb43139",
      "project_id": "c74bb5e0-e992-408d-92e8-b51ed26c6ce6"
    }
  ]
}
```

`project_id` 与 `remark` 只允许出现在 `lines[]` 中，均可空；单据头出现 `project_id` 或 `remark` 返回 422。同一物资可在不同项目下拆成多行，但同一 `(material_id, project_id)` 组合不得重复（空项目视为同一取值）；备注不参与该唯一性判断。出库明细不得出现 `manufacturer_id`；出现时应返回 422，而不是静默忽略。

部分填入的解析响应示例：

```json
{
  "doc_type": "INBOUND",
  "status": "PARTIAL",
  "document": {
    "warehouse_id": "2c6e09be-1a64-4e8f-97fd-8ec64f28f529",
    "department_id": "f835cefd-e08a-4f62-a65f-fcb59157e571",
    "lines": [
      {
        "material_id": "edeb31d2-b99f-4bf5-b0bf-96382052b7f6",
        "quantity": "1.250",
        "manufacturer_id": "3e80d7db-f74c-447e-a8f8-67240cb43139",
        "project_id": null,
        "remark": "采购批次 A"
      }
    ]
  },
  "total_rows": 2,
  "filled_rows": 1,
  "skipped_rows": 1,
  "errors": [
    {
      "source_row_no": 5,
      "error_field": "物资名称",
      "error_code": "MATERIAL_NOT_FOUND",
      "message": "未找到匹配的物资名称，本行未填入"
    }
  ]
}
```

`status` 仅为本次解析响应的 `SUCCESS/PARTIAL`，不是数据库状态；文件级失败不返回 `document`。全部有效行填入时为 `SUCCESS`，存在跳过行时为 `PARTIAL`。响应只用于当前页面填单，用户保存或提交时仍须走上述普通单据接口。解析响应的单据头来自“录入数据”表头区（B1 仓库名称、B2 管理部门名称），不从明细行推导；备注只来自各行明细。`source_row_no` 为 Excel 原始行号，明细自第 4 行开始。

库存响应示例：

```json
{
  "items": [
    {
      "warehouse_id": "2c6e09be-1a64-4e8f-97fd-8ec64f28f529",
      "warehouse_name": "一号库",
      "material_id": "edeb31d2-b99f-4bf5-b0bf-96382052b7f6",
      "code": "WZ-00001",
      "material_name": "示例物资",
      "unit": "千克",
      "quantity_type": "DECIMAL",
      "balance": "1.250"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```
