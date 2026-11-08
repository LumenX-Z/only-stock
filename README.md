# only-stock

面向小型公司/组织（单公司部署）的轻量仓库与库存管理系统：管理仓库、部门、项目、厂家、分类与物资等基础资料，登记入库单和出库单，按仓库与物资实时汇总库存并禁止负库存。

系统采用 Python 全栈、单一 SQLite 数据库和服务端渲染（Jinja2 + HTMX），通过 Docker Compose 以单个 `app` 服务部署；页面、JSON API 与 MCP 服务共用同一进程、同一端口和同一数据库。当前版本 `0.1.0`。

## AI 开发声明

本项目由 AI 辅助开发：应用代码、测试、文档与部署配置在很大程度上由 AI 生成，并由人类维护者确认需求、审查与发布。AI 生成内容可能存在错误、遗漏或安全缺陷；使用前请自行审查，在承载真实业务前完成独立的安全与业务验证。本项目按 MIT 许可“按原样”提供，不附带任何明示或暗示担保（见 [LICENSE](LICENSE)）。

## 主要用途

- 适用于规模较小的单一公司或组织：约十几人使用、全系统 20 个在线用户。
- 用于内部仓库/库存管理：基础资料维护、入库与出库登记、库存与流水查询、XLSX 快速录入、审计追踪、备份与恢复。
- 通过 MCP（Model Context Protocol）向 AI 客户端或自动化工具提供受权限控制的业务工具。
- 明确不支持：多公司/多租户、审批流、批次/序列号/保质期、库位与调拨、财务结算、HTTPS/反向代理/公网暴露、多副本高可用。完整范围见 [docs/01-项目概览与范围.md](docs/01-项目概览与范围.md)。

## 功能特性

- **账户与权限**：公司管理员、库存操作员两种固定角色；每个 HTTP 请求和每次 MCP 调用都在服务端重新校验账户、角色与凭证状态。
- **基础资料**：仓库、部门、项目、厂家、两级分类、物资；系统自动生成业务编码 `code`，不可修改、停用或删除后不复用。
- **单据与库存**：入库单（必填厂家）、出库单；草稿不影响库存，提交即生效且不可编辑；已生效单据可软删除并退出库存汇总；库存按仓库与物资由有效单据实时汇总，任何操作都不会导致负库存。
- **精确数量**：数量最多三位小数，底层以千分比整数存储，不使用浮点累计；单仓库物资余额有明确上限。
- **XLSX 快速录入**：新建页下载入库/出库模板，上传解析后只填入当前表单，不直接创建、修改或提交单据；单张单据可导出 XLSX。
- **审计与运维**：审计日志（含来源 `WEB`/`API`/`MCP`）；每日自动备份并保留最近 7 个成功备份集；仅支持停服后的离线 CLI 恢复与回滚；健康检查与迁移门禁。
- **MCP 与 API**：标准 Streamable HTTP MCP 端点与同版本 JSON API；用户自助创建 API Key，明文仅显示一次。

## 技术栈

- Python 3.13
- FastAPI、Uvicorn、Jinja2、HTMX
- SQLAlchemy 2、Alembic、SQLite（WAL、`BEGIN IMMEDIATE` 写事务）
- 官方 MCP Python SDK（Streamable HTTP）、APScheduler、openpyxl、Argon2
- 依赖与环境：uv（`pyproject.toml` + `uv.lock`）
- 测试与风格：pytest、Ruff
- 部署：Docker、Docker Compose（单服务、单 worker）

## 快速开始

### Docker Compose（推荐）

前置条件：Docker 与 Docker Compose。

```bash
# 1. 准备环境变量；.env 不得提交到仓库
cp .env.example .env
# 编辑 .env：设置 BOOTSTRAP_ADMIN_USERNAME、BOOTSTRAP_ADMIN_PASSWORD、
# MCP_ALLOWED_HOSTS（填入实际内网地址，不要允许任意 Host）、TZ 等

# 2. 构建并启动
docker compose --env-file .env up -d --build

# 3. 检查服务状态与就绪状态
docker compose ps
curl --fail --silent http://127.0.0.1:8000/health/ready
```

- 页面地址：`http://<服务器IP>:8000`；MCP 地址：`http://<服务器IP>:8000/mcp`。
- 仅当数据库没有任何用户时，首次启动会用 `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` 创建首位公司管理员；首次登录必须修改密码。
- 初始化成功后，从 `.env` 同时删除（或留空）`BOOTSTRAP_ADMIN_USERNAME` 和 `BOOTSTRAP_ADMIN_PASSWORD`，再执行 `docker compose up -d`；只设置其中一个会导致启动配置校验失败。
- 详细部署、首次启动、备份恢复与升级步骤见 [docs/13-Docker部署与运维.md](docs/13-Docker部署与运维.md)。

### 本地开发

