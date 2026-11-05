"""每日备份调度器：任务注册、执行与失败隔离。"""

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.modules.backups.service import list_backups
from app.services.backup_scheduler import DAILY_BACKUP_JOB_ID, BackupScheduler


def test_scheduler_registers_daily_job_at_configured_time(
    migrated: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    scheduler = BackupScheduler(
        migrated.timezone_name,
        settings=migrated,
        session_factory=session_factory,
    )
    try:
        scheduler.start()
        next_run = scheduler.next_run_time
        assert next_run is not None
        assert next_run.hour == migrated.backup_time.hour == 2
        assert next_run.minute == migrated.backup_time.minute == 0
        assert next_run.tzinfo is not None
        assert scheduler._scheduler.get_job(DAILY_BACKUP_JOB_ID) is not None
    finally:
        scheduler.shutdown()


def test_scheduled_backup_creates_success_set(
    migrated: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    scheduler = BackupScheduler(
        migrated.timezone_name,
        settings=migrated,
        session_factory=session_factory,
    )
    scheduler._run_daily_backup()

    items, total = list_backups(migrated, page=1, page_size=10)
    assert total == 1
    assert items[0].status == "SUCCESS"
    assert items[0].integrity == "ok"
    assert (migrated.backup_dir / items[0].id / "manifest.json").is_file()


def test_scheduled_backup_failure_is_logged_and_scheduler_survives(
    migrated: Settings,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("模拟定时备份失败")

    monkeypatch.setattr("app.services.backup_scheduler.create_backup", fail)
    scheduler = BackupScheduler(
        migrated.timezone_name,
        settings=migrated,
        session_factory=session_factory,
    )
    scheduler.start()
    try:
        scheduler._run_daily_backup()
        assert scheduler.running
        assert scheduler.next_run_time is not None
    finally:
        scheduler.shutdown()


def test_scheduler_without_runtime_still_starts() -> None:
    scheduler = BackupScheduler("Asia/Shanghai")
    scheduler.start()
    try:
        assert scheduler.running
        assert scheduler.next_run_time is None
    finally:
        scheduler.shutdown()
