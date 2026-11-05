# only-stock·Docker 部署与运维

> 文档版本：1.0 ｜ 文档状态：现行有效
> 发布目标：第一阶段发布 0.1.0 版本镜像
> 所属系统：only-stock（单一公司内网；Python 全栈、单一 SQLite、Docker Compose）
> 本文职责：规定 Docker Compose 服务、镜像、首次启动、升级迁移、备份与离线恢复。
> 导航：[文档索引](00-文档索引与阅读指南.md) ｜ [术语与总体约束](01-项目概览与范围.md#14-关键术语)

---

## 10. Docker Compose 部署方案

### 10.1 服务组成

本期只运行一个 `app` 服务：

- FastAPI/Uvicorn 单 worker，避免多副本调度和 SQLite 部署复杂度；MCP 挂载在同一 ASGI 应用，不新增服务。
- 内置 APScheduler 每日备份；禁止通过增加 worker 或副本绕过此约束。
- 两个持久卷：`inventory_data` 保存数据库，`inventory_backups` 保存备份集。
- 端口直接映射为宿主机 `8000`，页面通过 `http://服务器IP:8000` 访问，MCP 客户端通过 `http://服务器IP:8000/mcp` 连接。
- 无反向代理、无 HTTPS；会话 Cookie 不设置 `Secure`。

### 10.2 `compose.yaml` 参考

```yaml
services:
  app:
    build:
      context: .
    image: only-stock:0.1.0
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      APP_ENV: production
      DATA_DIR: /data
      BACKUP_DIR: /backups
      BACKUP_TIME: "02:00"
      BACKUP_RETENTION_SETS: "7"
      SESSION_IDLE_MINUTES: "480"
      SESSION_ABSOLUTE_HOURS: "24"
      MCP_KEY_RATE_PER_MINUTE: "60"
      MCP_IP_RATE_PER_MINUTE: "120"
      MCP_ALLOWED_HOSTS: "127.0.0.1,localhost,服务器IP"
      TZ: Asia/Shanghai
      BOOTSTRAP_ADMIN_USERNAME: ${BOOTSTRAP_ADMIN_USERNAME:-}
      BOOTSTRAP_ADMIN_PASSWORD: ${BOOTSTRAP_ADMIN_PASSWORD:-}
    volumes:
      - inventory_data:/data
      - inventory_backups:/backups
    security_opt:
      - no-new-privileges:true
    stop_grace_period: 30s
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 30s

volumes:
  inventory_data:
  inventory_backups:
```

容器启动命令固定为：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

镜像入口脚本以非 root 用户先调用唯一迁移执行器 `python -m app.cli migrate`，成功后用
`exec` 启动上述固定命令；迁移失败时入口脚本以非零状态退出，容器不会带着不兼容的
数据库结构对外服务。`app` 进程启动时对 `/data/app.lock` 持有排他锁，迁移和恢复 CLI
在同一锁被占用时拒绝执行，用于保证升级、恢复前应用确实已停服。

### 10.3 Dockerfile 要求

1. 使用受支持的 Python 3.13 精简基础镜像并固定镜像版本。
2. 以非 root 用户运行；`/data` 和 `/backups` 必须对该用户可写。
3. 使用锁文件安装依赖，不安装编译调试工具到最终运行层。
4. 镜像包含迁移、备份和恢复 CLI，入口脚本使用固定 `uvicorn ... --workers 1` 命令。
5. 不把 `.env`、数据库文件、备份或初始密码打入镜像。
6. 构建后进行依赖漏洞和镜像安全检查。

### 10.4 首次启动

1. 在部署目录准备 `.env`，设置唯一的初始公司管理员登录名和高强度随机初始密码：

```dotenv
BOOTSTRAP_ADMIN_USERNAME=initial-admin
BOOTSTRAP_ADMIN_PASSWORD=replace-with-a-long-random-password
```

2. 启动：

```bash
docker compose --env-file .env up -d --build
```

3. 检查：

```bash
docker compose ps
docker compose logs app
```

4. 仅当数据库没有任何用户时，应用读取初始化变量并创建首位公司管理员；否则忽略这些变量。
5. 首次登录必须修改密码。初始化成功后，从 `.env` 删除 `BOOTSTRAP_ADMIN_PASSWORD` 并同时删除 `BOOTSTRAP_ADMIN_USERNAME`（或把两者都留空），再重新执行 `docker compose up -d`，使运行配置不再携带初始密码。两个变量只设置一个会导致启动配置校验失败，不会无提示地跳过初始化。
6. 使用 `http://服务器IP:8000` 登录，由公司管理员创建其他用户和基础资料。
7. 将 Compose 中 `MCP_ALLOWED_HOSTS` 的“服务器IP”替换为实际内网地址，保留需要访问的本机地址，不得配置为允许任意 Host。用户登录后在“我的 API Key”创建 Key，并将 MCP 客户端地址配置为 `http://服务器IP:8000/mcp`，Bearer 凭证通过客户端安全配置注入。

### 10.5 升级与迁移

第一阶段发布 0.1.0 版本镜像，系统此前尚未正式开发或上线，`api_keys`、审计来源字段和 MCP 相关结构直接纳入首次建库 schema，不设计既有数据回填、迁移兼容或旧版本升级路径；以下为后续版本投入使用后的通用升级规则。

1. 升级前执行完整在线备份并确认完整性。
2. 使用单一迁移执行器升级数据库，在独立事务和迁移锁内执行。唯一执行器为
   `python -m app.cli migrate`；容器入口脚本和应用手动升级均调用同一实现。
3. 记录迁移版本、开始时间、结果和错误；执行器在数据目录追加
   `migration-log.jsonl`，并输出到容器日志。
4. 迁移失败就停止就绪状态；不得在数据库结构不兼容时继续提供业务服务。应用启动后
   若数据库未处于单一迁移 head，除健康检查和静态资源外的业务请求返回 503。
5. 回滚应用代码前必须确认数据库迁移是否可逆；不可逆时按已验证备份恢复，不直接强行启动旧代码。

### 10.6 备份策略

每日 `02:00` 由应用内调度器执行一次完整备份，公司管理员也可通过页面或 API 手动触发：

1. 暂停新的迁移和恢复操作，取得备份全局锁。
2. 为本次备份建立唯一临时目录。
3. 使用 SQLite Online Backup API 或等价一致性接口，禁止在运行中直接复制数据库主文件作为备份。
4. 对备份文件执行 `PRAGMA integrity_check`。
5. 生成 `manifest.json`，记录备份集 ID、UTC 时间、应用版本、文件相对路径、大小和 SHA-256。
6. 全部成功后原子重命名为成功备份集；失败则标记失败并保留错误日志，不计入可恢复备份。
7. 只清理最早的成功备份集，始终保留最近 7 个成功备份集；保留数量由 `BACKUP_RETENTION_SETS` 配置（默认 `7`），手动备份与定时备份同等计入，清理失败不得影响新备份。该规则按备份集数量保留，不承诺保留最近 7 个自然日。

备份集结构：

```text
/backups/<backup_set_id>/
  inventory.sqlite3
  manifest.json
  integrity-report.json
```

### 10.7 恢复要求

恢复必须针对完整数据库备份，且只能由运维人员通过离线 CLI 执行。恢复 CLI 必须实现以下命令契约，容器运行用户、数据卷和镜像版本（`only-stock:0.1.0`）与 `app` 服务一致：

```bash
# 1. 停止业务写入。恢复期间不得启动 app。
docker compose stop app

# 2. 可选：列出可恢复备份集，取得 backup_set_id。
docker compose run --rm --no-deps app python -m app.cli backup list

# 3. 校验并恢复指定备份集；失败返回非零退出码。
docker compose run --rm --no-deps app python -m app.cli restore \
  --backup-set <backup_set_id> --yes

# 4. 恢复成功后启动并检查就绪。
docker compose up -d app
curl --fail --silent http://127.0.0.1:8000/health/ready
```

执行约束：

1. `restore` 必须先校验 manifest、SHA-256 和 `PRAGMA integrity_check`，全部通过后才可改动 `/data`；任一步失败不得覆盖当前数据库，进程以非零状态退出并输出失败阶段。
2. `restore` 必须将当前 `inventory.sqlite3` 及同目录的 `-wal`、`-shm` 文件移动到 `/data/recovery-pre/<UTC时间戳>/`，再将备份文件原子安装到数据目录，并保持运行用户可读写；不得直接覆盖唯一副本。恢复和回滚都通过 `/data/app.lock` 排他锁确认应用已停止，并在恢复区写入 `restore-report.json`。
3. `--backup-set` 只接受完整备份集 ID，不接受任意文件路径；`--yes` 用于非交互执行，缺少该参数时不得在无人值守场景继续。
4. 恢复后启动应用并验证就绪检查、登录、库存视图、最近单据和 MCP 工具调用；恢复时完整数据库中的 API Key 一并恢复，恢复演练后应吊销测试 Key。
5. 若启动后验证失败，停止 `app` 并使用保留区回滚：

```bash
docker compose stop app
docker compose run --rm --no-deps app python -m app.cli restore \
  --rollback-from /data/recovery-pre/<UTC时间戳> --yes
docker compose up -d app
```

6. 每季度至少做一次隔离环境恢复演练，记录命令、备份集 ID、校验结果和回滚结果。
