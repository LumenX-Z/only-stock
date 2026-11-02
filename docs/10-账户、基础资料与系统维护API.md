# only-stock·账户、基础资料与系统维护 API

> 文档版本：1.0 ｜ 文档状态：现行有效
> 所属系统：only-stock（单一公司内网；Python 全栈、单一 SQLite、Docker Compose）
> 本文职责：规定认证、API Key、用户、六类基础资料、审计和备份等管理面接口。
> 导航：[文档索引](00-文档索引与阅读指南.md) ｜ [术语与总体约束](01-项目概览与范围.md#14-关键术语)

---

## 8.3 认证接口

| 方法与路径                     | 用途                    | 主要输入                           | 主要输出                                             | 权限                             |
| ------------------------------ | ----------------------- | ---------------------------------- | ---------------------------------------------------- | -------------------------------- |
| `POST /auth/login`           | 登录并建立会话          | `login_name`, `password`       | 当前用户、角色；Set-Cookie                           | 匿名，限流                       |
| `POST /auth/logout`          | 注销并撤销会话          | 无                                 | 204                                                  | 已登录，CSRF                     |
| `GET /auth/me`               | 获取当前身份            | 无                                 | 用户、角色、强制改密标记、CSRF Token                 | 已登录                           |
| `POST /auth/change-password` | 修改本人密码            | `old_password`, `new_password` | 成功状态；清除本人强制改密标记，撤销其他会话和全部 API Key，轮换当前会话并返回新的 Set-Cookie | 已登录，CSRF                     |
| `GET /api-keys`              | 查询本人 API Key 元数据 | 可选是否包含已吊销                 | ID、用途名称、末四位、创建/最后使用/吊销时间；不明文 | 已登录                           |
| `POST /api-keys`             | 创建本人 API Key        | `name`                           | 完整 Key 仅此一次返回，以及 Key 元数据               | 已登录，CSRF；最多 10 把有效 Key |
| `POST /api-keys/{id}/revoke` | 吊销本人 API Key        | 路径 ID                            | 吊销状态；重复吊销幂等                               | 已登录，CSRF；仅本人             |

## 8.4 用户、基础资料与系统维护接口

以下路径均使用统一数据库，不接受公司、租户或数据库路径参数。

| 资源 | 方法与路径                           | 用途与主要输入输出                                                                                                            | 权限              |
| ---- | ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| 用户 | `GET /users`                       | 查询用户及状态                                                                                                                | CA                |
| 用户 | `POST /users`                      | 创建 CA 或 OP；输入登录名、显示名、角色、初始密码                                                                             | CA                |
| 用户 | `PATCH /users/{id}`                | 修改显示名或角色；输入 `version`；保护最后 CA                                                                               | CA                |
| 用户 | `POST /users/{id}/disable`         | 禁用用户并撤销会话和全部 API Key                                                                                              | CA                |
| 用户 | `POST /users/{id}/reset-password`  | 重置密码；输入 `new_password`；撤销会话和全部 API Key，并强制改密；响应不回显密码 | CA                |
| 仓库 | `GET/POST /warehouses`             | 查询；创建时输入名称、位置，`code` 由系统自动生成并返回；名称去首尾空白后唯一，重复返回 409 `NAME_CONFLICT`               | GET：CU；POST：CA |
| 仓库 | `DELETE /warehouses/{id}`          | 仅未引用时删除；创建后不可修改                                                                                                | CA                |
| 仓库 | `POST /warehouses/{id}/disable`    | 停用；非零库存拒绝                                                                                                            | CA                |
| 仓库 | `POST /warehouses/{id}/enable`     | 启用已停用仓库；只改状态并写审计                                                                                              | CA                |
| 分类 | `GET/POST /categories`             | 查询树；创建一级或二级分类时输入名称及可选父分类，`code` 由系统自动生成并返回；一级名称全局唯一，二级名称按所属一级分类唯一 | GET：CU；POST：CA |
| 分类 | `DELETE /categories/{id}`          | 仅无子分类、无物资且完全未引用时删除；否则返回 409；创建后不可修改                                                            | CA                |
| 分类 | `POST /categories/{id}/disable`    | 存在未停用子分类或物资时拒绝并返回 409；不级联停用；成功后只影响入库                                                          | CA                |
| 分类 | `POST /categories/{id}/enable`     | 启用已停用分类；一级父分类未启用时返回 409 `PARENT_INACTIVE`                                                                | CA                |
| 物资 | `GET/POST /materials`              | 查询；创建时输入所属一级或二级分类、名称、单位、数量类型，`code` 由系统自动生成并返回；名称在直接所属分类内唯一             | GET：CU；POST：CA |
| 物资 | `DELETE /materials/{id}`           | 仅未引用时删除；创建后不可修改                                                                                                | CA                |
| 物资 | `POST /materials/{id}/disable`     | 停用；只影响入库，出库由库存决定                                                                                              | CA                |
| 物资 | `POST /materials/{id}/enable`      | 启用已停用物资；所属分类或一级父分类未启用时返回 409 `PARENT_INACTIVE`                                                      | CA                |
| 部门 | `GET/POST /departments`            | 查询；创建时输入名称，`code` 由系统自动生成并返回；名称去首尾空白后唯一，重复返回 409 `NAME_CONFLICT`                     | GET：CU；POST：CA |
| 部门 | `DELETE /departments/{id}`         | 按引用规则删除；创建后不可修改                                                                                                | CA                |
| 部门 | `POST /departments/{id}/disable`   | 停用部门                                                                                                                      | CA                |
| 部门 | `POST /departments/{id}/enable`    | 启用已停用部门；只改状态并写审计                                                                                              | CA                |
| 项目 | `GET/POST /projects`               | 查询；创建时输入名称，`code` 由系统自动生成并返回；可筛选启用项目；名称去首尾空白后唯一，重复返回 409 `NAME_CONFLICT`     | GET：CU；POST：CA |
| 项目 | `DELETE /projects/{id}`            | 按引用规则删除；创建后不可修改                                                                                                | CA                |
| 项目 | `POST /projects/{id}/disable`      | 停用项目                                                                                                                      | CA                |
| 项目 | `POST /projects/{id}/enable`       | 启用项目                                                                                                                      | CA                |
| 厂家 | `GET/POST /manufacturers`          | 查询；创建时输入名称，`code` 由系统自动生成并返回；名称去首尾空白后唯一，重复返回 409 `NAME_CONFLICT`                     | GET：CU；POST：CA |
| 厂家 | `DELETE /manufacturers/{id}`       | 按引用规则删除；创建后不可修改                                                                                                | CA                |
| 厂家 | `POST /manufacturers/{id}/disable` | 停用厂家                                                                                                                      | CA                |
| 厂家 | `POST /manufacturers/{id}/enable`  | 启用已停用厂家；只改状态并写审计                                                                                              | CA                |
| 审计 | `GET /audit-logs`                  | 按对象、动作、时间查询审计                                                                                                    | CA                |
| 备份 | `POST /backups`                    | 立即执行完整在线备份                                                                                                          | CA；CSRF          |
| 备份 | `GET /backups`                     | 查询备份时间、状态、大小和完整性结果                                                                                          | CA                |

六类基础资料的 `POST` 请求 schema 均不包含 `code`，服务端在创建成功后返回含系统生成 `code` 的完整对象。请求中携带 `code`时返回 422；前端不得通过隐藏字段、默认占位值或客户端预生成方式绕过。创建校验仅覆盖用户实际输入的名称、分类、单位、数量类型、位置等字段，编码唯一性由服务端生成机制与数据库唯一索引保证，不要求用户处理编码重复或格式错误。

所有 `PATCH` 返回更新后的完整对象和新 `version`。所有列表支持分页；物资、单据和库存必须支持按常用编码、名称、仓库筛选。仓库、部门、项目、厂家的创建接口在名称重复时返回 409 `NAME_CONFLICT`，`details.resource` 标识资源类型；该 409 必须在同一写事务内由服务层查重与数据库唯一索引共同产生，不得先插入后回滚返回通用错误。分类停用接口在存在未停用下级资料时返回 409，`details` 列出具体阻碍项，且不得部分停用。六类启用接口均只改状态并写审计，不触发库存重算；启用已启用记录幂等成功，不重复写审计。恢复不提供业务 API，只能按[《Docker 部署与运维》10.7](13-Docker部署与运维.md#107-恢复要求)通过离线 CLI 执行。
