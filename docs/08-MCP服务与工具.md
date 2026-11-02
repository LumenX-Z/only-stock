# only-stock·MCP 服务与工具

> 文档版本：1.0 ｜ 文档状态：现行有效
> 所属系统：only-stock（单一公司内网；Python 全栈、单一 SQLite、Docker Compose）
> 本文职责：规定 MCP 传输与认证、动态工具、参数契约、输出字段及原子建单/提交行为。
> 导航：[文档索引](00-文档索引与阅读指南.md) ｜ [术语与总体约束](01-项目概览与范围.md#14-关键术语)

---

## 7.9 MCP 原子创建并提交

1. MCP 适配层先把 `warehouse_code`、`department_code`、明细可选 `project_code`、明细 `material_code` 和入库 `manufacturer_code` 等关联引用参数分别按目标实体的 `code` 精确解析为内部 UUID，再调用普通单据服务；编码不存在返回 `RESOURCE_NOT_FOUND`，不得自动创建或按名称模糊匹配。解析出的内部 UUID 仅在业务服务内部使用，不得出现在工具结果或错误 `details`。
2. `submit=false` 使用普通创建草稿服务，返回 `DRAFT`；不读取或修改库存。
3. `submit=true` 使用专用原子应用服务，在同一 `BEGIN IMMEDIATE` 写事务内完成编码解析、草稿数据写入、提交校验、库存检查、状态置为 `POSTED`、生效字段和业务审计写入。不得先提交数据库草稿再另开事务提交生效。
4. 入库、出库仍分别完整执行[《库存计算与单据业务流程》7.3](06-库存计算与单据业务流程.md#73-入库流程)、[7.4](06-库存计算与单据业务流程.md#74-出库流程)的校验；出库必须在持有写锁后重新计算余额。任一资料、数量、厂家、项目、库存、计数器或审计步骤失败，单据头和全部明细均回滚，不留下草稿，不产生库存变化。
5. MCP 工具输入不包含 `request_id`。服务端在适配层为每次 `tools/call` 生成 UUIDv4 作为 `request_id` 并与单据同时写入，`(created_by, request_id)` 唯一约束照常生效；该值只用于审计关联、日志和错误定位，不参与去重判定，不出现在任何成功结果中。Web 页面与 `/api/v1` 的客户端幂等键语义不受本节影响。
6. MCP 建单去重按“创建者 + 规范化指纹 + 时间窗口”判定。窗口硬编码为 `MCP_DEDUP_WINDOW_SECONDS = 300`，不提供环境变量、工具参数或输出字段，调整需改代码发版。创建事务必须按以下规则计算 `request_fingerprint` 并与单据同时保存：在适配层完成 `code` 到内部引用的解析后，仅纳入统一的内部业务字段 `doc_type`、`warehouse_id`、`department_id`、`remark`、`submit` 及每行 `material_id`、`project_id`、`manufacturer_id`、`quantity_milli`，排除 `request_id` 和协议原始字段名；对象键按字典序排列；省略的可选字段、显式 `null` 与默认值归一为同一值（`submit` 默认 `false`，空项目为 `null`，可选 `remark` 的省略、`null` 和空字符串统一为 `null`）；数量统一转换为 `quantity_milli` 整数后参与计算；明细按请求中的顺序保留，数组顺序参与比较；以 UTF-8 规范化 JSON 的 SHA-256 十六进制值作为指纹。判定规则：
   - 仅当 `created_source = 'MCP'` 且 `deleted_at IS NULL` 时执行窗口查询：同一 `created_by`、相同 `request_fingerprint`、`created_at >= 当前时间 - 300 秒`，按 `created_at` 倒序取至多一条。
   - 窗口查询与建单必须在同一 `BEGIN IMMEDIATE` 写事务内完成，依靠[《库存计算与单据业务流程》7.7](06-库存计算与单据业务流程.md#77-并发与原子性)的写串行化排除并发双建，不额外引入乐观锁或分布式锁。
   - 命中时直接返回该单的 `doc_no`、`status` 和详情字段，置 `deduplicated=true`，不写单据头、明细、计数器或业务审计，不重复计算库存。
   - 未命中时正常建单并置 `deduplicated=false`。
   - 窗口外不报错，允许生成内容相同的新单。MCP 侧不产生 `REQUEST_ID_CONFLICT`，该错误码只保留给 Web 与 `/api/v1` 的客户端幂等键冲突。
   - `submit` 参与指纹，因此 `submit=false` 与 `submit=true` 即使内容相同也互不去重；修改任一参与指纹的字段（如 `remark`）即可改变指纹并绕过窗口，属预期行为，不视为绕过校验。
   - 原子直接提交失败并完整回滚时没有落库记录，同一内容可立即重试成功。已软删除的单据不参与窗口匹配，软删除后重新录入相同内容视为新建，不受窗口限制。
7. 本期不提供单独提交 MCP 已建草稿的工具；客户端可查看草稿，再通过网页/API 提交，或再次调用 `document_create(submit=true)` 创建并直接提交另一张单。两种模式互不去重，留下的草稿由调用方或用户自行删除，调用方必须避免业务重复。

---

## 8.6 MCP HTTP 与工具接口

### 8.6.1 传输、认证与协议

- MCP 地址为 `/mcp`，使用标准 Streamable HTTP，不支持旧版 HTTP+SSE，不建立独立端口或进程。实现使用官方 MCP Python SDK，并锁定协议兼容版本。
- 所有 MCP 请求使用 `Authorization: Bearer <api_key>`。未提供、格式错误、哈希不存在、Key 已吊销或用户已禁用时，按协议返回 HTTP 401；普通 Cookie 不能代替 Key。
- MCP 初始化、能力协商、`tools/list`、`tools/call`、通知和会话终止遵循 SDK 与协议规范；`MCP-Session-Id` 只是协议传输会话，不是系统登录会话，不能代替 API Key。所有请求均重新鉴权。
- 工具提供明确的中文描述、JSON Schema 输入和输出 schema、`readOnlyHint`/`destructiveHint` 等准确注解；新增工具的 `readOnlyHint=false`，直接提交工具不得标为只读。结果使用 MCP `structuredContent`，可附 JSON 文本作为兼容展示，不得仅返回自然语言。
- HTTP 状态只表达传输、认证、限流等协议层结果；已进入 `tools/call` 后的参数、权限或业务失败使用 MCP 工具错误 `isError=true`，其 `structuredContent.error` 包含 `code`、`message`、`request_id`、`details`。沿用现有错误码；未知/无权限工具分别使用 `TOOL_NOT_FOUND`、`FORBIDDEN`，不得向无权限者泄露工具实现细节。
- 定位与输出只使用业务标识：基础资料 `code`、单据 `doc_no`；工具输入一律不接受 `id`、`*_id`、内部 UUID 或 `request_id`（传入按参数校验错误拒绝），任何成功结果、列表项、详情及错误 `details` 都不包含 `id`、`*_id` 或内部 UUID。逐次请求的 `request_id` 由服务端生成，只用于错误、日志和审计关联，不作为任何工具的业务参数、定位依据或成功结果字段，规则见 8.6.5。

### 8.6.2 动态工具清单

| 工具名                | 功能                   | CA | OP | 主要输入                                                                |
| --------------------- | ---------------------- | -: | -: | ----------------------------------------------------------------------- |
| `warehouse_list`      | 查看仓库               | 是 | 是 | `code`（单条）或分页/`name` 筛选                                        |
| `warehouse_create`    | 新增仓库               | 是 | 否 | `name`, `location`；`code` 由系统自动生成                               |
| `department_list`     | 查看部门               | 是 | 是 | `code`（单条）或分页/`name` 筛选                                        |
| `department_create`   | 新增部门               | 是 | 否 | `name`；`code` 由系统自动生成                                           |
| `manufacturer_list`   | 查看厂家               | 是 | 是 | `code`（单条）或分页/`name` 筛选                                        |
| `manufacturer_create` | 新增厂家               | 是 | 否 | `name`；`code` 由系统自动生成                                           |
| `category_list`       | 查看分类及父子关系     | 是 | 是 | `code`（单条）或分页/`name`/`parent_code` 筛选                          |
| `category_create`     | 新增一级或二级分类     | 是 | 否 | `name`, 可选 `parent_code`；`code` 由系统自动生成                       |
| `material_list`       | 查看物资               | 是 | 是 | `code`（单条）或分页/`name`/`category_code` 筛选                        |
| `material_create`     | 新增物资               | 是 | 否 | `category_code`, `name`, `unit`, `quantity_type`；`code` 由系统自动生成 |
| `document_list`       | 查看入库/出库单        | 是 | 是 | `doc_no`（单条）或分页及单据筛选                                        |
| `document_create`     | 新增草稿或原子直接提交 | 是 | 是 | 见下表                                                                  |

`tools/list` 每次按当前账号权限生成，不返回无权限工具：OP 恰为六个 `*_list` 与 `document_create` 共 7 个，CA 为全部 12 个。`tools/call` 不能信任此前清单，必须再次执行相同权限判断；Key 的角色不固化。角色变化因 Key 全部撤销而需重建，新 Key 才能获得变化后的工具清单。

### 8.6.3 查询与新增参数契约

五类基础资料 `list` 与 `document_list` 共用分页参数 `page`、`page_size`（默认 `1`/`20`，最大 `100`）；五类基础资料另有 `include_inactive`，`document_list` 另有软删除筛选。单条记录以业务标识定位：`warehouse_list`、`department_list`、`manufacturer_list`、`category_list`、`material_list` 使用 `code`，`document_list` 使用 `doc_no`。传定位参数时忽略分页与其他筛选（五类基础资料同时忽略 `include_inactive`），精确返回单个完整对象（基础资料含停用记录，单据含软删除记录）；不存在返回 `RESOURCE_NOT_FOUND`。不传定位参数时返回 `items/page/page_size/total`；五类基础资料默认只列启用记录，`include_inactive=true` 时包含停用记录。分类返回自身 `code` 及父分类引用 `parent_code`，物资返回自身 `code`、直接分类及其父级信息。

各 `list` 的筛选字段为：仓库 `name`；部门 `name`；厂家 `name`；分类 `name/parent_code`；物资 `name/category_code`；单据 `doc_type/status/warehouse_code/department_code/project_code/manufacturer_code/start_date/end_date/deleted`。单据的 `project_code` 与 `manufacturer_code` 均按明细行筛选，任一行匹配即命中；`manufacturer_code` 只匹配入库明细，出库明细没有厂家。`document_list` 摘要中的项目以去重列表返回。带资源前缀的编码参数均为对目标实体 `code` 的关联引用；`parent_code`、`category_code` 只是引用参数名，不是目标表字段。关联筛选编码不存在时返回空列表。五类基础资料的列表项与按 `code` 查询返回相同业务字段；单据摘要列表返回常用字段，按 `doc_no` 查询时返回单据头、全部明细和历史快照，已软删除单保留状态与明细。所有数量仍为十进制字符串；输出只含业务编码与名称，不含 `id`、`*_id` 或内部 UUID。

基础资料新增工具不接收自身的 `code`、`id`、`*_id`、`is_active`、审计字段或客户端指定的显示名快照；`document_create` 的编码类输入只接受关联 `*_code`，不接收 `doc_no`/`id`。传入 `id`/`*_id`/UUID、`request_id` 或基础资料创建工具的 `code` 时按参数校验错误拒绝，不得静默忽略。五类基础资料创建后均为启用状态，复用普通 API 的服务端编码生成与唯一约束、名称唯一、分类层级、上级启用和物资数量类型规则。`category_create` 无 `parent_code` 为一级，有则为二级；`material_create` 的 `category_code` 可指向一级或二级分类。新增成功返回含系统生成 `code` 的完整对象，`document_create` 返回含系统生成 `doc_no` 的完整单据，均不含内部 ID；失败不自动改名、复用同名资料或创建关联资料。

`document_create` 的以下编码参数均为关联引用，不是新建实体的编码输入：各 `<resource>_code` 分别精确匹配对应资源已存在的 `code`。

| 字段                                            | 必填     | 规则                                                             |
| ----------------------------------------------- | -------- | ---------------------------------------------------------------- |
| `doc_type`                                    | 是       | `INBOUND` 或 `OUTBOUND`                                      |
| `submit`                                      | 否       | 默认 `false`；`true` 时原子创建并提交                        |
| `warehouse_code`, `department_code`         | 是       | 分别精确匹配启用仓库、部门的 `code`                            |
| `remark`                                      | 否       | 与普通单据一致                                                   |
| `lines`                                       | 是       | 至少一行；同一 `material_code` 与 `project_code` 组合不重复（项目为空视为同一取值） |
| `lines[].project_code`                        | 否       | 可空；非空时精确匹配启用项目的 `code`；同一单据各行可不同     |
| `lines[].material_code`, `lines[].quantity` | 是       | 精确匹配物资 `code`；数量为十进制字符串，执行数量类型/精度校验 |
| `lines[].manufacturer_code`                   | 入库必填 | 必须精确匹配启用厂家的 `code`；出库出现此字段即拒绝            |

`doc_no` 由服务端生成并在结果中返回，不能由客户端指定或修改。`project_code` 只允许出现在 `lines[]` 内，顶层出现按参数校验错误拒绝。

`document_create` 的工具描述必须写明去重语义，供模型正确解释输出：`deduplicated=true` 表示服务端在 300 秒内已存在内容完全相同的 MCP 单据，本次未新建，返回的是原单。描述中不得出现 `request_id` 或任何内部 ID，只描述业务可见的判定条件与结果含义。

### 8.6.4 MCP 调用示例

```json
{
  "doc_type": "INBOUND",
  "submit": true,
  "warehouse_code": "CK-00001",
  "department_code": "BM-00001",
  "remark": "采购入库",
  "lines": [
    {
      "material_code": "WZ-00001",
      "quantity": "1.250",
      "manufacturer_code": "CJ-00001",
      "project_code": "XM-00001"
    }
  ]
}
```

示例不含 `request_id`：该值由服务端为每次 `tools/call` 生成，只出现在错误对象的 `error.request_id` 中用于审计与日志关联，成功结果不回显。MCP 建单的业务去重按创建者、规范化指纹和 300 秒窗口判定，命中时结果中 `deduplicated=true` 并返回原单的 `doc_no`。

客户端连接配置指向 `http://服务器IP:8000/mcp`，完整 Key 仅配置在受控 MCP 客户端的 Authorization/Bearer 凭证中，不放入 URL、工具参数或提示词。部署页面应给出 MCP 地址和配置示例，但不得显示任何真实 Key。

### 8.6.5 输出字段

所有工具成功结果（含 `structuredContent` 及可选的兼容 JSON 文本）、列表项、单条详情及 `isError=true` 的 `error`（含 `details`）均遵守：

1. 禁止出现 `id`、`*_id` 及任何内部 UUID，包括但不限于 `warehouse_id`、`department_id`、`project_id`、`material_id`、`manufacturer_id`、`category_id`、`parent_id`、`document_id`、`deleted_by`；内部 UUID 仅用于数据库关联。
2. 跨实体引用只输出业务编码：`parent_code`、`category_code`、`parent_category_code`、`warehouse_code`、`department_code`、`project_code`、`material_code`、`manufacturer_code`；单据自身用 `doc_no`。
3. 关联编码由业务服务按内部外键解析；被单据引用的基础资料不可物理删除且 `code` 不可变，因此历史单据与已软删除单据可稳定返回编码。若关联无法解析，返回 `INTERNAL_ERROR`，不得回退输出内部 UUID 或伪造编码。
4. 时间使用 UTC RFC 3339 字符串；数量为不带科学计数法的十进制字符串；可空关联输出 `null`；布尔字段为 JSON boolean。
5. 工具输入一律不接受 `id`、`*_id`、UUID 或 `request_id` 作为实体定位、筛选或幂等参数，传入按参数校验错误拒绝。
6. `request_id` 不属于实体 ID，也不属于工具输入或成功结果：它由服务端为每次 MCP 请求生成，只出现在错误对象的 `error.request_id` 中，用于审计与日志关联。
7. `document_create` 的输出在详情字段之外增加布尔字段 `deduplicated`，其他工具不输出该字段。`deduplicated=true` 表示服务端在 300 秒窗口内命中已有单据并原样返回，本次未新建。
8. 输出 schema 只声明本节字段，不得直接复用含内部 ID 的内部 ORM/API 序列化模型。

| 工具                                        | 定位参数 | 输出字段                                                                                                                                                           |
| ------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `warehouse_list` / `warehouse_create`       | `code`   | `code`, `name`, `location`, `is_active`, `created_at`, `updated_at`                                                                                                |
| `department_list` / `department_create`     | `code`   | `code`, `name`, `is_active`, `created_at`, `updated_at`                                                                                                            |
| `manufacturer_list` / `manufacturer_create` | `code`   | `code`, `name`, `is_active`, `created_at`, `updated_at`                                                                                                            |
| `category_list` / `category_create`         | `code`   | `code`, `name`, `level`, `parent_code`, `is_active`, `created_at`, `updated_at`                                                                                    |
| `material_list` / `material_create`         | `code`   | `code`, `name`, `unit`, `quantity_type`, `category_code`, `category_name`, `parent_category_code`, `parent_category_name`, `is_active`, `created_at`, `updated_at` |

创建工具不接受定位参数；`code` 由系统生成并在输出中返回。

| 工具/场景                     | 定位参数                | 输出字段                                                                                                                                                                             |
| ----------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `document_list`（摘要列表）   | 无（分页/筛选）         | `doc_no`, `doc_type`, `status`, `warehouse_code`, `warehouse_name`, `department_code`, `department_name`, `projects`, `created_by_name`, `created_at`, `deleted` |
| `document_list`（按单号查询） | `doc_no`                | 摘要字段另加 `remark`, `submitted_at`, `submitted_by_name`, `deleted_at`, `lines`                                                                                                    |
| `document_create`             | 无（系统生成 `doc_no`） | 与按单号查询的详情相同，另加 `deduplicated`（布尔；仅此工具输出）                                                                                                                    |

`projects` 为去重后的项目列表，元素为 `{project_code, project_name}`；没有项目行时为空数组。`lines[]` 的字段为 `line_no`、`material_code`、`material_name`、`unit`、`quantity_type`、`category_name`、`parent_category_name`、`manufacturer_code`、`manufacturer_name`、`project_code`、`project_name`、`quantity`；入库单的厂家字段必填，出库单为 `null`；项目字段可空，`project_name` 为明细快照。

示例成功结果（不含任何内部 ID）：

```json
{
  "doc_no": "RK20261024-000001",
  "doc_type": "INBOUND",
  "status": "POSTED",
  "deduplicated": false,
  "warehouse_code": "CK-00001",
  "warehouse_name": "一号库",
  "department_code": "BM-00001",
  "department_name": "仓储部",
  "projects": [
    {
      "project_code": "XM-00001",
      "project_name": "示例项目"
    }
  ],
  "remark": "采购入库",
  "created_by_name": "张三",
  "created_at": "2026-10-24T02:15:00+00:00",
  "submitted_by_name": "张三",
  "submitted_at": "2026-10-24T02:15:05+00:00",
  "deleted": false,
  "deleted_at": null,
  "lines": [
    {
      "line_no": 1,
      "material_code": "WZ-00001",
      "material_name": "示例物资",
      "unit": "千克",
      "quantity_type": "DECIMAL",
      "category_name": "原材料",
      "parent_category_name": null,
      "manufacturer_code": "CJ-00001",
      "manufacturer_name": "示例厂家",
      "project_code": "XM-00001",
      "project_name": "示例项目",
      "quantity": "1.250"
    }
  ]
}
```
