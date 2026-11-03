from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.config import PROJECT_ROOT, ConfigError, Settings
from app.db.migrations import MigrationChecker
from app.main import create_app


def test_lifespan_prepares_directories_and_starts_scheduler(
    app: FastAPI, settings: Settings
) -> None:
    assert not settings.data_dir.exists()

    with TestClient(app) as client:
        assert settings.data_dir.is_dir()
        assert settings.backup_dir.is_dir()
        assert settings.database_path.is_file()
        assert app.state.runtime.scheduler.running
        assert client.get("/health/ready").status_code == 200

    assert not app.state.runtime.scheduler.running


def test_startup_fails_when_data_dir_is_a_file(tmp_path: Path) -> None:
    data_path = tmp_path / "data"
    data_path.write_text("not a directory", encoding="utf-8")
    settings = Settings.from_env(
        {
            "APP_ENV": "test",
            "DATA_DIR": str(data_path),
            "BACKUP_DIR": str(tmp_path / "backups"),
        }
    )
    app = create_app(settings)

    with pytest.raises(ConfigError, match="DATA_DIR"):
        with TestClient(app):
            pass


def test_no_migration_scripts_means_migrations_are_current(db_engine: Engine) -> None:
    checker = MigrationChecker(PROJECT_ROOT / "alembic.ini")

    assert checker.is_current(db_engine) is True
