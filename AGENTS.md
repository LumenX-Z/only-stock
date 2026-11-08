# AGENTS.md

本文件面向在本仓库工作的编码代理和开发者，规定工作方式、开发环境与运行命令。

产品行为、数据模型、接口契约、页面交互、部署要求和验收标准，以 `docs/` 中的文档为唯一事实来源。

## 1. 唯一事实来源

- **`docs/` 目录下的文档是本项目唯一的事实来源。** 入口是 [00-文档索引与阅读指南.md](docs/00-文档索引与阅读指南.md)，该索引包含全部文档的职责清单和章节索引。
- 任何实现、测试、部署配置、脚本和提交说明，都必须以 `docs/` 中对应职责文档为依据。
- 代码或测试与 `docs/` 不一致时，以 `docs/` 为准修正代码；若确认 `docs/` 本身需要变更，先更新对应文档和 [00-文档索引与阅读指南.md](docs/00-文档索引与阅读指南.md)，再改代码与测试。
- 不要在其他文档、代码注释或测试中另行建立一套规则；需要引用规则时，引用对应文档的章节编号。
- 仓库结构按 [02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md) 维护；实施顺序与完成定义见 [18-实施顺序与完成定义.md](docs/18-实施顺序与完成定义.md)。

## 2. 技术环境

- Python 3.13。
- FastAPI、Jinja2、HTMX、SQLAlchemy 2、Alembic、pytest、SQLite。
- 代码风格检查与格式化统一使用 Ruff（开发依赖），规则见 [02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md) 3.5。
- 单一公司、单一 SQLite 数据库、服务端渲染；生产环境为单 `app` 服务、单 worker，MCP 与 HTTP 同应用同端口。
- 依赖与环境统一使用 **uv** 管理，使用 `pyproject.toml` 声明依赖、`uv.lock` 锁定版本。

### 禁止事项

- 不使用 `pip`、`pip3`、`python -m venv`、`virtualenv`、`poetry`、`conda` 安装或管理依赖。
- 不手工激活或修改 `.venv`；所有 Python 命令通过 `uv run` 执行。
- 不手工编辑 `uv.lock`；新增、升级、移除依赖必须使用 `uv add` / `uv remove` / `uv lock`。
- 不在 Dockerfile 中执行无锁定的 `pip install`；镜像构建同样使用 uv 和锁定依赖。

## 3. 首次环境准备

在仓库根目录执行：

```bash
# 安装并固定 Python 3.13
uv python install 3.13
uv python pin 3.13

# 首次建立或更新锁文件
uv lock

# 按锁定依赖安装环境
uv sync --locked

# 验证解释器
uv run python --version
```

CI、验收环境或需要严格复现时，统一使用：

```bash
uv sync --locked
```

如果 `uv.lock` 与 `pyproject.toml` 不一致，先执行 `uv lock`，并把 `pyproject.toml` 与 `uv.lock` 一起提交。

## 4. 本地运行

```bash
# 本地开发（仅本机访问，单 worker）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 与部署形态一致（单 worker）
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

- 开发时可以使用 `--reload`；验收和部署不得增加 worker 或副本。
- 页面：`http://127.0.0.1:8000`
- MCP：`http://127.0.0.1:8000/mcp`
- 健康检查：

```bash
curl --fail --silent http://127.0.0.1:8000/health/ready
```

