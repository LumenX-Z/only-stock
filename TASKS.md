# TASKS.md

> 执行计划版本：1.0 ｜ 更新日期：2026-11-01 ｜ 发布目标：第一阶段发布 0.1.0 版本镜像
> 依据：`docs/` 中的文档是唯一事实来源；本文件只拆解实施顺序，不定义产品规则。
> 环境与命令约束：见 [AGENTS.md](AGENTS.md)。

## 使用说明

- `[ ]` 未开始，`[~]` 进行中，`[x]` 已完成，`[!]` 阻塞。
- 每个任务开始前，先阅读“依据”列出的文档章节；文档与实现冲突时，以文档为准。
- 每个任务完成前，必须完成“完成标准”并记录执行的测试命令与结果。
- 所有 Python 命令使用 `uv run`；依赖变更使用 `uv add` / `uv remove` / `uv lock`；禁止 `pip`、手工虚拟环境和其他包管理器。
- 任务按阶段推进；阶段门禁未通过，不进入下一阶段。测试贯穿全程，不等到最后补测。
- 如发现文档缺失、矛盾或不可实现，先暂停实现并更新对应文档与 [00-文档索引与阅读指南.md](docs/00-文档索引与阅读指南.md)；不得在代码中另立规则。

## 总体路径

```text
P0 准备
  ↓
P1 工程骨架、配置、日志、健康检查、测试夹具
  ↓
P2 单一数据库、账户、会话、API Key、CSRF、初始管理员
  ↓
P3 用户、权限、基础资料、统一 code、分类、数量、审计
  ↓
P4 草稿、库存视图、入库、出库、单据/库存 API
  ↓
P5 软删除、停用启用、余额边界、并发与故障
  ↓
P6 XLSX 模板、解析、填单、导出
  ↓
P7 MCP 传输、工具、原子建单、去重
  ↓
P8 全部页面、API 契约收口、端到端
  ↓
P9 Docker、备份恢复、性能、验收、发布 0.1.0 版本镜像
```

P0–P5 是主路径；P6 与 P7 可在 P4/P5 稳定后并行；P8 依赖 P3–P7；P9 最后收口，但 Docker 骨架、备份设计和测试夹具可提前准备。

## 阶段—文档映射

> T0.1 产出。产品规则仍以 `docs/` 为唯一事实来源；本表只用于把实施阶段定位到职责文档。

| 阶段 | 职责文档 | 主要章节 |
|---|---|---|
| P0 准备与基线 | [00-文档索引与阅读指南.md](docs/00-文档索引与阅读指南.md)、[18-实施顺序与完成定义.md](docs/18-实施顺序与完成定义.md)、[AGENTS.md](AGENTS.md) | 00 全篇；18 15.1；AGENTS 第 2、3、5 节 |
| P1 工程骨架与基础能力 | [02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md)、[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md)、[13-Docker部署与运维.md](docs/13-Docker部署与运维.md)、[14-安全、可靠性与性能要求.md](docs/14-安全、可靠性与性能要求.md)、[15-测试方案.md](docs/15-测试方案.md) | 3.1–3.5；8.1、8.7；10.1、10.2、10.4；11.1、11.2；12.1、12.2 |
| P2 数据库、账户、会话与 API Key | [03-角色、权限与账户生命周期.md](docs/03-角色、权限与账户生命周期.md)、[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md)、[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md)、[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md)、[10-账户、基础资料与系统维护API.md](docs/10-账户、基础资料与系统维护API.md)、[13-Docker部署与运维.md](docs/13-Docker部署与运维.md)、[15-测试方案.md](docs/15-测试方案.md)、[16-验收标准.md](docs/16-验收标准.md) | 4.3；5.1、5.2、5.4、5.5；6.1；8.1；8.3；10.4；12.1；13.1、13.5 |
| P3 用户、权限、基础资料与审计 | [03-角色、权限与账户生命周期.md](docs/03-角色、权限与账户生命周期.md)、[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md)、[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md)、[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md)、[10-账户、基础资料与系统维护API.md](docs/10-账户、基础资料与系统维护API.md)、[15-测试方案.md](docs/15-测试方案.md)、[16-验收标准.md](docs/16-验收标准.md) | 4.1–4.3；5.6；6.2、6.4、6.6–6.8；7.6、7.6.1；8.4；12.2；13.2 |
| P4 草稿、库存与出入库 | [05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md)、[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md)、[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md)、[11-单据与库存业务API.md](docs/11-单据与库存业务API.md)、[15-测试方案.md](docs/15-测试方案.md)、[16-验收标准.md](docs/16-验收标准.md) | 6.5、6.6；7.1–7.4、7.7；8.1；8.5；12.1；13.3 |
| P5 软删除、停用启用与并发边界 | [05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md)、[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md)、[14-安全、可靠性与性能要求.md](docs/14-安全、可靠性与性能要求.md)、[16-验收标准.md](docs/16-验收标准.md) | 6.6、6.7；7.5–7.7；11.2；13.3 |
| P6 XLSX 模板、快速录入与导出 | [05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md)、[07-XLSX模板、快速录入与导出.md](docs/07-XLSX模板、快速录入与导出.md)、[11-单据与库存业务API.md](docs/11-单据与库存业务API.md)、[14-安全、可靠性与性能要求.md](docs/14-安全、可靠性与性能要求.md)、[16-验收标准.md](docs/16-验收标准.md) | 6.8；7.8–7.8.5；8.5；11.1、11.3；13.7、13.8 |
| P7 MCP 服务与工具 | [02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md)、[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md)、[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md)、[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md)、[08-MCP服务与工具.md](docs/08-MCP服务与工具.md)、[15-测试方案.md](docs/15-测试方案.md)、[16-验收标准.md](docs/16-验收标准.md) | 3.1；5.5、5.6；6.2、6.4；7.3、7.4；7.9、8.6–8.6.5；12.1；13.9 |
| P8 页面、API 契约与端到端 | [04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md)、[07-XLSX模板、快速录入与导出.md](docs/07-XLSX模板、快速录入与导出.md)、[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md)、[10-账户、基础资料与系统维护API.md](docs/10-账户、基础资料与系统维护API.md)、[11-单据与库存业务API.md](docs/11-单据与库存业务API.md)、[12-页面与交互要求.md](docs/12-页面与交互要求.md)、[15-测试方案.md](docs/15-测试方案.md)、[16-验收标准.md](docs/16-验收标准.md) | 5.5；7.8.4；8.1、8.2；8.3、8.4；8.5；第 9 章；12.1；13.1、13.5 |
| P9 部署、备份恢复、性能与验收 | [09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md)、[13-Docker部署与运维.md](docs/13-Docker部署与运维.md)、[14-安全、可靠性与性能要求.md](docs/14-安全、可靠性与性能要求.md)、[16-验收标准.md](docs/16-验收标准.md)、[17-需求追踪矩阵.md](docs/17-需求追踪矩阵.md)、[18-实施顺序与完成定义.md](docs/18-实施顺序与完成定义.md) | 8.7；10.1–10.7；11.3；第 13 章；第 14 章；15.2 |

## 质量门禁与记录方式

> T0.3 产出。测试层次与必备夹具依据 [15-测试方案.md](docs/15-测试方案.md) 12.1、12.2；目录与执行约束依据 [AGENTS.md](AGENTS.md) 第 5、10 节。

### 测试目录

| 目录 | 测试层次与用途 |
|---|---|
| `tests/unit` | 单元测试：领域规则、编码、数量转换、权限矩阵、状态转换 |
| `tests/integration` | 数据库、迁移、API、MCP 集成测试 |
| `tests/security` | 认证、权限、越权、CSRF、注入、凭证脱敏 |
| `tests/concurrency` | 并发竞争、锁、原子性、最后管理员保护 |
| `tests/e2e` | 页面端到端流程 |
| `tests/recovery` | 备份恢复、故障回滚、进程终止恢复 |

### 阶段门禁命令清单（权威）

> 下列命令为各阶段门禁的唯一执行口径；各阶段末尾的“阶段门禁”代码块必须与本清单一致。任一命令失败即门禁失败，不进入下一阶段。P1 的 `uvicorn` 在前台启动，执行两条 `curl` 时保持运行，验证后停止。

| 阶段 | 门禁命令（按顺序执行） |
|---|---|
| P0 | `uv --version`<br>`uv run python --version` |
| P1 | `uv run ruff check .`<br>`uv run ruff format --check .`<br>`uv run python -c "from app.main import app"`<br>`uv run pytest tests/unit`<br>`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`（前台）<br>`curl --fail --silent http://127.0.0.1:8000/health/live`<br>`curl --fail --silent http://127.0.0.1:8000/health/ready` |
| P2 | `uv run ruff check .`<br>`uv run ruff format --check .`<br>`uv run alembic upgrade head`<br>`uv run pytest tests/unit tests/integration tests/security` |
| P3 | `uv run ruff check .`<br>`uv run ruff format --check .`<br>`uv run pytest tests/unit tests/integration`<br>`uv run pytest tests/concurrency` |
| P4 | `uv run ruff check .`<br>`uv run ruff format --check .`<br>`uv run pytest tests/unit tests/integration`<br>`uv run pytest tests/concurrency -k "submit or stock"` |
| P5 | `uv run ruff check .`<br>`uv run ruff format --check .`<br>`uv run pytest tests/concurrency`<br>`uv run pytest tests/recovery --collect-only` |
| P6 | `uv run ruff check .`<br>`uv run ruff format --check .`<br>`uv run pytest tests/unit tests/security -k "xlsx or template or export"` |
| P7 | `uv run ruff check .`<br>`uv run ruff format --check .`<br>`uv run pytest tests/security tests/integration -k "mcp or api_key"` |
| P8 | `uv run ruff check .`<br>`uv run ruff format --check .`<br>`uv run pytest tests/e2e`<br>`uv run pytest tests/security` |
| P9 | `uv run ruff check .`<br>`uv run ruff format --check .`<br>`uv run pytest`<br>`docker compose config`<br>`docker compose --env-file .env up -d --build`<br>`docker compose ps`<br>`curl --fail --silent http://127.0.0.1:8000/health/ready` |

