"""唯一数据库迁移执行器。

依据 docs/13-Docker部署与运维.md 10.5：

- 通过 Alembic 单一迁移链升级到唯一 head；
- 在数据目录 ``app.lock`` 排他锁内执行，应用运行期间拒绝升级；
- 记录迁移前版本、目标 head、开始/结束时间、结果和错误到
  ``migration-log.jsonl``；失败时保留失败记录并以非零状态退出；
- 应用启动时仍由 readiness 检查数据库是否处于单一 head，迁移未完成时业务
  请求被 ``MigrationGateMiddleware`` 拒绝。
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from app.config import Settings
from app.db.database import create_engine_from_settings
from app.db.migrations import VERSION_TABLE, alembic_config
from app.services.app_lock import AppLock, AppLockBusyError

logger = logging.getLogger(__name__)

MIGRATION_LOG = "migration-log.jsonl"
_MAX_LOG_RECORDS = 200


class MigrationError(RuntimeError):
    """迁移失败；消息包含失败阶段，不包含数据库内容或密码。"""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True, slots=True)
class MigrationResult:
    from_revision: str | None
    to_revision: str | None
    started_at: datetime
    finished_at: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def run_migrations(settings: Settings) -> MigrationResult:
    settings.prepare_directories()
    lock = AppLock(settings.data_dir)
    try:
        lock.acquire()
    except AppLockBusyError as exc:
        raise MigrationError("lock", "应用正在运行，请先停止 app 再执行迁移") from exc
    except OSError as exc:
        raise MigrationError("lock", "无法取得数据目录锁") from exc

    started_at = datetime.now(UTC)
    engine: Engine | None = None
    from_revision: str | None = None
    to_revision: str | None = None
    try:
        engine = create_engine_from_settings(settings)
        from_revision = _current_revision(engine)
        to_revision = _head_revision(settings)
        if to_revision is None:
            raise MigrationError("prepare", "未找到唯一的迁移 head")
        command.upgrade(alembic_config(settings), "head")
        finished_at = datetime.now(UTC)
        record = _record(
            from_revision=from_revision,
            to_revision=to_revision,
            started_at=started_at,
            finished_at=finished_at,
            status="SUCCESS",
        )
        _append_log(settings, record)
        logger.info(
            "database migration succeeded from=%s to=%s duration_seconds=%.3f",
            from_revision,
            to_revision,
            (finished_at - started_at).total_seconds(),
        )
        return MigrationResult(
            from_revision=from_revision,
            to_revision=to_revision,
            started_at=started_at,
            finished_at=finished_at,
        )
    except Exception as exc:
        finished_at = datetime.now(UTC)
        stage = getattr(exc, "stage", "upgrade")
        record = _record(
            from_revision=from_revision,
            to_revision=to_revision,
            started_at=started_at,
            finished_at=finished_at,
            status="FAILED",
        )
        record["stage"] = stage
        record["error"] = _safe_error(exc)
        _append_log(settings, record)
        logger.error("database migration failed stage=%s error=%s", stage, _safe_error(exc))
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError("upgrade", f"数据库迁移失败：{_safe_error(exc)}") from exc
    finally:
        if engine is not None:
            engine.dispose()
        lock.release()


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"version_table": VERSION_TABLE})
        return context.get_current_revision()


def _head_revision(settings: Settings) -> str | None:
    script = ScriptDirectory.from_config(alembic_config(settings))
    heads = script.get_heads()
    return heads[0] if len(heads) == 1 else None


def _record(
    *,
    from_revision: str | None,
    to_revision: str | None,
    started_at: datetime,
    finished_at: datetime,
    status: str,
) -> dict[str, object]:
    return {
        "from_revision": from_revision,
        "to_revision": to_revision,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "status": status,
    }


def _append_log(settings: Settings, record: dict[str, object]) -> None:
    path = settings.data_dir / MIGRATION_LOG
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
        path.chmod(0o600)
        _trim_log(path)
    except OSError:
        logger.exception("migration log could not be written")


def _trim_log(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_LOG_RECORDS:
            path.write_text("\n".join(lines[-_MAX_LOG_RECORDS:]) + "\n", encoding="utf-8")
            path.chmod(0o600)
    except OSError:
        logger.exception("migration log rotation failed")


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip()
    return message[:300] if message else exc.__class__.__name__