```bash
uv python install 3.13
uv sync --locked
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：`/health/live` 与 `/health/ready`。所有 Python 命令统一通过 `uv run` 执行，不使用 pip 或手工虚拟环境。

## 使用说明

### 角色与权限

| 角色 | 权限 |
|---|---|
| 公司管理员（`COMPANY_ADMIN`） | 用户管理、六类基础资料、全部出入库单、已生效单据软删除、审计查询、手动备份 |
| 库存操作员（`OPERATOR`） | 出入库日常操作；可提交全部普通出入库草稿，只能修改或删除本人创建的草稿，只能软删除本人创建的已生效单据 |

页面按钮隐藏不是权限边界，权限始终在服务端校验。

### 基础资料与单据流程

1. 公司管理员创建用户，并维护仓库、部门、项目、厂家、分类与物资。
2. 两种角色都可新建入库/出库草稿；每张单据必填一个仓库和一个管理部门。
3. 入库单每条明细必须填写厂家，出库单不填厂家；每条明细可选关联一个启用项目，同一单据可混用不同项目。
4. 草稿保存后不影响库存；提交后立即生效、不可编辑，库存同步变化。
5. 已生效单据可软删除（记录与明细保留，但退出库存汇总）；删除或提交都不会允许任一物资余额为负。

### XLSX 快速录入与导出

- 新建入库/出库页面可下载对应模板；填写后上传解析，有效行会整体替换当前表单内容，由用户核对后手动保存或提交。
- 解析不会创建、修改或提交任何单据，也不会改变库存；一个文件对应一张单据。
- 文件最大 5 MiB、最多 2,000 个非空数据行；文件级错误整份拒绝，行级错误跳过并显示摘要。
- 单据详情页可导出单张 XLSX；草稿使用当前资料，已生效单据使用历史快照。

### JSON API

- 基础路径为 `/api/v1`，认证接口位于 `/auth`（会话 Cookie + 写操作 CSRF）；MCP 位于 `/mcp` 且使用 API Key Bearer。
- OpenAPI 契约可通过 `/openapi.json` 获取；错误统一返回 `error.code`、`error.message`、`error.request_id` 与 `error.details`。
- 分页、排序、错误码、`request_id` 语义与权限细节见 [docs/09](docs/09-HTTP-API通用约定与健康检查.md)、[docs/10](docs/10-账户、基础资料与系统维护API.md)、[docs/11](docs/11-单据与库存业务API.md)。

### MCP 接入

1. 使用任意角色登录后，在“我的 API Key”页面创建 API Key；完整明文仅在创建时显示一次，每用户最多 10 把有效 Key。
2. 在 MCP 客户端配置地址 `http://<服务器IP>:8000/mcp`，并通过客户端的安全配置注入 `Authorization: Bearer <API Key>`；不要把 Key 放入 URL、工具参数或提示词。
3. 工具清单按当前账号角色实时过滤：公司管理员 13 个工具，库存操作员 8 个工具；每次调用都会重新鉴权。
4. MCP 建单在 300 秒窗口内按创建者与规范化内容去重；工具输入输出不含内部 ID，基础资料使用 `code`、单据使用 `doc_no` 定位；`inventory_list` 提供按仓库与物资编码筛选的实时库存查询。
5. 需在 `MCP_ALLOWED_HOSTS` 中配置实际访问的内网地址，并保持 Origin/Host 校验开启。

### 备份与恢复

- 每日 `02:00` 自动执行 SQLite 在线备份；公司管理员可手动触发备份；始终保留最近 7 个成功备份集（含手动备份）。
- 备份集包含 `inventory.sqlite3`、`manifest.json` 与 `integrity-report.json`，失败备份不计入可恢复集。
- 恢复只能在停止 `app` 后通过离线 CLI 执行，且会先校验 manifest、SHA-256 与完整性：

```bash
docker compose stop app
docker compose run --rm --no-deps app python -m app.cli backup list
docker compose run --rm --no-deps app python -m app.cli restore --backup-set <backup_set_id> --yes
docker compose up -d app
curl --fail --silent http://127.0.0.1:8000/health/ready
```

回滚、恢复区与失败处理见 [docs/13](docs/13-Docker部署与运维.md) 10.7。

### 迁移

数据库结构变更统一通过 Alembic 单一迁移链，执行器为 `python -m app.cli migrate`；容器入口脚本会先执行迁移。迁移未完成时 `/health/ready` 返回 503，业务请求返回 `SYSTEM_NOT_READY`。

## 目录结构

```text
app/          应用代码（页面、API、业务模块、MCP、模板与静态资源）
alembic/      Alembic 迁移链
tests/        unit / integration / security / concurrency / e2e / recovery / performance
scripts/      发布检查 release_check.py、性能压测 perf_load.py
docs/         规范文档（唯一事实来源）与历史归档
compose.yaml  Docker Compose 编排
Dockerfile    镜像构建（uv 锁定依赖、非 root、单 worker）
```

## 安全与使用边界

- 仅设计为可信内网使用：直接暴露 HTTP，不包含 HTTPS、反向代理或多副本；不要将服务直接暴露到公网。
- 单一公司、单一 SQLite 数据库、单 worker；不得通过增加副本、独立 MCP 服务或第二数据库绕过该约束。
- `.env`、数据库与备份不得提交到仓库；初始密码不得写入文档或镜像。
- 测试使用独立临时数据库，不读写开发数据库、生产数据卷或真实备份集。
- 发现问题时，先更新 `docs/` 中对应职责文档，再修改代码与测试。

## 测试与发布检查

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python scripts/release_check.py
```

`release_check.py` 在检查镜像相关条目时需要已构建 `only-stock:0.1.0` 镜像；仅做静态检查时可加 `--skip-image`。验收条目与最近一次发布证据见 [docs/16-验收标准.md](docs/16-验收标准.md) 与 [docs/19-验收与发布记录.md](docs/19-验收与发布记录.md)。

## 文档

- 文档入口与章节索引：[docs/00-文档索引与阅读指南.md](docs/00-文档索引与阅读指南.md)
- 数据模型与约束：[docs/05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md)
- 库存与单据流程：[docs/06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md)
- 部署、备份与恢复：[docs/13-Docker部署与运维.md](docs/13-Docker部署与运维.md)
- 开发方式与命令约束：[AGENTS.md](AGENTS.md)

## 许可证

本项目采用 [MIT 许可证](LICENSE)。Copyright (c) 2026 LumenX。