- 首次启动的初始化变量、`.env` 配置和首次登录流程见 [13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 的 10.4 节；初始密码不得提交到仓库。

## 5. 测试

```bash
# 代码风格与静态检查（规则见 docs/02 3.5）
uv run ruff check .
uv run ruff format --check .

# 全量测试
uv run pytest

# 按职责运行
uv run pytest tests/unit
uv run pytest tests/integration
uv run pytest tests/security
uv run pytest tests/concurrency
uv run pytest tests/e2e
uv run pytest tests/recovery
```

- 提交或交付前必须运行 `uv run ruff check .` 与 `uv run ruff format --check .`，并运行与改动相关的测试；涉及权限、事务、库存、并发或备份恢复的改动，必须运行对应专项测试。
- Ruff 规则、`# noqa` 边界和修复命令见 [02-技术架构与代码组织.md](docs/02-技术架构与代码组织.md) 3.5；不得通过关闭规则或排除目录让代码通过。
- 测试层次与必备夹具见 [15-测试方案.md](docs/15-测试方案.md)；验收口径见 [16-验收标准.md](docs/16-验收标准.md)。
- 测试必须使用独立临时数据库或测试配置，不得读写开发数据库、生产数据卷或真实备份集。

## 6. 数据库与迁移

```bash
# 应用迁移
uv run alembic upgrade head

# 查看当前版本
uv run alembic current

# 生成迁移（修改模型后）
uv run alembic revision --autogenerate -m "describe change"
```

- 使用单一 Alembic 迁移链，不创建并行 head。
- 不直接执行 `sqlite3` 修改业务表；结构变更必须通过迁移。
- 迁移前先确认 `docs/05-数据模型与实体约束.md` 中的表、约束和索引规则，迁移不得绕过其中的外键、唯一约束和触发器要求。
- 生产升级、备份和恢复要求见 [13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 的 10.5–10.7 节。

## 7. CLI、备份与恢复

本地通过 uv 执行应用 CLI：

```bash
uv run python -m app.cli --help
uv run python -m app.cli backup list
```

容器内执行同一 CLI：

```bash
docker compose run --rm --no-deps app python -m app.cli backup list
```

- 恢复（`restore`）只能在停止 `app` 后按 [13-Docker部署与运维.md](docs/13-Docker部署与运维.md) 10.7 节执行；不得在应用运行期间恢复或覆盖数据库。
- 备份恢复流程必须可重试、可校验，并记录 manifest、哈希和完整性检查结果；具体契约以文档 10.6、10.7 为准。

## 8. Docker Compose

```bash
docker compose --env-file .env up -d --build
docker compose ps
docker compose logs -f app
docker compose stop app
```

- 只运行一个 `app` 服务；不得通过增加 worker、副本或独立 MCP 服务绕过 SQLite 与单进程约束。
- Dockerfile 必须使用 uv 安装锁定依赖，以非 root 用户运行；不得把 `.env`、数据库、备份或初始密码打入镜像。
- 服务组成、卷、端口、健康检查和首次启动步骤见 [13-Docker部署与运维.md](docs/13-Docker部署与运维.md)。

## 9. 依赖变更

```bash
# 增加运行时依赖
uv add <package>

# 增加开发/测试依赖
uv add --dev <package>

# 移除依赖
uv remove <package>

# 更新锁文件并同步环境
uv lock
uv sync --locked
```

- 依赖版本通过 `uv.lock` 固定；不要使用未锁定版本或手工修改虚拟环境。
- Ruff、pytest 等仅用于开发、测试和校验的工具使用 `uv add --dev`，不得成为运行时依赖。
- 新增依赖前先确认 `docs/02-技术架构与代码组织.md` 的技术组件范围；不得引入与既定技术栈冲突的框架。
- 每次依赖变更同时提交 `pyproject.toml` 和 `uv.lock`。

## 10. 开发工作流

1. 明确当前任务与对应的职责文档；阶段门禁未通过时，不跳到下一阶段。
2. 在 [00-文档索引与阅读指南.md](docs/00-文档索引与阅读指南.md) 中找到该任务对应的职责文档。
3. 阅读对应章节，确认数据模型、接口、权限和验收要求。
4. 如行为或契约需要变化，先更新对应文档与索引，再实施代码。
5. 使用 `uv run` 执行开发、测试、迁移和 CLI 命令。
6. 运行 `uv run ruff check .`、`uv run ruff format --check .` 和相关测试；涉及数据库、并发、权限、XLSX、MCP 或备份恢复时，补跑专项测试。
7. 更新任务状态与执行记录；交付说明中引用任务 ID（如有）、所依据的文档章节、测试命令与结果。

## 11. 执行计划与任务跟踪

- 任务拆分、阶段门禁与完成状态以当前阶段计划为准；每个任务包含“依据、产出、完成标准”三项，开始前先读依据，完成后逐项核对完成标准。
- `[ ]` 未开始、`[~]` 进行中、`[x]` 已完成、`[!]` 阻塞。不要在没有测试、验收或阶段门禁证据时把任务标为已完成。
- 阻塞时在执行记录中写明原因、缺失信息和下一步；若阻塞来自文档缺失或矛盾，先更新 `docs/` 与索引，不得在代码中猜测规则。
- 阶段完成时必须执行该阶段计划中的门禁命令；全量完成时执行 `uv run pytest` 和发布检查。
- 不要绕过阶段门禁直接开始后续阶段；允许在阶段内并行，但必须保持依赖顺序。

## 12. 不可违反的关键约束

以下约束来自 `docs/`，实现时不得放宽；详细规则以链接文档为准：

- 单一公司、单一 SQLite 数据库，不引入公司/租户切换或第二业务数据库。见 [04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md)、[05-数据模型与实体约束.md](docs/05-数据模型与实体约束.md)。
- 禁止负库存；提交、软删除和库存校验必须在写事务内重新计算，失败整单回滚。见 [06-库存计算与单据业务流程.md](docs/06-库存计算与单据业务流程.md)。
- 页面按钮隐藏和 MCP 工具清单过滤都不是权限边界；每个 HTTP 请求和每次 MCP 工具调用都必须在服务端重新校验角色、账户和 Key 状态。见 [03-角色、权限与账户生命周期.md](docs/03-角色、权限与账户生命周期.md)、[04-认证、会话与凭证安全.md](docs/04-认证、会话与凭证安全.md)。
- XLSX 只用于填入新建页表单，不直接创建、修改或提交单据。见 [07-XLSX模板、快速录入与导出.md](docs/07-XLSX模板、快速录入与导出.md)。
- 生产环境只运行单个 `app` 服务、单 worker；MCP 与 HTTP 共用同一进程和 SQLite 数据库。见 [08-MCP服务与工具.md](docs/08-MCP服务与工具.md)、[13-Docker部署与运维.md](docs/13-Docker部署与运维.md)。
