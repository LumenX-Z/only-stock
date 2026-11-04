# only-stock·HTTP API 通用约定与健康检查

> 文档版本：1.0 ｜ 文档状态：现行有效
> 所属系统：only-stock（单一公司内网；Python 全栈、单一 SQLite、Docker Compose）
> 本文职责：规定 HTTP API 通用约定、错误模型、权限简写和健康检查。
> 导航：[文档索引](00-文档索引与阅读指南.md) ｜ [术语与总体约束](01-项目概览与范围.md#14-关键术语)

---

## 8. 接口设计

> 8.3–8.4 见[《账户、基础资料与系统维护 API》](10-账户、基础资料与系统维护API.md)，8.5 见[《单据与库存业务 API》](11-单据与库存业务API.md)，8.6 见[《MCP 服务与工具》](08-MCP服务与工具.md)。

### 8.1 通用约定

- 基础路径：`/api/v1`。
- 页面路由与 API 路由分离，但调用同一服务层和权限组件。
- 普通页面和 `/api/v1` 认证使用会话 Cookie；除登录和健康检查外均需认证。`/mcp` 使用 API Key Bearer，规则见[《认证、会话与凭证安全》5.5](04-认证、会话与凭证安全.md#55-api-key-与-mcp-认证安全)和[《MCP 服务与工具》8.6](08-MCP服务与工具.md#86-mcp-http-与工具接口)，不属于 `/api/v1`。
- 所有会话认证的写 API 校验 CSRF；修改草稿使用 `If-Match` 或请求体中的 `version`。API Key Bearer 请求不校验 CSRF，但执行 Origin/Host 等 MCP 安全检查。
- 创建单据的 Web 页面与 `/api/v1` 必须传客户端 `request_id`，重试使用相同值；MCP `document_create` 不接受该参数，由服务端生成并按[《MCP 服务与工具》7.9 第 6 条](08-MCP服务与工具.md#79-mcp-原子创建并提交)去重。无副作用的 XLSX 解析不使用 `request_id`，也不提供跨请求结果去重。
- `request_id` 的语义按入口固定，不得在同一请求内混用：Web 页面与 `/api/v1` 创建单据使用客户端 UUID 作为幂等键，重试保持同一值，错误和日志沿用该值；其他 `/api/v1` 请求以及全部 MCP 请求由服务端为每次请求生成，其中 MCP 建单的去重不依赖该值（见[《MCP 服务与工具》7.9](08-MCP服务与工具.md#79-mcp-原子创建并提交)、[8.6.3](08-MCP服务与工具.md#863-查询与新增参数契约)）。
- `/api/v1` 路径与响应中的 ID 使用 UUID 字符串；MCP 工具不使用内部 ID，规则见[《MCP 服务与工具》8.6.5](08-MCP服务与工具.md#865-输出字段)。时间响应使用 UTC RFC 3339；页面显示为 `Asia/Shanghai`。
- 数量、可用量均为十进制字符串，禁止科学计数法。
- 列表参数统一为 `page`、`page_size`、`sort`、`order`：`page` 默认 `1`，`page_size` 默认 `20`、最大 `100`；`sort` 只接受下表为该资源声明的白名单字段，缺省使用默认字段；`order` 只接受 `asc` 或 `desc`，缺省使用默认方向。未知 `sort` 返回 422 `VALIDATION_ERROR`，`details.resource` 标识资源，`details.items` 至少包含 `field`、`value` 与 `allowed`；非法 `order` 同样返回 422 `VALIDATION_ERROR`，`details.items` 标明 `field` 与原因。
- 列表响应统一为 `items`、`page`、`page_size`、`total`；排序必须附带唯一决胜字段，保证翻页结果稳定。
- 成功创建返回 201；查询和修改返回 200；无内容删除返回 204；失败不返回伪成功。
- OpenAPI 必须可生成并与实现一致：每个 `/api/v1` 与 `/auth` 操作声明统一错误响应组件 `ErrorResponse`，会话认证声明为 `sessionCookie` 安全方案，写操作声明必需请求头 `X-CSRF-Token`，每个响应声明 `X-Request-ID` 头。

列表排序白名单：

| 接口 | 白名单 `sort` | 默认 `sort` | 默认 `order` |
| --- | --- | --- | --- |
| `GET /api/v1/users` | `login_name`、`display_name`、`role`、`status`、`created_at`、`updated_at` | `created_at` | `asc` |
| `GET /api/v1/api-keys` | `name`、`created_at`、`last_used_at`、`revoked_at` | `created_at` | `desc` |
| `GET /api/v1/warehouses`、`/departments`、`/projects`、`/manufacturers` | `code`、`name`、`created_at`、`updated_at` | `code` | `asc` |
| `GET /api/v1/categories` | `code`、`name`、`created_at`、`updated_at` | `code` | `asc` |
| `GET /api/v1/materials` | `code`、`name`、`created_at`、`updated_at` | `code` | `asc` |
| `GET /api/v1/documents` | `created_at`、`doc_no`、`submitted_at`、`status`、`doc_type` | `created_at` | `desc` |
| `GET /api/v1/inventory` | `warehouse_code`、`material_code`、`material_name`、`balance` | `warehouse_code` | `asc` |
| `GET /api/v1/inventory/transactions` | `submitted_at`、`doc_no`、`line_no`、`material_code`、`quantity` | `submitted_at` | `desc` |
| `GET /api/v1/audit-logs` | `created_at`、`action`、`entity_type`、`source` | `created_at` | `desc` |

统一错误结构：

```json
{
  "error": {
    "code": "INSUFFICIENT_STOCK",
    "message": "库存不足，出库单未生效",
    "request_id": "4f8a71c6-07d4-4b6f-a2f4-d2d84eaae3f2",
    "details": {
      "resource": "material",
      "items": [
        {
          "code": "WZ-00001",
          "available": "2.000",
          "requested": "8.000"
        }
      ]
    }
  }
}
```

`details` 统一为对象，至少包含 `resource`（无关联资源时为 `null`）和 `items` 数组；每个条目可带 `code`、名称、数量、行号等错误码专属字段，禁止直接把数组作为 `details`。调用方应按对象读取 `details.resource` 和 `details.items`，不得依赖未定义的顶层数组结构。

主要状态码：400 报文错误，401 未认证，403 无权限或账户被禁用，404 对象不存在，409 状态、版本、库存、名称重复、分类删除/停用、项目停用、负库存、余额超限、重复软删除或写锁冲突，422 字段校验失败，429 登录限流，503 系统未就绪。库存不足返回 `INSUFFICIENT_STOCK`；软删除导致余额小于零返回 `NEGATIVE_STOCK`；余额超过上限返回 `BALANCE_LIMIT_EXCEEDED`；重复软删除返回 `DOCUMENT_ALREADY_DELETED`；Web 与 `/api/v1` 创建单据时同一 `request_id` 对应不同请求内容返回 409 `REQUEST_ID_CONFLICT`（MCP 不接收该键，不产生此错误码，见[《MCP 服务与工具》7.9 第 6 条](08-MCP服务与工具.md#79-mcp-原子创建并提交)）。名称重复统一返回 `NAME_CONFLICT`，以 `details.resource` 区分 `warehouse`、`department`、`project`、`manufacturer`、`category`、`material`，并给出冲突记录 ID、名称、启用状态及分类的层级范围；MCP 工具错误的 `details` 不返回内部 ID，冲突记录改用 `code`/`doc_no` 与名称标识。分类和物资不再分别使用独立的名称冲突码，统一并入 `NAME_CONFLICT`。分类因子分类、物资或其他引用而不可删除时分别返回 `CATEGORY_HAS_CHILDREN`、`CATEGORY_HAS_MATERIALS`、`CATEGORY_IN_USE`；分类因存在未停用子分类或未停用物资而禁止停用时分别返回 `CATEGORY_HAS_ACTIVE_CHILDREN`、`CATEGORY_HAS_ACTIVE_MATERIALS`，`details.items` 须列出阻碍记录的名称、编码和数量。分类或物资因上级分类未启用而无法启用时返回 409 `PARENT_INACTIVE`。明细引用停用项目时返回 409 `PROJECT_INACTIVE`，`details.items` 列出行号、项目编码和名称。响应须说明所属层级及冲突原因，不得以删除码代替停用码。数据库唯一约束冲突也须转换为上述明确响应，不得返回伪成功或通用 500。

### 8.2 权限简写

- `CA`：公司管理员。
- `OP`：库存操作员。
- `CU`：任一启用用户，即 `CA` 或 `OP`。

---

### 8.7 健康检查

| 路径                  | 用途                                               | 权限                           |
| --------------------- | -------------------------------------------------- | ------------------------------ |
| `GET /health/live`  | 进程存活，不访问业务数据                           | 匿名                           |
| `GET /health/ready` | 数据库可用、迁移完成、数据目录可访问、备份调度正常 | 匿名，但不得泄露路径或敏感配置 |

数据库迁移失败时，就绪检查失败，应用不得继续提供可能损坏数据的业务请求。
