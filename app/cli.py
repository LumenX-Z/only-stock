"""应用 CLI 入口。

依据 docs/13-Docker部署与运维.md 10.4–10.7：

- ``backup list`` 列出备份集，供离线恢复选择 ``backup_set_id``；
- ``restore --backup-set <id> --yes`` 校验并安装完整备份集；
- ``restore --rollback-from <recovery-pre 目录> --yes`` 回滚到恢复区副本；
- ``migrate`` 是唯一迁移执行器，容器入口脚本与人工升级共用。

所有命令失败均返回非零退出码；恢复与迁移要求应用已停止。
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC

from app.config import ConfigError, Settings
from app.db.migration_runner import MigrationError, run_migrations
from app.modules.backups.restore import RestoreError, restore_backup_set, rollback_from
from app.modules.backups.service import BackupInfo, list_backups


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.cli",
        description="only-stock 管理命令：迁移、备份列表、离线恢复与回滚",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    migrate = subcommands.add_parser("migrate", help="升级数据库到唯一迁移 head")
    migrate.set_defaults(handler=_run_migrate)

    backup = subcommands.add_parser("backup", help="备份集查询")
    backup_commands = backup.add_subparsers(dest="backup_command", required=True)
    backup_list = backup_commands.add_parser("list", help="列出备份集与失败记录")
    backup_list.set_defaults(handler=_run_backup_list)

    restore = subcommands.add_parser("restore", help="离线恢复或回滚数据库")
    source = restore.add_mutually_exclusive_group(required=True)
    source.add_argument("--backup-set", dest="backup_set", help="完整备份集 ID")
    source.add_argument(
        "--rollback-from",
        dest="rollback_from",
        help="恢复区目录，例如 /data/recovery-pre/20260101T000000Z",
    )
    restore.add_argument(
        "--yes",
        action="store_true",
        help="非交互确认；恢复与回滚必须显式提供",
    )
    restore.set_defaults(handler=_run_restore)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        return int(args.handler(args, settings))
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    except (MigrationError, RestoreError) as exc:
        stage = getattr(exc, "stage", "")
        print(f"失败阶段：{stage}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1


def _run_migrate(_args: argparse.Namespace, settings: Settings) -> int:
    result = run_migrations(settings)
    print(
        "迁移完成："
        f"{result.from_revision or 'base'} -> {result.to_revision} "
        f"用时 {result.duration_seconds:.3f} 秒"
    )
    return 0


def _run_backup_list(_args: argparse.Namespace, settings: Settings) -> int:
    items, total = list_backups(settings, page=1, page_size=1000)
    if not items:
        print("没有备份集。")
        return 0
    print(f"备份集（共 {total} 个）：")
    print(f"{'备份集 ID':<28} {'状态':<8} {'时间(UTC)':<26} {'大小':>10}  完整性")
    for item in items:
        print(_format_backup(item))
    print("只有状态为 SUCCESS 且完整性为 ok 的备份集可用于 restore。")
    return 0


def _format_backup(item: BackupInfo) -> str:
    size = f"{item.size_bytes / 1024 / 1024:.2f} MiB"
    integrity = item.integrity or "-"
    created = item.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    return f"{item.id:<28} {item.status:<8} {created:<26} {size:>10}  {integrity}"


def _run_restore(args: argparse.Namespace, settings: Settings) -> int:
    if not args.yes:
        print(
            "恢复与回滚必须显式提供 --yes，且在 docker compose stop app 之后执行。",
            file=sys.stderr,
        )
        return 2
    if args.backup_set:
        result = restore_backup_set(settings, args.backup_set)
        action = "恢复"
        source = f"备份集 {result.backup_set_id}"
    else:
        result = rollback_from(settings, args.rollback_from)
        action = "回滚"
        source = f"恢复区 {args.rollback_from}"
    print(f"{action}完成：{source}")
    print(f"恢复时间（UTC）：{result.restored_at.isoformat()}")
    print(f"SHA-256：{result.sha256}")
    print(f"完整性：{result.integrity}")
    print(f"原数据库已移动到：{result.recovery_dir}")
    print("请启动 app 并检查 /health/ready、登录、库存、最近单据与 MCP 工具调用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