### 执行记录格式

每完成一个任务或一个阶段门禁，在文末“执行记录”追加一行：

| 字段 | 要求 |
|---|---|
| 日期 | `YYYY-MM-DD` |
| 任务 | 任务 ID（如 `T0.2`）或阶段门禁（如 `P0 门禁`） |
| 命令/验证 | 实际执行的命令、参数与关键操作；保留 `uv run` 原文，不写“已测试”等无证据描述 |
| 结果 | `通过` / `失败` / `阻塞`，附关键输出（版本号、用例数、HTTP 状态码等） |
| 备注 | 依据文档章节、失败原因、下一步或证据位置 |

- 失败记录必须保留，不得改写为通过；修复后追加新行。
- 只有“完成标准”全部满足且存在对应记录时，才能把任务从 `[ ]` 改为 `[x]`。
- 测试目录、门禁命令和记录格式确定后，不在任务执行中临时更改；确需调整时，先更新本节并在执行记录中说明原因。

## P0 准备与基线

### 阶段目标

确认规范基线、工具链和任务边界，避免在文档未读或环境不一致时开始编码。

### 环境与仓库基线（2026-11-01）

- `uv 0.13.6`，位于 `~/.local/bin/uv`，已在当前 PATH 中可用。
- Python `3.13.18`（uv 托管，`~/.local/share/uv/python/cpython-3.13.18-linux-x86_64-gnu/bin/python3.13`），`.python-version` 固定为 `3.13`；`uv python find 3.13` 解析到 `~/.local/share/uv/python/cpython-3.13-linux-x86_64-gnu/bin/python3.13`，不创建手工虚拟环境。
- 依赖管理仅使用 uv：`uv add` / `uv remove` / `uv lock` / `uv sync --locked`；不使用 pip、`python -m venv`、virtualenv、poetry、conda。
- 仓库状态：仅 `AGENTS.md`、`TASKS.md`、`docs/`（18 份规范文档）和 `.python-version`；尚无 `app/`、`alembic/`、`tests/`、`pyproject.toml`、`uv.lock`；工作区未初始化为 Git 仓库。

- [x] **T0.1 建立规范与任务映射**
  - 依据：[00-文档索引与阅读指南.md](docs/00-文档索引与阅读指南.md)、[18-实施顺序与完成定义.md](docs/18-实施顺序与完成定义.md)。
  - 产出：确认本 TASKS 的阶段与文档章节一一对应；列出当前仓库状态。
  - 完成：能指出每个阶段对应的职责文档；确认当前仓库无应用代码。

- [x] **T0.2 安装并固定 uv / Python 3.13**
  - 依据：[AGENTS.md](AGENTS.md) 第 2、3 节。
  - 产出：可用的 `uv`、`.python-version` 或等价的 Python 版本声明。
  - 完成：`uv --version` 成功；`uv python pin 3.13` 已执行；明确不使用 pip/venv。

- [x] **T0.3 建立质量门禁与记录方式**
  - 依据：[AGENTS.md](AGENTS.md) 第 5、10 节；[15-测试方案.md](docs/15-测试方案.md)。
  - 产出：约定每阶段执行的测试目录、命令和结果记录格式。
  - 完成：阶段门禁命令清单确定；后续调整必须先更新清单并在执行记录中说明原因。

- [x] **T0.4 固化 Ruff 代码风格基线**
  - 依据：[02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md) 3.5；[AGENTS.md](AGENTS.md) 第 5 节。
  - 产出：Ruff 配置基线、只读校验命令、`# noqa` 边界和阶段门禁口径写入文档。
  - 完成：`docs/02` 3.5、文档索引、[18-实施顺序与完成定义.md](docs/18-实施顺序与完成定义.md) 15.2、`AGENTS.md` 与本文件口径一致；仓库当前无 `.py` 文件，实际校验由 T1.8 执行。

### 阶段门禁

```bash
uv --version
uv run python --version
```

## P1 工程骨架与基础能力

### 阶段目标

建立可启动、可测试、可迁移的空应用，完成配置、日志、健康检查和测试基础设施。

- [x] **T1.1 初始化 uv 项目与依赖**
  - 依据：[02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md) 3.2、3.3、3.5；[AGENTS.md](AGENTS.md) 第 3、9 节。
  - 产出：`pyproject.toml`、`uv.lock`、`.gitignore`、基础包结构。
  - 完成：`uv lock` 与 `uv sync --locked` 成功；依赖包含 FastAPI、Uvicorn、SQLAlchemy 2、Alembic、Jinja2、Argon2-cffi、APScheduler、openpyxl、python-multipart、MCP Python SDK、pytest、Ruff；无未锁定依赖。

- [x] **T1.2 建立代码目录骨架**
  - 依据：[02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md) 3.3。
  - 产出：`app/`（`main.py`、`config.py`、`db/`、`auth/`、`mcp/`、`modules/`、`services/`、`templates/`、`static/`、`cli.py`）、`alembic/`、`tests/` 六大测试目录。
  - 完成：各包可导入；目录职责与文档一致；不把业务规则放入路由。

