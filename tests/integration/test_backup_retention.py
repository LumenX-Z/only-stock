"""备份保留集、失败记录与清理失败隔离。

依据 docs/13-Docker部署与运维.md 10.6 与 docs/16 ACC-OPS-004：
手动与定时备份同等计入最近 N 个成功集；失败不计入；清理失败不影响新备份。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

import app.modules.backups.service as backup_service
from app.config import Settings
from app.errors import AppError
from app.modules.backups.service import create_backup, list_backups
from app.services.backup_scheduler import BackupScheduler


class FakeClock(datetime):
    current = datetime(2026, 1, 1, tzinfo=UTC)

    @classmethod
    def now(cls, tz: object = None) -> "FakeClock":
        value = cls.current
        cls.current = value + timedelta(seconds=1)
        if tz is None:
            return value.replace(tzinfo=None)  # type: ignore[return-value]
        return value.astimezone(tz)  # type: ignore[arg-type, return-value]


def _create(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    source: str = "API",
) -> str:
    info = create_backup(
        settings,
        session_factory,
        actor_user_id=None,
        request_id=None,
        source=source,
    )
    return info.id


def _success_dirs(settings: Settings) -> list[Path]:
    return sorted(
        child
        for child in settings.backup_dir.iterdir()
        if child.is_dir() and not child.name.startswith(".") and (child / "manifest.json").is_file()
    )


def test_keeps_only_latest_success_sets(
    settings: Settings,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_service, "datetime", FakeClock)
    created = [_create(settings, session_factory) for _ in range(9)]

    remaining = _success_dirs(settings)
    assert len(remaining) == settings.backup_retention_sets == 7
    assert [path.name for path in remaining] == sorted(created[-7:])


def test_manual_and_scheduled_backups_share_retention(
    env: dict[str, str],
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.from_env({**env, "BACKUP_RETENTION_SETS": "2"})
    monkeypatch.setattr(backup_service, "datetime", FakeClock)
    first = _create(settings, session_factory)
    second = _create(settings, session_factory)
    scheduler = BackupScheduler(
        settings.timezone_name,
        settings=settings,
        session_factory=session_factory,
    )
    scheduler._run_daily_backup()

    remaining = _success_dirs(settings)
    assert len(remaining) == 2
    assert first not in {path.name for path in remaining}
    assert second in {path.name for path in remaining}
    assert any(path.name.startswith("2026") for path in remaining)


def test_retention_cleanup_failure_does_not_remove_new_backup(
    settings: Settings,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_service, "datetime", FakeClock)
    created = [_create(settings, session_factory) for _ in range(7)]
    oldest = created[0]
    original_rmdir = Path.rmdir

    def failing_rmdir(path: Path) -> None:
        if path.name == oldest:
            raise OSError("模拟清理失败")
        original_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", failing_rmdir)
    newest = _create(settings, session_factory)

    assert (settings.backup_dir / newest / "manifest.json").is_file()
    assert (settings.backup_dir / oldest).is_dir()


def test_failed_backup_is_recorded_but_not_counted_as_restorable(
    settings: Settings,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_copy(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("模拟复制失败")

    settings.prepare_directories()
    original_copy = backup_service._copy_database
    monkeypatch.setattr(backup_service, "_copy_database", fail_copy)
    with pytest.raises(AppError) as error:
        _create(settings, session_factory)
    assert error.value.code == "BACKUP_FAILED"
    monkeypatch.setattr(backup_service, "_copy_database", original_copy)

    failures = list((settings.backup_dir / "failures").glob("*.json"))
    assert len(failures) == 1
    assert _success_dirs(settings) == []

    for _ in range(7):
        _create(settings, session_factory)
    items, total = list_backups(settings, page=1, page_size=20)
    assert total == 8
    assert sum(1 for item in items if item.status == "SUCCESS") == 7
    assert sum(1 for item in items if item.status == "FAILED") == 1
    assert len(_success_dirs(settings)) == 7


def test_failed_integrity_check_creates_no_success_set(
    settings: Settings,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_service, "integrity_check", lambda _path: "damaged")

    with pytest.raises(AppError):
        _create(settings, session_factory)

    assert _success_dirs(settings) == []
    assert not any(path.name.startswith(".tmp-") for path in settings.backup_dir.iterdir())
    assert len(list((settings.backup_dir / "failures").glob("*.json"))) == 1
