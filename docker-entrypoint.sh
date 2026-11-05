#!/bin/sh
# 容器入口：仅在启动应用时先执行唯一迁移执行器，随后用 docs/13 10.2 的固定命令
# 启动单 worker。docker compose run 执行 backup/restore/migrate CLI 时不自动迁移，
# 避免恢复流程被入口脚本改动数据库。
set -eu

if [ "${1:-}" = "uvicorn" ]; then
    python -m app.cli migrate
fi

exec "$@"
