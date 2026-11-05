"""唯一迁移执行器：升级、日志、失败记录与运行锁。"""

import json
from pathlib import Path

import pytest

from app.cli import main
from app.config import PROJECT_ROOT, Settings
from app.db.database import create_engine_from_settings
from app.db.migration_runner import MIGRATION_LOG, MigrationError, run_migrations
from app.db.migrations import MigrationChecker
from app.services.app_lock import AppLock


def _log_records(settings: Settings) -> list[dict[str, object]]:
    path = settings.data_dir / MIGRATION_LOG
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_migrations_upgrades_and_records_success(settings: Settings) -> None:
    result = run_migrations(settings)

    assert result.to_revision is not None
    assert result.from_revision is None
    assert result.duration_seconds >= 0
    engine = create_engine_from_settings(settings)
    try:
        assert MigrationChecker(_alembic_ini()).is_current(engine)
    finally:
        engine.dispose()
    records = _log_records(settings)
    assert records[-1]["status"] == "SUCCESS"
    assert records[-1]["to_revision"] == result.to_revision
    assert "started_at" in records[-1]
    assert "finished_at" in records[-1]


def test_run_migrations_is_idempotent(settings: Settings) -> None:
    first = run_migrations(settings)
    second = run_migrations(settings)

    assert second.to_revision == first.to_revision
    assert second.from_revision == first.to_revision
    assert [record["status"] for record in _log_records(settings)] == ["SUCCESS", "SUCCESS"]


def test_migration_failure_is_recorded_and_database_stays_unmigrated(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_upgrade(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("模拟迁移失败")

    monkeypatch.setattr("app.db.migration_runner.command.upgrade", fail_upgrade)

    with pytest.raises(MigrationError) as error:
        run_migrations(settings)
    assert error.value.stage == "upgrade"

    records = _log_records(settings)
    assert records[-1]["status"] == "FAILED"
    assert records[-1]["stage"] == "upgrade"
    assert "模拟迁移失败" in str(records[-1]["error"])
    engine = create_engine_from_settings(settings)
    try:
        assert MigrationChecker(_alembic_ini()).is_current(engine) is False
    finally:
        engine.dispose()


def test_migration_refuses_when_application_lock_is_held(settings: Settings) -> None:
    lock = AppLock(settings.data_dir)
    lock.acquire()
    try:
        with pytest.raises(MigrationError) as error:
            run_migrations(settings)
        assert error.value.stage == "lock"
    finally:
        lock.release()
    assert not (settings.data_dir / MIGRATION_LOG).exists()


def test_cli_migrate_uses_same_executor(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_env(monkeypatch, settings)

    assert main(["migrate"]) == 0
    assert "迁移完成" in capsys.readouterr().out
    engine = create_engine_from_settings(settings)
    try:
        assert MigrationChecker(_alembic_ini()).is_current(engine)
    finally:
        engine.dispose()


def test_cli_migrate_returns_nonzero_when_application_is_running(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_env(monkeypatch, settings)
    lock = AppLock(settings.data_dir)
    lock.acquire()
    try:
        assert main(["migrate"]) == 1
    finally:
        lock.release()
    captured = capsys.readouterr()
    assert "lock" in captured.err
    assert "停止 app" in captured.err


def _alembic_ini() -> Path:
    return PROJECT_ROOT / "alembic.ini"


def _set_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("BACKUP_DIR", str(settings.backup_dir))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "")
