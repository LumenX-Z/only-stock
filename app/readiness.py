"""就绪检查：数据库、迁移、数据目录与备份调度状态。

依据 docs/09-HTTP-API通用约定与健康检查.md 8.7 与
docs/14-安全、可靠性与性能要求.md 11.2。检查结果只返回检查项名称，
不返回路径、SQL 或异常消息，避免通过匿名健康检查泄露部署信息。
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from app.config import Settings
from app.db.migrations import MigrationChecker
from app.services.backup_scheduler import BackupScheduler

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    checks: Mapping[str, bool]

    @property
    def ready(self) -> bool:
        return all(self.checks.values())

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(name for name, passed in self.checks.items() if not passed)


class ReadinessProbe:
    def __init__(
        self,
        settings: Settings,
        engine: Engine,
        scheduler: BackupScheduler,
        migration_checker: MigrationChecker,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._scheduler = scheduler
        self._migration_checker = migration_checker

    def check(self) -> ReadinessResult:
        database_ok = self._database_ok()
        checks = {
            "data_dir": _directory_writable(self._settings.data_dir),
            "backup_dir": _directory_writable(self._settings.backup_dir),
            "database": database_ok,
            "migrations": database_ok and self._migrations_ok(),
            "backup_scheduler": self._scheduler.running,
        }
        return ReadinessResult(checks=checks)

    def _database_ok(self) -> bool:
        try:
            with self._engine.connect() as connection:
                selected = connection.exec_driver_sql("SELECT 1").scalar()
                foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
                journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
            return selected == 1 and foreign_keys == 1 and str(journal_mode).lower() == "wal"
        except Exception as exc:
            logger.warning("readiness database check failed error=%s", type(exc).__name__)
            return False

    def _migrations_ok(self) -> bool:
        try:
            return self._migration_checker.is_current(self._engine)
        except Exception as exc:
            logger.warning("readiness migration check failed error=%s", type(exc).__name__)
            return False


def _directory_writable(directory: Path) -> bool:
    return directory.is_dir() and os.access(directory, os.W_OK | os.X_OK)
