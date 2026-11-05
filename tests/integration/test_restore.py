"""离线恢复与回滚：校验、安装、恢复区、拒绝路径与 CLI 退出码。"""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.cli import main
from app.config import Settings
from app.db.database import create_engine_from_settings, create_session_factory
from app.db.migrations import upgrade_to_head
from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.main import create_app
from app.modules.backups.restore import RestoreError, restore_backup_set, rollback_from
from app.modules.backups.service import create_backup
from app.modules.users.models import Role, User
from app.services.app_lock import AppLock


def _prepare(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    upgrade_to_head(settings)
    engine = create_engine_from_settings(settings)
    return engine, create_session_factory(engine)


def _add_user(session_factory: sessionmaker[Session], login_name: str) -> None:
    now = utc_now()
    with session_factory() as session, begin_immediate(session):
        session.add(
            User(
                login_name=login_name,
                password_hash="x" * 32,
                display_name=login_name,
                role=Role.OPERATOR.value,
                status="ACTIVE",
                force_password_change=False,
                password_version=1,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )


def _backup(settings: Settings, session_factory: sessionmaker[Session]) -> str:
    info = create_backup(
        settings,
        session_factory,
        actor_user_id=None,
        request_id=None,
        source="API",
    )
    return info.id


def _count_users(database_path: Path) -> int:
    connection = sqlite3.connect(database_path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _close_and_checkpoint(engine: Engine, settings: Settings) -> None:
    engine.dispose()
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def _set_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("BACKUP_DIR", str(settings.backup_dir))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "")


def test_restore_installs_verified_backup_and_moves_previous_database(settings: Settings) -> None:
    engine, session_factory = _prepare(settings)
    _add_user(session_factory, "first")
    backup_id = _backup(settings, session_factory)
    _add_user(session_factory, "second")
    assert _count_users(settings.database_path) == 2
    _close_and_checkpoint(engine, settings)

    database_path = settings.database_path
    database_path.with_name(database_path.name + "-wal").write_bytes(b"old-wal")
    database_path.with_name(database_path.name + "-shm").write_bytes(b"old-shm")

    result = restore_backup_set(settings, backup_id)

    assert result.action == "restore"
    assert result.backup_set_id == backup_id
    assert result.integrity == "ok"
    assert _count_users(database_path) == 1
    assert database_path.stat().st_mode & 0o777 == 0o600
    assert not database_path.with_name(database_path.name + "-wal").exists()
    assert not database_path.with_name(database_path.name + "-shm").exists()

    recovery_dir = result.recovery_dir
    assert recovery_dir.parent.name == "recovery-pre"
    assert (recovery_dir / "inventory.sqlite3").is_file()
    assert (recovery_dir / "inventory.sqlite3-wal").read_bytes() == b"old-wal"
    assert (recovery_dir / "inventory.sqlite3-shm").read_bytes() == b"old-shm"
    report = json.loads((recovery_dir / "restore-report.json").read_text(encoding="utf-8"))
    assert report["action"] == "restore"
    assert report["backup_set_id"] == backup_id
    assert report["integrity_check"] == "ok"


def test_restore_rejects_tampered_backup_without_touching_current_database(
    settings: Settings,
) -> None:
    engine, session_factory = _prepare(settings)
    _add_user(session_factory, "first")
    backup_id = _backup(settings, session_factory)
    _close_and_checkpoint(engine, settings)
    before = _sha256(settings.database_path)

    backup_file = settings.backup_dir / backup_id / "inventory.sqlite3"
    backup_file.write_bytes(backup_file.read_bytes() + b"tampered")

    with pytest.raises(RestoreError) as error:
        restore_backup_set(settings, backup_id)
    assert error.value.stage == "validate"
    assert _sha256(settings.database_path) == before
    assert not (settings.data_dir / "recovery-pre").exists()


def test_restore_rejects_invalid_backup_set_id(settings: Settings) -> None:
    engine, _session_factory = _prepare(settings)
    try:
        for value in ("../escape", "20260101T000000Z-zzzzzzzz", ""):
            with pytest.raises(RestoreError) as error:
                restore_backup_set(settings, value)
            assert error.value.stage == "validate"
    finally:
        engine.dispose()


def test_restore_refuses_when_application_is_running(settings: Settings) -> None:
    engine, session_factory = _prepare(settings)
    backup_id = _backup(settings, session_factory)
    _close_and_checkpoint(engine, settings)

    app = create_app(settings)
    with TestClient(app):
        with pytest.raises(RestoreError) as error:
            restore_backup_set(settings, backup_id)
        assert error.value.stage == "lock"
        assert "停止 app" in str(error.value)


def test_restore_requires_explicit_yes(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_factory = _prepare(settings)
    backup_id = _backup(settings, session_factory)
    _close_and_checkpoint(engine, settings)
    before = _sha256(settings.database_path)
    _set_env(monkeypatch, settings)

    assert main(["restore", "--backup-set", backup_id]) == 2
    assert _sha256(settings.database_path) == before
    assert not (settings.data_dir / "recovery-pre").exists()


def test_cli_backup_list_and_restore(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine, session_factory = _prepare(settings)
    _add_user(session_factory, "first")
    backup_id = _backup(settings, session_factory)
    _add_user(session_factory, "second")
    _close_and_checkpoint(engine, settings)
    _set_env(monkeypatch, settings)

    assert main(["backup", "list"]) == 0
    output = capsys.readouterr().out
    assert backup_id in output
    assert "SUCCESS" in output

    assert main(["restore", "--backup-set", backup_id, "--yes"]) == 0
    output = capsys.readouterr().out
    assert "恢复完成" in output
    assert _count_users(settings.database_path) == 1


def test_rollback_from_recovery_directory(settings: Settings) -> None:
    engine, session_factory = _prepare(settings)
    _add_user(session_factory, "first")
    backup_id = _backup(settings, session_factory)
    _add_user(session_factory, "second")
    _close_and_checkpoint(engine, settings)

    first_restore = restore_backup_set(settings, backup_id)
    assert _count_users(settings.database_path) == 1

    rollback = rollback_from(settings, first_restore.recovery_dir)

    assert rollback.action == "rollback"
    assert rollback.backup_set_id is None
    assert _count_users(settings.database_path) == 2
    assert rollback.recovery_dir != first_restore.recovery_dir
    report = json.loads((rollback.recovery_dir / "restore-report.json").read_text(encoding="utf-8"))
    assert report["action"] == "rollback"
    assert report["rollback_from"] == str(first_restore.recovery_dir)


def test_rollback_rejects_paths_outside_recovery_pre(settings: Settings) -> None:
    engine, _session_factory = _prepare(settings)
    engine.dispose()
    outside = settings.data_dir / "elsewhere"
    outside.mkdir()

    with pytest.raises(RestoreError) as error:
        rollback_from(settings, outside)
    assert error.value.stage == "validate"


def test_backup_list_empty_and_invalid_restore_exit_codes(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_env(monkeypatch, settings)
    settings.prepare_directories()

    assert main(["backup", "list"]) == 0
    assert "没有备份集" in capsys.readouterr().out
    assert main(["restore", "--backup-set", "not-an-id", "--yes"]) == 1
    captured = capsys.readouterr()
    assert "validate" in captured.err


def test_restore_lock_is_released_after_failure(settings: Settings) -> None:
    engine, session_factory = _prepare(settings)
    backup_id = _backup(settings, session_factory)
    _close_and_checkpoint(engine, settings)
    backup_file = settings.backup_dir / backup_id / "inventory.sqlite3"
    backup_file.write_bytes(b"broken")

    with pytest.raises(RestoreError):
        restore_backup_set(settings, backup_id)
    assert not AppLock.is_held(settings.data_dir)