- [x] **T1.3 配置与启动校验**
  - 依据：[13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 10.1、10.4；[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.1。
  - 产出：集中配置模块；数据目录、备份目录、会话、MCP、初始管理员相关环境变量的读取与校验。
  - 完成：缺失关键配置时启动失败并给出明确错误；日志和错误不泄露密码、Key、Cookie。

- [x] **T1.4 SQLite Engine/Session 与事务封装**
  - 依据：[02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md) 3.4；[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.1。
  - 产出：统一 Engine/Session；每连接 `PRAGMA foreign_keys=ON`、WAL、synchronous=NORMAL、busy_timeout=5000；`BEGIN IMMEDIATE` 辅助函数；SQLITE_BUSY/LOCKED 冲突映射。
  - 完成：启动自检通过；请求级 Session 可关闭；不使用 ATTACH；写事务不调用外部服务或等待用户输入。

- [x] **T1.5 请求 ID、日志与统一错误基础**
  - 依据：[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md) 8.1；[14-安全、可靠性与性能要求.md](docs/14-安全、可靠性与性能要求.md) 11.1、11.2。
  - 产出：请求 ID 中间件、结构化日志、统一错误响应骨架（`error.code/message/request_id/details`）。
  - 完成：错误 `details` 为对象且至少含 `resource`、`items`；日志不含密码、会话、完整 API Key、Authorization 头。

- [x] **T1.6 健康检查**
  - 依据：[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md) 8.7；[13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 10.2。
  - 产出：`GET /health/live`、`GET /health/ready`。
  - 完成：live 不访问业务数据；ready 检查数据库、迁移、数据目录和备份调度状态；失败时不返回敏感配置。

- [x] **T1.7 测试基础设施与夹具**
  - 依据：[15-测试方案.md](docs/15-测试方案.md) 12.1、12.2。
  - 产出：pytest 配置、临时数据库、应用测试客户端、CA/OP/禁用账户、基础资料夹具、并发测试辅助。
  - 完成：`uv run pytest` 可运行空测试集；测试不触碰开发数据库或真实备份目录。

- [x] **T1.8 配置 Ruff 并纳入质量门禁**
  - 依据：[02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md) 3.5；[AGENTS.md](AGENTS.md) 第 5 节。
  - 产出：`pyproject.toml` 写入 3.5.1 的 `[tool.ruff]` 配置；Ruff 作为开发依赖锁定；全仓库通过只读校验。
  - 完成：`uv run ruff check .` 与 `uv run ruff format --check .` 均退出 0；无未说明的 `# noqa`；规则、排除路径和格式化选项与 3.5 一致。

### 阶段门禁

```bash
uv run ruff check .
uv run ruff format --check .
uv run python -c "from app.main import app"
uv run pytest tests/unit
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
curl --fail --silent http://127.0.0.1:8000/health/live
curl --fail --silent http://127.0.0.1:8000/health/ready
```

## P2 数据库、账户、会话与 API Key

### 阶段目标

完成统一账户体系、初始管理员、登录会话、CSRF、密码生命周期和 API Key 凭证，使后续所有接口有可信身份。

- [x] **T2.1 账户/会话/API Key 表与首次迁移**
  - 依据：[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.1；[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.1、5.2。
  - 产出：`users`、`sessions`、`api_keys` 模型与首次 Alembic 迁移；UUID、UTC 时间、唯一约束、索引。
  - 完成：`uv run alembic upgrade head` 成功；外键开启；登录名规范化唯一；Key 只存哈希与末四位。

- [x] **T2.2 密码服务**
  - 依据：[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.4；[03-角色、权限与账户生命周期.md](docs/03-角色、权限与账户生命周期.md) 4.3。
  - 产出：Argon2id 哈希/校验、密码最小长度与首次改密标记。
  - 完成：密码不明文落库、不进日志；初始密码与重置密码首次登录必须修改。

- [x] **T2.3 初始公司管理员**
  - 依据：[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.2；[13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 10.4。
  - 产出：空库启动时按初始化变量创建首位公司管理员；非空库忽略初始化变量。
  - 完成：重复启动不重复创建；创建失败整体回滚；首次登录强制改密。

- [x] **T2.4 登录、会话与 CSRF**
  - 依据：[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.4；[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md) 8.1。
  - 产出：`POST /auth/login`、`POST /auth/logout`、`GET /auth/me` 的会话与 CSRF；Cookie 属性、8 小时空闲/24 小时绝对超时。
  - 完成：未认证 401；写请求无 CSRF 失败；Cookie 为 HttpOnly、SameSite=Lax、内网 HTTP 下不设 Secure；登录按 IP/登录名限流。

- [x] **T2.5 本人改密与管理员重置密码**
  - 依据：[03-角色、权限与账户生命周期.md](docs/03-角色、权限与账户生命周期.md) 4.3；[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.4。
  - 产出：`POST /auth/change-password`、`POST /users/{id}/reset-password` 的核心服务。
  - 完成：本人改密撤销其他会话和全部 Key 并轮换当前会话；重置密码撤销全部会话/Key、设置强制改密、响应不回显密码。

- [x] **T2.6 API Key 生命周期**
  - 依据：[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.5；[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.1。
  - 产出：创建/列表/吊销服务；完整 Key 仅创建时返回一次；SHA-256 哈希、末四位、用途名称；每用户最多 10 把有效 Key。
  - 完成：改密、重置、角色变化、禁用时同事务撤销全部有效 Key；重复吊销幂等；不记录完整 Key。

- [x] **T2.7 认证与 API Key 接口**
  - 依据：[10-账户、基础资料与系统维护API.md](docs/10-账户、基础资料与系统维护API.md) 8.3。
  - 产出：登录、登出、当前身份、改密、本人 API Key 的查询/创建/吊销接口。
  - 完成：符合路径、输入输出、权限和 CSRF 要求；Key 明文只在创建成功响应中出现一次。

- [x] **T2.8 认证安全测试**
  - 依据：[16-验收标准.md](docs/16-验收标准.md) 13.1、13.5；[15-测试方案.md](docs/15-测试方案.md) 12.1。
  - 产出：登录限流、会话超时、CSRF、Cookie 属性、密码哈希、Key 单次显示、Key 哈希存储测试。
  - 完成：`uv run pytest tests/security` 相关用例通过；无明文密码/完整 Key/Authorization 日志。

### 阶段门禁

```bash
uv run ruff check .
uv run ruff format --check .
uv run alembic upgrade head
uv run pytest tests/unit tests/integration tests/security
```

## P3 用户、权限、基础资料与审计

### 阶段目标

完成固定角色权限、用户管理、六类基础资料、自动编码、名称唯一、分类层级、数量转换和审计，为单据业务提供稳定主数据。

- [x] **T3.1 权限模型与服务端授权**
  - 依据：[03-角色、权限与账户生命周期.md](docs/03-角色、权限与账户生命周期.md) 4.1、4.2；[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md) 8.2。
  - 产出：`COMPANY_ADMIN` / `OPERATOR` 固定角色；`CA`、`OP`、`CU` 权限依赖；每个写接口的服务端授权。
  - 完成：操作员访问管理接口返回 403；页面按钮隐藏不作为权限边界；每次请求重新校验账户状态。

- [x] **T3.2 用户生命周期与最后管理员保护**
  - 依据：[03-角色、权限与账户生命周期.md](docs/03-角色、权限与账户生命周期.md) 4.3。
  - 产出：创建用户、改显示名/角色、禁用、重置密码；至少保留一个启用公司管理员。
  - 完成：最后一名管理员不能停用或降级；并发禁用/降级在 `BEGIN IMMEDIATE` 内重新统计并回滚；禁用立即撤销会话/Key。

- [x] **T3.3 用户管理接口**
  - 依据：[10-账户、基础资料与系统维护API.md](docs/10-账户、基础资料与系统维护API.md) 8.4 用户部分。
  - 产出：`GET /users`、`POST /users`、`PATCH /users/{id}`、禁用、重置密码接口。
  - 完成：仅 CA；分页、版本/最后管理员保护、错误码和审计符合文档。

- [x] **T3.4 六类实体统一 code 生成**
  - 依据：[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.2。
  - 产出：`code_counters`；前缀 `CK/BM/FL/WZ/XM/CJ`；固定 5 位补零；创建事务内生成。
  - 完成：编码唯一、不可修改、停用/删除不复用；序号达 99999 失败；并发创建不重复；失败与计数器整体回滚。

- [x] **T3.5 仓库、部门、项目、厂家**
  - 依据：[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.2、6.7；[10-账户、基础资料与系统维护API.md](docs/10-账户、基础资料与系统维护API.md) 8.4。
  - 产出：四类主数据的创建、列表、删除、停用、启用服务；名称规范化唯一。
  - 完成：创建后不可修改；已引用只能停用；名称重复 409 `NAME_CONFLICT`；停用记录仍占名。

- [x] **T3.6 分类层级**
  - 依据：[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.4、6.7。
  - 产出：一级/二级分类树；父级约束；一级/二级名称唯一范围；分类删除与停用条件。
  - 完成：禁止第三级；有子分类/物资时不能删除；存在未停用子分类或物资时不能停用；父级未启用不能启用。

- [x] **T3.7 物资**
  - 依据：[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.4、6.7。
  - 产出：物资创建（一级或二级分类、名称、单位、数量类型）、列表、删除/停用/启用。
  - 完成：物资名称在直接所属分类内唯一；`INTEGER/DECIMAL` 数量类型生效；创建后不可修改；停用只影响入库。

- [x] **T3.8 六类名称规范化与唯一索引**
  - 依据：[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.7、6.8。
  - 产出：去首尾空白后精确比较；服务层查重 + 数据库唯一索引；分类/物资层级唯一索引。
  - 完成：并发同名创建只有一个成功；停用记录参与查重；不折叠大小写、不删除内部空白。

- [x] **T3.9 停用、启用与删除规则**
  - 依据：[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.7；[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.6、7.6.1。
  - 产出：通用停用/启用服务；分类自底向上停用；启用上级校验；删除引用检查。
  - 完成：停用/启用只改状态并写审计，不改历史单据/快照/库存；`PARENT_INACTIVE`、`CATEGORY_HAS_ACTIVE_*` 等错误按文档返回。

- [x] **T3.10 数量转换与校验**
  - 依据：[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.6。
  - 产出：Decimal → `quantity_milli` 转换；最多三位小数；整数物资校验；`MAX_BALANCE_MILLI` 常量。
  - 完成：`1.25` 存为 `1250`；拒绝浮点、四位小数、科学计数法；余额上限不溢出。

- [x] **T3.11 审计日志**
  - 依据：[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.6。
  - 产出：只追加审计服务；记录操作者、时间、请求 ID、对象、动作、前后状态、来源 `WEB/API/MCP`。
  - 完成：登录、用户、Key、资料变更、停用/启用、提交、软删除、备份/恢复均写审计；不记录敏感凭证。

- [x] **T3.12 基础资料与用户管理 API 收口**
  - 依据：[10-账户、基础资料与系统维护API.md](docs/10-账户、基础资料与系统维护API.md) 8.4。
  - 产出：六类资料的 GET/POST/DELETE/停用/启用接口；审计查询；CA 权限；分页和错误结构。
  - 完成：请求体不含 `code`，传 `code` 返回 422；创建返回系统生成 `code`；OpenAPI 与实现一致。

- [x] **T3.13 基础资料专项测试**
  - 依据：[16-验收标准.md](docs/16-验收标准.md) 13.2；[15-测试方案.md](docs/15-测试方案.md) 12.2。
  - 产出：编码并发生成、名称并发重名、分类层级/删除/停用、物资分类、数量精度、停用启用、权限测试。
  - 完成：`uv run pytest tests/unit tests/integration tests/concurrency` 相关用例通过。

### 阶段门禁

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/unit tests/integration
uv run pytest tests/concurrency
```

## P4 草稿、库存与出入库

### 阶段目标

完成单据草稿、库存视图、入库/出库提交和单据/库存查询，使核心库存业务可用。

- [ ] **T4.1 单据表、明细与约束**
  - 依据：[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.5。
  - 产出：`stock_documents`、`stock_document_lines`、`document_counters` 模型与迁移；索引、唯一约束、触发器。
  - 完成：已生效单据/明细禁止更新；软删除字段可用；同物资同项目唯一；至少一行的约束由服务与数据库共同保证。

- [ ] **T4.2 草稿创建、修改、删除与幂等**
  - 依据：[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.2。
  - 产出：草稿服务；客户端 `request_id` 幂等；`request_fingerprint` 规范化计算；乐观锁 `version`。
  - 完成：Web/API 重放同 `request_id` 返回原单；同键不同内容 409 `REQUEST_ID_CONFLICT`；草稿不影响库存。

- [ ] **T4.3 明细校验与逐行项目**
  - 依据：[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.3、7.4；[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.5。
  - 产出：入库厂家必填、出库无厂家；项目可空且逐行；同物资同项目去重；数量/单位/快照校验。
  - 完成：单据头出现 `project_id` 返回 422；出库带厂家返回 422；停用项目 409 `PROJECT_INACTIVE` 并列行号。

- [ ] **T4.4 库存视图与查询索引**
  - 依据：[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.1。
  - 产出：`v_inventory_balance` 或等价查询；仓库+物资汇总；索引与测试查询计划。
  - 完成：草稿/软删除不计入；零余额补齐；项目/部门/厂家筛选不改变库存定义。

- [ ] **T4.5 入库提交事务**
  - 依据：[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.3；[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.6。
  - 产出：`POST /documents/{id}/submit` 的入库路径；`BEGIN IMMEDIATE`；事务内重算余额上限。
  - 完成：提交成功立即增加库存；超 `MAX_BALANCE_MILLI` 返回 `BALANCE_LIMIT_EXCEEDED` 并整单回滚；已生效不可修改。

- [ ] **T4.6 出库提交事务**
  - 依据：[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.4、7.7。
  - 产出：出库提交路径；事务内读取当前余额；按物资汇总拆行数量；负库存拒绝。
  - 完成：可用量等于出库量可提交；不足返回 `INSUFFICIENT_STOCK` 含编码/可用量/请求量；任一行失败整单回滚；并发两单不能都基于旧余额成功。

- [ ] **T4.7 单据与库存查询接口**
  - 依据：[11-单据与库存业务API.md](docs/11-单据与库存业务API.md) 8.5。
  - 产出：单据分页/详情、修改/删除/提交、库存和流水接口；软删除路径可先留桩，P5 完成；模板下载/XLSX 解析/导出由 P6 接入同一 API 契约。
  - 完成：路径、分页、筛选、权限、示例响应与文档一致；项目按明细返回、`projects` 摘要正确。

- [ ] **T4.8 单据错误与状态码**
  - 依据：[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md) 8.1。
  - 产出：库存不足、版本冲突、重复提交、项目停用、余额超限等错误映射。
  - 完成：409/422/403/401 口径正确；`details` 结构统一；不得伪成功。

- [ ] **T4.9 单据与库存测试**
  - 依据：[16-验收标准.md](docs/16-验收标准.md) 13.3；[15-测试方案.md](docs/15-测试方案.md) 12.1。
  - 产出：入库/出库、拆分项目、边界数量、库存不足、并发提交、幂等重试测试。
  - 完成：`uv run pytest tests/unit tests/integration tests/concurrency` 相关用例通过。

### 阶段门禁

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/unit tests/integration
uv run pytest tests/concurrency -k "submit or stock"
```

## P5 软删除、停用启用与并发边界

### 阶段目标

补齐已生效单据软删除、项目/资料停用启用对单据的影响，以及负库存、余额上限和故障回滚的完整边界。

- [ ] **T5.1 已生效单据软删除**
  - 依据：[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.5。
  - 产出：`POST /documents/{id}/delete`；权限（CA 全部/OP 本人）；`deleted_at`、`deleted_by`；事务内移除本单后重算余额。
  - 完成：软删除后退出库存汇总但保留头/明细/快照；重复删除 409 `DOCUMENT_ALREADY_DELETED`；导致负库存返回 `NEGATIVE_STOCK`；审计同事务。

- [ ] **T5.2 项目停用/启用对单据的约束**
  - 依据：[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.6。
  - 产出：草稿保存/提交逐行校验项目状态；停用项目拒绝并列出项目编码/名称/行号。
  - 完成：历史单据项目字段和快照不变；启用后可再次使用；列表默认只显示启用项目。

- [ ] **T5.3 基础资料停用/启用对单据的影响**
  - 依据：[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.6.1；[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.7。
  - 产出：仓库、部门、厂家、分类、物资停用/启用与单据保存/提交的联动。
  - 完成：停用资料不能用于新单/提交（分类、物资只影响入库）；出库仍按库存判定；启用只改状态并写审计。

- [ ] **T5.4 余额上限与负库存全链路**
  - 依据：[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.6；[16-验收标准.md](docs/16-验收标准.md) 13.2、13.3。
  - 产出：提交、软删除、MCP 直接提交共用的余额校验；边界用例。
  - 完成：单行/多行最大数量不溢出；超限/负库存整单回滚；不存在部分成功。

- [ ] **T5.5 故障注入与原子性**
  - 依据：[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.7；[14-安全、可靠性与性能要求.md](docs/14-安全、可靠性与性能要求.md) 11.2。
  - 产出：数据库异常、锁超时、进程终止模拟；事务回滚验证。
  - 完成：不产生半张有效单据；计数器、审计、单据头/明细一致；锁冲突返回明确错误。

- [ ] **T5.6 并发竞争专项**
  - 依据：[16-验收标准.md](docs/16-验收标准.md) 13.3 ACC-FLOW-008、009。
  - 产出：并发出库、软删除与提交竞争、并发停用/启用等测试。
  - 完成：最终库存不为负；成功结果唯一且可解释；`tests/concurrency` 全部通过。

### 阶段门禁

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/concurrency
uv run pytest tests/recovery --collect-only
```

## P6 XLSX 模板、快速录入与导出

### 阶段目标

完成入库/出库 XLSX 模板、无副作用解析、部分填入、导出和安全防护。

- [ ] **T6.1 XLSX 模板生成**
  - 依据：[07-XLSX模板、快速录入与导出.md](docs/07-XLSX模板、快速录入与导出.md) 7.8.1。
  - 产出：入库/出库模板；“录入数据”“填写说明”工作表；版本 `INBOUND_V1`/`OUTBOUND_V1`；字段顺序与说明。
  - 完成：出库模板无厂家列；项目列逐行可选；模板由服务端动态生成。

- [ ] **T6.2 只读解析与文件防护**
  - 依据：[07-XLSX模板、快速录入与导出.md](docs/07-XLSX模板、快速录入与导出.md) 7.8.1、7.8.3；[14-安全、可靠性与性能要求.md](docs/14-安全、可靠性与性能要求.md) 11.1。
  - 产出：`openpyxl` 只读解析；5 MiB/2,000 行限制；ZIP/工作表结构校验；不执行宏/公式/外部链接；普通数值从 `<v>` 原始词法转 `Decimal`。
  - 完成：超限、损坏、异常压缩被拒绝；公式行按行级错误；不使用 `cell.value` 浮点结果。

- [ ] **T6.3 名称解析**
  - 依据：[07-XLSX模板、快速录入与导出.md](docs/07-XLSX模板、快速录入与导出.md) 7.8.2；[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.8。
  - 产出：仓库/部门/项目/厂家/分类/物资批量名称解析；父子分类消歧；启停状态校验。
  - 完成：名称仅去首尾空白精确匹配；找不到/停用/重复定位产生行级错误；不自动创建或改名。

- [ ] **T6.4 文件级/行级错误与部分填入**
  - 依据：[07-XLSX模板、快速录入与导出.md](docs/07-XLSX模板、快速录入与导出.md) 7.8.3、7.8.4。
  - 产出：文件级失败整份拒绝；行级错误跳过；`SUCCESS/PARTIAL` 响应、行数统计、错误摘要。
  - 完成：部分填入显著提示；错误含 Excel 行号/字段/代码/原因；不生成错误报告、不持久化解析状态。

- [ ] **T6.5 无副作用解析接口**
  - 依据：[07-XLSX模板、快速录入与导出.md](docs/07-XLSX模板、快速录入与导出.md) 7.8.4；[11-单据与库存业务API.md](docs/11-单据与库存业务API.md) 8.5。
  - 产出：`POST /document-entry/parse`；仅在新建页使用；CSRF/权限；不开启写事务、不写单据/审计/解析表。
  - 完成：重复解析不改变数据；响应不含内部 ID/批次/文件哈希；保存/提交仍走普通单据接口。

- [ ] **T6.6 单张单据导出**
  - 依据：[07-XLSX模板、快速录入与导出.md](docs/07-XLSX模板、快速录入与导出.md) 7.8.5。
  - 产出：`GET /documents/{id}/export`；“录入数据/填写说明/单据信息”；草稿当前资料、生效单快照；文件名安全编码。
  - 完成：导出不改变状态；超过 2,000 行提示不可快速录入；用户文本不以公式解释。

- [ ] **T6.7 模板与导出安全测试**
  - 依据：[16-验收标准.md](docs/16-验收标准.md) 13.8。
  - 产出：5 MiB/2,001 行、损坏 ZIP、公式/外部链接、压缩炸弹、公式注入、极值精度用例。
  - 完成：`uv run pytest tests/security` 与 XLSX 专项通过；解析前后单据/审计/主数据数量不变。

- [ ] **T6.8 XLSX 性能与内存**
  - 依据：[14-安全、可靠性与性能要求.md](docs/14-安全、可靠性与性能要求.md) 11.3；[16-验收标准.md](docs/16-验收标准.md) 13.7。
  - 产出：2,000 行文件解析/导出基准；批量名称解析。
  - 完成：参考环境下单次目标 ≤10 秒，无内存溢出和逐行重复查询。

### 阶段门禁

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/unit tests/security -k "xlsx or template or export"
```

## P7 MCP 服务与工具

### 阶段目标

在同一 FastAPI 应用挂载标准 MCP Streamable HTTP，完成 12 个工具、动态权限过滤、原子建单和 300 秒去重。

- [ ] **T7.1 挂载 MCP Streamable HTTP**
  - 依据：[08-MCP服务与工具.md](docs/08-MCP服务与工具.md) 8.6.1；[02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md) 3.1。
  - 产出：官方 MCP Python SDK 挂载 `/mcp`；同应用同端口同进程；协议初始化/会话/通知。
  - 完成：标准客户端可初始化；无独立 MCP 服务/端口；无旧 HTTP+SSE。

- [ ] **T7.2 MCP 认证与传输安全**
  - 依据：[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.5；[08-MCP服务与工具.md](docs/08-MCP服务与工具.md) 8.6.1。
  - 产出：Bearer API Key 认证；Key 状态/用户状态校验；Origin/Host 白名单；Key/IP 限流；请求体/响应大小限制。
  - 完成：Cookie 不能代替 Key；无/错/吊销/禁用 Key 401；超限 429；未知 Host/Origin 拒绝；日志不记完整凭证。

- [ ] **T7.3 工具 schema 与动态清单**
  - 依据：[08-MCP服务与工具.md](docs/08-MCP服务与工具.md) 8.6.2。
  - 产出：12 个工具（仓库、部门、厂家、分类、物资、单据六类资源各 `list/create`）；输入输出 schema；`readOnlyHint`/`destructiveHint`。
  - 完成：CA 12 个、OP 7 个；每次 `tools/list` 按当前账号过滤；`tools/call` 再校验，不信任先前清单。

- [ ] **T7.4 查询工具**
  - 依据：[08-MCP服务与工具.md](docs/08-MCP服务与工具.md) 8.6.3、8.6.5。
  - 产出：`warehouse_list`、`department_list`、`manufacturer_list`、`category_list`、`material_list`、`document_list`；分页/筛选/单条定位。
  - 完成：基础资料 `code`、单据 `doc_no` 定位；默认只列启用、可含停用；单据返回明细/快照/软删除状态；输出无内部 ID。

- [ ] **T7.5 基础资料新增工具**
  - 依据：[08-MCP服务与工具.md](docs/08-MCP服务与工具.md) 8.6.3；[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md) 6.2、6.4。
  - 产出：五类 `*_create` 工具；复用普通服务层；系统生成 `code`；分类父级、物资分类/单位/数量类型校验。
  - 完成：创建工具不接受 `code`/`id`/`*_id`；CA 可用、OP 不可见且越权 `FORBIDDEN`；名称/编码唯一与普通 API 一致。

- [ ] **T7.6 `document_create` 与原子直接提交**
  - 依据：[08-MCP服务与工具.md](docs/08-MCP服务与工具.md) 7.9、8.6.3；[06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md) 7.3、7.4。
  - 产出：`submit=false` 建草稿；`submit=true` 同事务完成编码解析、写入、校验、库存、生效、审计。
  - 完成：入库厂家/出库无厂家、逐行项目、数量/库存规则完整；失败回滚不留下草稿；工具不接收 `request_id`。

- [ ] **T7.7 MCP 建单 300 秒去重**
  - 依据：[08-MCP服务与工具.md](docs/08-MCP服务与工具.md) 7.9 第 6 条。
  - 产出：规范化 `request_fingerprint`；创建者+指纹+300 秒窗口查询；命中返回原单 `deduplicated=true`。
  - 完成：窗口内不重复建单/审计/计数；窗口外可新建；不同用户隔离；`submit=false/true` 互不去重；软删除单不参与。

- [ ] **T7.8 MCP 输出、审计与错误**
  - 依据：[08-MCP服务与工具.md](docs/08-MCP服务与工具.md) 8.6.5；[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.6。
  - 产出：业务编码映射；成功/错误结构化内容；`source=MCP` 审计与 `api_key_id`。
  - 完成：任何结果/错误 `details` 不含内部 UUID；错误 `request_id` 由服务端生成；MCP 查询不逐条业务审计。

- [ ] **T7.9 MCP 协议、权限与故障测试**
  - 依据：[16-验收标准.md](docs/16-验收标准.md) 13.9；[15-测试方案.md](docs/15-测试方案.md) 12.1。
  - 产出：标准客户端初始化、12/7 工具清单、越权、分页/单条、输出字段白名单、直接提交回滚、去重、限流、Origin/Host 测试。
  - 完成：`uv run pytest tests/security tests/integration -k mcp` 相关用例通过。

### 阶段门禁

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/security tests/integration -k "mcp or api_key"
```

## P8 页面、API 契约与端到端

### 阶段目标

完成服务端渲染页面和全部交互，收口 OpenAPI 与错误契约，验证真实用户路径和权限边界。

- [ ] **T8.1 API 契约收口**
  - 依据：[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md) 8.1、8.2；[10-账户、基础资料与系统维护API.md](docs/10-账户、基础资料与系统维护API.md)、[11-单据与库存业务API.md](docs/11-单据与库存业务API.md)。
  - 产出：完整 OpenAPI；统一分页、筛选、排序白名单、错误结构、`request_id` 语义。
  - 完成：路径/输入输出/权限与文档一致；错误 `details` 统一；OpenAPI 可生成且实现一致。

- [ ] **T8.2 基础布局与导航**
  - 依据：[12-页面与交互要求.md](docs/12-页面与交互要求.md) 第 9 章。
  - 产出：登录态布局、角色可见导航、分页/错误/空状态组件；HTMX 局部交互。
  - 完成：无权限入口隐藏但后端仍校验；列表分页；错误不只显示“服务器错误”。

- [ ] **T8.3 登录、用户与基础资料页面**
  - 依据：[12-页面与交互要求.md](docs/12-页面与交互要求.md)；[10-账户、基础资料与系统维护API.md](docs/10-账户、基础资料与系统维护API.md) 8.4。
  - 产出：登录/首次改密、用户管理、仓库/部门/项目/厂家/分类/物资页面；创建表单无 `code` 输入。
  - 完成：名称冲突就地回显；分类停用显示阻碍项并自底向上操作；启用上级未启用提示并跳转；`code` 只读展示。

- [ ] **T8.4 单据、库存与软删除页面**
  - 依据：[12-页面与交互要求.md](docs/12-页面与交互要求.md)；[11-单据与库存业务API.md](docs/11-单据与库存业务API.md) 8.5。
  - 产出：单据列表/新建/编辑/详情/提交/软删除、库存与流水页面；项目逐行选择；出库可用量提示。
  - 完成：草稿标识“不计入库存”；提交二次确认；库存不足定位明细；创建成功但提交失败展示草稿及复用 `request_id`。

- [ ] **T8.5 XLSX 快速录入 UI**
  - 依据：[12-页面与交互要求.md](docs/12-页面与交互要求.md) 第 9 章；[07-XLSX模板、快速录入与导出.md](docs/07-XLSX模板、快速录入与导出.md) 7.8.4。
  - 产出：新建页模板下载/上传；非空表单确认替换；成功/部分/失败状态；错误摘要；保存/提交人工按钮。
  - 完成：解析不自动保存/提交；已有草稿编辑页无入口；部分填入显著提示；不生成错误报告。

- [ ] **T8.6 API Key 与账户安全页面**
  - 依据：[12-页面与交互要求.md](docs/12-页面与交互要求.md)；[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md) 5.5。
  - 产出：“我的 API Key”页面、创建后一次性明文展示、吊销确认、MCP 地址/配置示例。
  - 完成：只能管理本人 Key；刷新/离开不可恢复明文；提示改密/角色变化/禁用会撤销 Key；不展示真实 Key。

- [ ] **T8.7 审计与备份页面**
  - 依据：[12-页面与交互要求.md](docs/12-页面与交互要求.md)；[10-账户、基础资料与系统维护API.md](docs/10-账户、基础资料与系统维护API.md) 8.4。
  - 产出：审计查询页、手动备份与备份状态页；CA 权限。
  - 完成：操作员不可见/不可调用；备份失败明确展示，不以“已执行”代替成功。

- [ ] **T8.8 端到端与权限回归**
  - 依据：[15-测试方案.md](docs/15-测试方案.md) 12.1；[16-验收标准.md](docs/16-验收标准.md) 13.1、13.5。
  - 产出：登录→初始化→建用户→建资料→入库→出库→停用/启用→软删除→API Key 主路径；CA/OP 交叉权限。
  - 完成：`uv run pytest tests/e2e` 通过；越权路径全部被拒绝；页面关键提示符合文档。

### 阶段门禁

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/e2e
uv run pytest tests/security
```

## P9 部署、备份恢复、性能与验收

### 阶段目标

完成可重复部署、备份恢复、性能基线和全部验收证据，发布 `only-stock:0.1.0` 镜像，达到发布完成定义。

- [ ] **T9.1 Dockerfile**
  - 依据：[13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 10.3；[AGENTS.md](AGENTS.md) 第 8 节。
  - 产出：Python 3.13 精简镜像；uv 锁定依赖安装；非 root；包含迁移/备份/恢复 CLI；不写入 `.env`/数据库/备份/初始密码。
  - 完成：镜像 `only-stock:0.1.0` 可构建；无硬编码密码/API Key；依赖漏洞和镜像安全检查完成。

- [ ] **T9.2 Compose 服务与卷**
  - 依据：[13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 10.1、10.2。
  - 产出：单个 `app` 服务；端口 `8000`；`inventory_data`、`inventory_backups` 卷；健康检查；单 worker。
  - 完成：`docker compose config` 通过；服务启动、健康检查、页面/API/MCP 可访问；无反向代理/HTTPS/多副本。

- [ ] **T9.3 首次启动与配置**
  - 依据：[13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 10.4。
  - 产出：`.env` 示例/检查清单；初始管理员变量；首次登录改密；MCP 地址与允许 Host 配置。
  - 完成：空库首次启动创建管理员；重复启动不重复；初始化后移除初始密码；首次登录强制改密。

- [ ] **T9.4 每日/手动备份**
  - 依据：[13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 10.6。
  - 产出：APScheduler 每日 02:00 备份；SQLite Online Backup API；`integrity_check`；`manifest.json`；成功后原子重命名；保留最近 7 个成功集。
  - 完成：失败不计入可恢复集；手动备份与定时备份同等计数；清理失败不影响新备份；页面/API 可查看状态。

- [ ] **T9.5 离线恢复与回滚**
  - 依据：[13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 10.7。
  - 产出：`app.cli backup list`、`restore --backup-set`、`restore --rollback-from`；manifest/哈希/完整性校验；恢复前移动 `-wal`/`-shm` 到恢复区。
  - 完成：恢复前必须停 app；失败不覆盖当前库；恢复后 health/登录/库存/单据/MCP 验证；隔离环境演练记录。

- [ ] **T9.6 升级、迁移失败与就绪**
  - 依据：[13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 10.5；[09-HTTP-API通用约定与健康检查.md](docs/09-HTTP-API通用约定与健康检查.md) 8.7。
  - 产出：单迁移执行器；迁移版本/时间/结果记录；迁移失败 readiness 失败；不可逆迁移的恢复策略。
  - 完成：升级前完整备份；迁移失败停止业务写入；回滚前确认可逆性或按备份恢复。

- [ ] **T9.7 性能与容量**
  - 依据：[14-安全、可靠性与性能要求.md](docs/14-安全、可靠性与性能要求.md) 11.3；[16-验收标准.md](docs/16-验收标准.md) 13.7。
  - 产出：10 万有效明细数据集；查询计划；10 并发/5 分钟压测；2,000 行 XLSX 解析/导出基准。
  - 完成：P95 ≤2 秒、错误率 <0.1%；XLSX ≤10 秒；无全表扫描/无界内存增长；不达标时有经确认的优化或调整结论。

- [ ] **T9.8 验收与追踪**
  - 依据：[16-验收标准.md](docs/16-验收标准.md) 第 13 章；[17-需求追踪矩阵.md](docs/17-需求追踪矩阵.md)。
  - 产出：ACC-ROLE/DATA/FLOW/STORAGE/API/OPS/PERF/XLSX/MCP 全部条目执行记录；需求→章节→验收追踪核对。
  - 完成：核心条目全部通过；失败项有修复记录或明确不验收结论；证据可复现。

- [ ] **T9.9 发布检查**
  - 依据：[18-实施顺序与完成定义.md](docs/18-实施顺序与完成定义.md) 15.2。
  - 产出：发布检查清单、镜像版本（`only-stock:0.1.0`）、迁移版本、OpenAPI、部署说明、测试报告。
  - 完成：镜像无硬编码密码/Key；日志无敏感信息；初始密码已移除；OpenAPI/迁移/部署文档/镜像版本（`only-stock:0.1.0`）一致；TASKS 全部关闭。

### 阶段门禁

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker compose config
docker compose --env-file .env up -d --build
docker compose ps
curl --fail --silent http://127.0.0.1:8000/health/ready
```

## 全局完成定义

只有同时满足以下条件，才能将项目标记为完成：

- [ ] P0–P9 全部任务完成，阻塞项清零。
- [ ] [16-验收标准.md](docs/16-验收标准.md) 的 `ACC-ROLE`、`ACC-DATA`、`ACC-FLOW`、`ACC-STORAGE`、`ACC-API`、`ACC-OPS`、`ACC-PERF`、`ACC-XLSX`、`ACC-MCP` 核心条目全部通过。
- [ ] 并发负库存、软删除负库存、余额上限、重复提交、故障回滚测试通过。
- [ ] 六类 `code` 自动生成、并发唯一、不可修改、停用/删除不复用测试通过。
- [ ] 分类删除/停用边界、六类名称唯一与规范化查重、启用上级校验测试通过。
- [ ] XLSX 模板、部分填入、无副作用解析、原始数值精度、导出与安全测试通过。
- [ ] MCP 12/7 工具清单、越权拒绝、`code`/`doc_no` 定位、无内部 ID、原子提交、300 秒去重、限流与 Origin/Host 测试通过。
- [ ] 备份恢复演练成功，恢复后库存/单据一致；最近 7 个成功集清理、离线恢复与回滚命令可执行。
- [ ] 10 万明细性能达到基线，或形成经确认的调整结论。
- [ ] `uv run pytest` 全量通过；`uv sync --locked` 可复现环境。
- [ ] `uv run ruff check .` 与 `uv run ruff format --check .` 全量通过；无未说明的 `# noqa` 或临时禁用规则。
- [ ] OpenAPI、数据库迁移、部署文档、镜像版本（`only-stock:0.1.0`）一致；无硬编码密码/Key；日志无敏感信息。
- [ ] 如实施中修改了规范，对应 `docs/` 文档与 [00-文档索引与阅读指南.md](docs/00-文档索引与阅读指南.md) 已同步更新。

## 风险与处理

| 风险 | 影响 | 处理 |
|---|---|---|
| SQLite 并发/锁冲突 | 单据部分成功、负库存 | P1 事务封装；P4/P5 并发测试；统一 `BEGIN IMMEDIATE` 与冲突映射 |
| 六类 `code` 并发/回绕 | 编码重复、创建失败 | P3 计数器事务与唯一索引；并发生成测试 |
| 名称并发重名 | 主数据重复、XLSX 歧义 | P3 服务层查重 + 数据库唯一索引；并发测试 |
| 分类停用/删除边界 | 父级停用子级启用、历史数据误删 | P3 自底向上停用、引用检查、事务内校验 |
| 数量/余额溢出 | 库存计算错误 | P3 Decimal→milli；P5 边界与多行汇总测试 |
| XLSX 浮点/压缩炸弹 | 精度丢失、资源耗尽 | P6 原始 XML 词法、5 MiB/2,000 行、ZIP 结构校验 |
| MCP 权限/内部 ID/去重 | 越权、契约泄露、重复建单 | P7 动态过滤 + 调用重校验 + 输出白名单 + 指纹窗口 |
| 备份恢复误操作 | 覆盖唯一数据库 | P9 停服恢复、manifest/哈希/完整性校验、回滚区 |
| 文档与实现漂移 | 规范失效 | 任何行为变更先改 `docs/` 与索引，再改代码；TASKS 中记录依据 |
| 代码风格漂移 | 门禁失败、临时禁用规则 | 统一 Ruff 配置与 P1–P9 门禁；规则变更先改 `docs/02` 3.5 并记录依据 |

## 执行记录

| 日期 | 任务 | 命令/验证 | 结果 | 备注 |
|---|---|---|---|---|
| 2026-11-01 | T0.1 | 通读 `docs/00`、`docs/18`、`TASKS.md`；核对 10 个阶段的任务“依据”并写入“阶段—文档映射”；`ls -la` 检查仓库 | 通过：P0–P9 均有职责文档；仓库无 `app/`、`alembic/`、`tests/`、`pyproject.toml`、`uv.lock` | 依据 `docs/00` 文档清单、`docs/18` 15.1；基线快照见 P0“环境与仓库基线” |
| 2026-11-01 | T0.2 | `uv python install 3.13`；`uv python list --only-installed`；`uv python find 3.13`；`uv run python --version`；`uv python pin 3.13` | 通过：`uv 0.13.6`；托管 `cpython-3.13.18-linux-x86_64-gnu`；`uv python find 3.13` → `~/.local/share/uv/python/cpython-3.13-linux-x86_64-gnu/bin/python3.13`；`Python 3.13.18`；`.python-version` 为 `3.13` | 托管解释器安装完成，未使用 pip/venv |
| 2026-11-01 | T0.3 | 写入“测试目录”“阶段门禁命令清单（权威）”“执行记录格式”；逐项核对 P1–P9 末尾门禁代码块 | 通过：6 个测试目录、P0–P9 门禁命令和 5 字段记录格式已确定 | 依据 `AGENTS.md` 第 5、10 节；`docs/15` 12.1、12.2 |
| 2026-11-01 | P0 门禁 | `uv --version`；`uv run python --version` | 通过：`uv 0.13.6`；`Python 3.13.18` | 退出码均为 0 |
| 2026-11-03 | T0.4 | `find . -name '*.py' -not -path './.git/*'`（0 个）；`git diff --stat`；`git -c core.whitespace=cr-at-eol diff --check`；核对 `docs/02` 3.5 与 P1–P9 门禁块 | 通过（文档）：Ruff 配置基线、只读命令、`# noqa` 边界和门禁口径已写入 6 个 Markdown 文件；仓库无 Python 文件，未安装/未运行 Ruff | 按要求只改文档、不配置环境；实际 `ruff check`/`format --check` 由 T1.8 在 T1.1 建立依赖后执行 |
| 2026-11-03 | T1.1 | 用户执行 `uv self update`、`uv sync`；核对侧执行 `uv lock --check`、`uv sync --locked --offline`、`uv run python --version` | 通过：`uv 0.13.7`、`Python 3.13.15`；lock 校验退出 0；`Checked 57 packages`；依赖含 FastAPI、Uvicorn、SQLAlchemy 2、Alembic、Jinja2、Argon2-cffi、APScheduler、openpyxl、python-multipart、MCP SDK、pytest、Ruff | `pyproject.toml`、`uv.lock`、`.gitignore`、`.env.example` 与基础包结构已生成；DSH 注入 `UV_PROJECT_ENVIRONMENT`，验证时指向项目 `.venv`；验证命令加 `--offline` |
| 2026-11-03 | T1.2 | 创建 `app/`（`main.py`、`config.py`、`db/`、`auth/`、`mcp/`、`modules/`、`services/`、`templates/`、`static/`、`cli.py`）、`alembic/`、`tests/` 六个测试目录；`uv run pytest tests/unit/test_package_structure.py -q`；`uv run python -m app.cli --help` | 通过：33 个包/模块导入；CLI 退出码 0；未注册业务路由 | 依据 `docs/02` 3.3；空测试目录用 `.gitkeep` 占位 |
| 2026-11-03 | T1.3 | `uv run pytest tests/unit/test_config.py -q`（20 passed）；`uv run pytest tests/integration/test_startup.py -q`（3 passed） | 通过：DATA_DIR、BACKUP_DIR、会话、MCP、TZ、BOOTSTRAP 解析与校验；非法配置启动失败；密码不出现在错误消息或 `Settings` repr | 依据 `docs/13` 10.1、10.4，`docs/04` 5.1、5.4；启动时创建并验证目录 |
| 2026-11-03 | T1.4 | `uv run pytest tests/unit/test_sqlite.py tests/unit/test_session.py -q`（8 passed） | 通过：每连接 foreign_keys=ON、WAL、synchronous=NORMAL、busy_timeout=5000；`BEGIN IMMEDIATE` 提交/回滚；SQLITE_BUSY 映射 409 `WRITE_CONFLICT`；请求级 Session 关闭；启动 PRAGMA 自检 | 依据 `docs/02` 3.4、`docs/05` 6.1；未使用 ATTACH |
| 2026-11-03 | T1.5 | `uv run pytest tests/unit/test_errors.py tests/unit/test_logging.py -q`（8 passed）；`curl` 检查 `X-Request-ID` 响应头 | 通过：统一 `error.code/message/request_id/details`；`details` 含 `resource`、`items`；校验错误不回显输入；500 响应不泄露异常详情；日志为 UTC 且含请求 ID | 依据 `docs/09` 8.1、`docs/14` 11.1、11.2 |
| 2026-11-03 | T1.6 | `uv run pytest tests/unit/test_health.py -q`（7 passed）；启动 uvicorn 后 `curl /health/live`、`/health/ready` | 通过：live 200；ready 200 且 `data_dir`、`backup_dir`、`database`、`migrations`、`backup_scheduler` 全为 true；删除数据目录或停止调度器返回 503 且不泄露路径 | 迁移检查使用 `schema_migrations`；备份任务本身按 P9 实现 |
| 2026-11-03 | T1.7 | `uv run pytest -q`（84 passed）；`uv run pytest tests/unit -q`（78 passed） | 通过：临时数据库、应用测试客户端、并发 Barrier 辅助可用；测试使用 `tmp_path`，未读写开发数据库或真实备份目录 | CA/OP/禁用账户与基础资料夹具依赖 `users` 及业务表，需在 T2.1、P3 建立模型后补入；本阶段完成测试基础设施与隔离 |
| 2026-11-03 | T1.8 | `uv run ruff check .`；`uv run ruff format --check .` | 通过：0 告警，67 个文件格式一致；无 `# noqa`；规则、排除路径与格式化选项同 `docs/02` 3.5.1 | 补充 `known-first-party = ["app", "tests"]` 并同步 `docs/02` 3.5.1，避免 isort 将 `app` 误判为第三方 |
| 2026-11-03 | P1 门禁 | `uv run ruff check .`；`uv run ruff format --check .`；`uv run python -c "from app.main import app"`；`uv run pytest tests/unit`（78 passed）；`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`（后台）；`curl --fail --silent http://127.0.0.1:8000/health/live`；`curl --fail --silent http://127.0.0.1:8000/health/ready` | 通过：Ruff 0；应用导入成功；78 passed；live 200 `{"status":"ok"}`；ready 200 `{"status":"ready","checks":{...}}`；验证后已停止服务 | `uv 0.13.7`、`Python 3.13.15`；因 DSH 注入 `UV_PROJECT_ENVIRONMENT`，命令使用项目 `.venv` 并加 `--offline`；门禁启动创建了 `data/`、`backups/`（均被忽略），验证后已删除 |
| 2026-11-03 | T2.1 | `UV_CACHE_DIR=/tmp/only-stock-uv-cache uv run alembic upgrade head`；`uv run alembic current`；`uv run alembic check`；`uv run pytest tests/integration/test_account_migration.py -q` | 通过：`0001_accounts (head)`；`No new upgrade operations detected`；8 passed；首次迁移建立 `users`、`sessions`、`api_keys`，UUID 主键、epoch 毫秒 UTC 时间、登录名/会话哈希/Key 哈希唯一索引与外键均生效；外键违规被拒绝 | 依据 `docs/05` 6.1、`docs/04` 5.1、5.2、5.4、5.5。先修正 `docs/10` 8.3 中 API Key 路径与 `docs/04` 5.5 的矛盾，统一为 `/api/v1/api-keys`；迁移与模型由 `alembic check` 校验一致 |
| 2026-11-03 | T2.2 | `uv run pytest tests/unit/test_passwords.py -q` | 通过：5 passed；Argon2id 哈希可校验、错误哈希返回 False、密码至少 12 位、超长拒绝；错误信息不回显密码 | 依据 `docs/04` 5.4、`docs/03` 4.3；密码明文未落库、未进入日志 |
| 2026-11-03 | T2.3 | `uv run pytest tests/integration/test_bootstrap_admin.py -q` | 通过：7 passed；空库首次启动创建首位 `COMPANY_ADMIN` 并强制改密；重复启动不重复创建；非空库忽略初始化变量；并发初始化仅一个成功；触发器注入失败时整体回滚、无半成品账户 | 依据 `docs/04` 5.2、`docs/13` 10.4；启动仅在迁移为当前 head 时执行初始化 |
| 2026-11-03 | T2.4 | `uv run pytest tests/integration/test_auth_api.py -q`；`uv run pytest tests/unit/test_rate_limit.py -q` | 通过：17 passed、3 passed；登录/登出/当前身份、Set-Cookie 属性（HttpOnly、SameSite=Lax、无 Secure）、CSRF、8 小时空闲/24 小时绝对超时、禁用账户拒绝、登录限流 429；未知账户与错误密码响应一致 | 依据 `docs/04` 5.4、`docs/09` 8.1；会话 ID 使用 32 字节随机数，库内只存哈希；CSRF Token 由会话派生且库内只存哈希 |
| 2026-11-03 | T2.5 | `uv run pytest tests/integration/test_auth_api.py tests/security/test_auth_security.py -q` | 通过：本人改密清除强制改密、撤销其他会话与全部 Key、轮换当前会话；旧 Cookie 失效、旧 CSRF 失效；管理员重置服务撤销全部会话/Key、设置强制改密、不回显密码 | 依据 `docs/03` 4.3、`docs/04` 5.4；重置密码接口按 P3 T3.3 提供，本阶段完成核心服务 |
| 2026-11-03 | T2.6 | `uv run pytest tests/integration/test_api_keys_api.py tests/security/test_auth_security.py -q` | 通过：7 passed、16 passed；创建返回完整 Key 一次、列表不含明文、库内仅 SHA-256 哈希与末四位；最多 10 把有效 Key；本人隔离、重复吊销幂等；改密/重置/角色变化/禁用均在同事务撤销全部 Key | 依据 `docs/04` 5.5、`docs/05` 6.1；角色变化与禁用由 `apply_user_state_change` 在调用方 `BEGIN IMMEDIATE` 内完成，最后管理员保护留给 P3 T3.2 |
| 2026-11-03 | T2.7 | `uv run pytest tests/integration/test_auth_api.py tests/integration/test_api_keys_api.py -q`；检查 `/openapi.json` | 通过：24 passed；`/auth/login`、`/auth/logout`、`/auth/me`、`/auth/change-password`、`/api/v1/api-keys`、`/api/v1/api-keys/{key_id}/revoke` 与 OpenAPI 一致；写接口均校验 CSRF，API Key 明文仅在创建成功响应出现一次 | 依据 `docs/10` 8.3（已消除 `/api-keys` 与 `/api/v1/api-keys` 矛盾）、`docs/09` 8.1 |
| 2026-11-03 | T2.8 | `uv run pytest tests/security -q`；`uv run pytest tests/unit/test_passwords.py tests/unit/test_login_names.py tests/unit/test_db_types.py tests/unit/test_api_key_format.py -q` | 通过：16 passed；18 passed；覆盖登录限流、会话超时、CSRF、Cookie 属性、密码哈希、Key 单次显示/哈希存储、禁用与吊销、日志脱敏；数据库文件与日志均无明文密码、会话 ID、CSRF Token、完整 Key 或 Authorization 头 | 依据 `docs/16` 13.1、13.5，`docs/15` 12.1 |
| 2026-11-03 | P2 门禁 | `UV_CACHE_DIR=/tmp/only-stock-uv-cache uv run ruff check .`；`uv run ruff format --check .`；`uv run alembic upgrade head`；`uv run pytest tests/unit tests/integration tests/security` | 通过：Ruff 0 告警、91 个文件格式一致；迁移到 `0001_accounts (head)`；175 passed（unit+integration+security） | 依据 `TASKS.md` P2 阶段门禁；另执行 `uv run pytest -q` 全量 175 passed；`uv run alembic check` 无模型漂移 |
| 2026-11-03 | T3.1 | `uv run pytest tests/integration/test_masterdata_api.py::test_operator_can_read_but_cannot_manage tests/integration/test_users_api.py::test_operator_cannot_access_user_management tests/integration/test_audit_api.py::test_audit_api_rejects_operator -q` | 通过：3 passed；操作员查询基础资料 200，创建/删除/停用基础资料、用户管理与审计查询均 403 `FORBIDDEN`；每个请求重新加载账户状态 | 依据 `docs/03` 4.1、4.2，`docs/09` 8.2；`app/auth/permissions.py` 统一服务端授权，页面隐藏不作为权限边界 |
| 2026-11-03 | T3.2 | `uv run pytest tests/integration/test_users_api.py -q`（13 passed）；`uv run pytest tests/concurrency/test_last_admin.py tests/concurrency/test_name_conflicts.py -q`（9 passed） | 通过：创建/改显示名/改角色/禁用/重置可用；最后一名启用管理员不能降级或禁用，两名管理员并发互降/互禁时恰好一个成功且至少保留一名启用管理员；角色变化和禁用撤销目标会话与全部 API Key | 依据 `docs/03` 4.3；检查、统计与写入在 `BEGIN IMMEDIATE` 内完成并写审计 |
| 2026-11-03 | T3.3 | `uv run pytest tests/integration/test_users_api.py -q` | 通过：13 passed；`GET/POST /api/v1/users`、`PATCH /users/{id}`、`/disable`、`/reset-password` 仅 CA 可用；分页、筛选、`version` 冲突、最后管理员保护、响应不回显密码；OpenAPI 路径一致 | 依据 `docs/10` 8.4；用户创建、禁用、重置写审计，审计与业务同事务 |
| 2026-11-03 | T3.4 | `uv run pytest tests/unit/test_codes.py tests/concurrency/test_code_generation.py -q`；`uv run pytest tests/integration/test_account_migration.py::test_models_and_migration_are_in_sync -q` | 通过：11 passed；六类前缀 `CK/BM/FL/WZ/XM/CJ` 与固定 5 位补零；并发创建编码唯一；序号 99999 后失败且不回绕；插入失败时计数器整体回滚；停用/删除后编码不复用；`alembic check` 无模型漂移 | 依据 `docs/05` 6.2；`code_counters` 仅在 `BEGIN IMMEDIATE` 写事务内更新 |
| 2026-11-03 | T3.5 | `uv run pytest tests/integration/test_masterdata_api.py -q` | 通过：23 passed；仓库（含位置）、部门、项目、厂家创建返回系统编码，列表默认仅启用、可切换停用；名称去首尾空白后唯一且区分大小写；删除/停用/启用按引用规则执行；创建后无修改接口 | 依据 `docs/05` 6.2、6.7，`docs/10` 8.4 |
| 2026-11-03 | T3.6 | `uv run pytest tests/integration/test_masterdata_api.py -q` | 通过：23 passed；分类严格两级，父分类必须为一级且启用；一级名称全局唯一、二级名称在父级内唯一；树查询返回嵌套 `children`；存在未停用子分类/物资时停用返回 409 并列出阻碍项；删除受子分类、物资与引用约束 | 依据 `docs/05` 6.4、6.7、6.8，`docs/06` 7.6.1 |
| 2026-11-03 | T3.7 | `uv run pytest tests/integration/test_masterdata_api.py -q` | 通过：23 passed；物资必须挂一级或二级且启用分类，名称在直接所属分类内唯一，单位必填，`INTEGER/DECIMAL` 校验生效；停用/启用与分类上级校验生效；创建后不可修改 | 依据 `docs/05` 6.4、6.6、6.7 |
| 2026-11-03 | T3.8 | `uv run pytest tests/unit/test_names.py -q`（9 passed）；`uv run pytest tests/concurrency/test_name_conflicts.py tests/concurrency/test_code_generation.py -q`（9 passed） | 通过：仅去除首尾空白，保留大小写与内部空白；停用记录仍占名；仓库/分类/物资并发同名仅一个成功；服务层查重与部分唯一索引/复合唯一索引共同生效 | 依据 `docs/05` 6.4、6.7、6.8；并发对手竞争由 `BEGIN IMMEDIATE` 串行化并以唯一索引兜底 |
| 2026-11-03 | T3.9 | `uv run pytest tests/integration/test_masterdata_api.py -q`；`uv run pytest tests/concurrency/test_name_conflicts.py -q` | 通过：23 passed、6 passed；停用/启用只改 `is_active` 并写审计，不改历史；启用已启用记录幂等且不重复写审计；分类自底向上停用、`PARENT_INACTIVE`、`CATEGORY_HAS_ACTIVE_CHILDREN`、`CATEGORY_HAS_ACTIVE_MATERIALS` 均按文档返回；停用与新增下级资料竞争后不产生“父停子启” | 依据 `docs/05` 6.7，`docs/06` 7.6、7.6.1 |
| 2026-11-03 | T3.10 | `uv run pytest tests/unit/test_quantity.py -q` | 通过：20 passed；`1.25` 转 `1250`、`0.001` 转 `1`；拒绝浮点、布尔、科学计数法、零/负数、四位小数与超上限；整数物资拒绝小数；`MAX_BALANCE_MILLI=9000000000000000000` 低于 SQLite 有符号 64 位上限 | 依据 `docs/05` 6.6；数量输入按字符串解析为 `Decimal`，输出固定三位小数 |
| 2026-11-03 | T3.11 | `uv run pytest tests/integration/test_audit_api.py -q` | 通过：8 passed；系统初始化、登录、用户创建/禁用/重置、API Key 创建/吊销、六类资料创建/停用/启用/删除均写审计；记录操作者、时间、请求 ID、对象、动作、前后状态与来源；`source` 仅 `WEB/API/MCP`；数据库触发器拒绝 UPDATE/DELETE；审计不含密码、完整 Key 或哈希 | 依据 `docs/04` 5.6；MCP 来源与 `api_key_id` 字段已预留，P7 接入 |
| 2026-11-03 | T3.12 | `uv run pytest tests/integration/test_masterdata_api.py tests/integration/test_master_data_migration.py -q` | 通过：30 passed；六类资料 GET/POST/DELETE/停用/启用与审计查询路径齐全；创建请求携带 `code` 返回 422，成功响应返回系统生成 `code`；列表分页、错误 `details.resource/items` 结构、CA/OP 权限与 OpenAPI 一致 | 依据 `docs/10` 8.4、`docs/09` 8.1；六类创建 schema 使用 `extra="forbid"` |
| 2026-11-03 | T3.13 | `uv run pytest tests/concurrency -q` | 通过：12 passed；覆盖六类并发生成编码、编码序号耗尽、插入失败计数器回滚、并发同名仓库/分类/物资、不同层级同名允许、分类停用与新增物资/启用子类竞争、最后管理员并发保护 | 依据 `docs/16` 13.2，`docs/15` 12.1、12.2；测试使用 `tmp_path` 独立数据库与 Barrier 并发辅助 |
| 2026-11-03 | P3 门禁 | `UV_CACHE_DIR=/tmp/only-stock-uv-cache uv run ruff check .`；`uv run ruff format --check .`；`uv run pytest tests/unit tests/integration`；`uv run pytest tests/concurrency` | 通过：Ruff 0 告警、124 个文件格式一致；unit+integration 247 passed；concurrency 12 passed；另执行全量 `uv run pytest -q` 275 passed | 依据 `TASKS.md` P3 阶段门禁；迁移 `0002_master_data` 与模型通过 `alembic check`，未修改 `docs/` 产品规则 |
