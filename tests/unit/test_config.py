from pathlib import Path

import pytest

from app.config import DATABASE_FILENAME, PROJECT_ROOT, ConfigError, Settings


def test_defaults_follow_documented_environment() -> None:
    settings = Settings.from_env({})

    assert settings.app_env == "development"
    assert settings.data_dir == (PROJECT_ROOT / "data").resolve()
    assert settings.backup_dir == (PROJECT_ROOT / "backups").resolve()
    assert settings.database_path == settings.data_dir / DATABASE_FILENAME
    assert settings.database_url == f"sqlite+pysqlite:///{settings.database_path.as_posix()}"
    assert settings.backup_time.isoformat() == "02:00:00"
    assert settings.backup_retention_sets == 7
    assert settings.session_idle_minutes == 480
    assert settings.session_absolute_hours == 24
    assert settings.mcp_key_rate_per_minute == 60
    assert settings.mcp_ip_rate_per_minute == 120
    assert settings.mcp_allowed_hosts == ("127.0.0.1", "localhost")
    assert settings.timezone_name == "Asia/Shanghai"
    assert settings.bootstrap_admin_username is None
    assert settings.bootstrap_admin_password is None


def test_relative_directories_are_resolved_under_project_root() -> None:
    settings = Settings.from_env({"DATA_DIR": "./var/data", "BACKUP_DIR": "./var/backups"})

    assert settings.data_dir == (PROJECT_ROOT / "var/data").resolve()
    assert settings.backup_dir == (PROJECT_ROOT / "var/backups").resolve()


def test_invalid_app_env_is_rejected() -> None:
    with pytest.raises(ConfigError, match="APP_ENV"):
        Settings.from_env({"APP_ENV": "staging"})


@pytest.mark.parametrize(
    "name",
    [
        "BACKUP_RETENTION_SETS",
        "SESSION_IDLE_MINUTES",
        "SESSION_ABSOLUTE_HOURS",
        "MCP_KEY_RATE_PER_MINUTE",
        "MCP_IP_RATE_PER_MINUTE",
    ],
)
def test_invalid_integer_names_variable(name: str) -> None:
    with pytest.raises(ConfigError, match=name):
        Settings.from_env({name: "not-a-number"})


def test_backup_time_must_use_two_digit_24_hour_format() -> None:
    assert Settings.from_env({"BACKUP_TIME": "23:59"}).backup_time.isoformat() == "23:59:00"
    for value in ("2:00", "24:00", "02:60", "02:00:00"):
        with pytest.raises(ConfigError, match="BACKUP_TIME"):
            Settings.from_env({"BACKUP_TIME": value})


def test_idle_session_cannot_exceed_absolute_session() -> None:
    with pytest.raises(ConfigError, match="SESSION_IDLE_MINUTES"):
        Settings.from_env({"SESSION_IDLE_MINUTES": "1441", "SESSION_ABSOLUTE_HOURS": "24"})


def test_bootstrap_admin_requires_username_and_password_together() -> None:
    with pytest.raises(ConfigError, match="BOOTSTRAP_ADMIN_USERNAME"):
        Settings.from_env({"BOOTSTRAP_ADMIN_USERNAME": "admin"})
    with pytest.raises(ConfigError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        Settings.from_env({"BOOTSTRAP_ADMIN_PASSWORD": "a" * 12})


def test_bootstrap_admin_password_minimum_length_and_no_echo() -> None:
    password = "short-pass"
    with pytest.raises(ConfigError) as error:
        Settings.from_env(
            {"BOOTSTRAP_ADMIN_USERNAME": "admin", "BOOTSTRAP_ADMIN_PASSWORD": password}
        )
    assert password not in str(error.value)


def test_repr_does_not_expose_bootstrap_credentials() -> None:
    password = "correct-horse-battery"
    settings = Settings.from_env(
        {"BOOTSTRAP_ADMIN_USERNAME": "admin", "BOOTSTRAP_ADMIN_PASSWORD": password}
    )

    assert password not in repr(settings)
    assert "bootstrap_admin_password" not in repr(settings)
    assert "bootstrap_admin_username" not in repr(settings)


def test_mcp_allowed_hosts_are_normalized_and_deduplicated() -> None:
    settings = Settings.from_env({"MCP_ALLOWED_HOSTS": " LOCALHOST, 127.0.0.1,localhost "})

    assert settings.mcp_allowed_hosts == ("localhost", "127.0.0.1")


def test_mcp_allowed_hosts_reject_empty_and_wildcard() -> None:
    with pytest.raises(ConfigError, match="MCP_ALLOWED_HOSTS"):
        Settings.from_env({"MCP_ALLOWED_HOSTS": " , "})
    with pytest.raises(ConfigError, match="MCP_ALLOWED_HOSTS"):
        Settings.from_env({"MCP_ALLOWED_HOSTS": "127.0.0.1,*"})


def test_timezone_must_be_valid() -> None:
    with pytest.raises(ConfigError, match="TZ"):
        Settings.from_env({"TZ": "Mars/Base"})


def test_empty_data_dir_is_rejected() -> None:
    with pytest.raises(ConfigError, match="DATA_DIR"):
        Settings.from_env({"DATA_DIR": "   "})


def test_prepare_directories_creates_data_and_backup_directories(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path / "data"), "BACKUP_DIR": str(tmp_path / "backups")}
    )

    settings.prepare_directories()

    assert settings.data_dir.is_dir()
    assert settings.backup_dir.is_dir()
    assert not settings.database_path.exists()


def test_prepare_directories_rejects_path_that_is_a_file(tmp_path: Path) -> None:
    data_path = tmp_path / "data"
    data_path.write_text("not a directory", encoding="utf-8")
    settings = Settings.from_env(
        {"DATA_DIR": str(data_path), "BACKUP_DIR": str(tmp_path / "backups")}
    )

    with pytest.raises(ConfigError, match="DATA_DIR"):
        settings.prepare_directories()


def test_test_settings_are_isolated_under_tmp_path(settings: Settings, tmp_path: Path) -> None:
    assert settings.data_dir.is_relative_to(tmp_path)
    assert settings.data_dir != (PROJECT_ROOT / "data").resolve()
