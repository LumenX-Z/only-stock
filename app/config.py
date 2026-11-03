"""集中配置与启动校验。

依据：
- docs/13-Docker部署与运维.md 10.1、10.2、10.4（环境变量与服务形态）
- docs/04-认证、会话与凭证安全.md 5.1、5.4（单一数据库、密码规则）
- docs/14-安全、可靠性与性能要求.md 11.2（启动自检）

配置错误不得包含密码等敏感值；目录只允许来自服务端环境变量。
"""

import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_FILENAME = "inventory.sqlite3"

_APP_ENVS = frozenset({"development", "test", "production"})
_BACKUP_TIME_PATTERN = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$")


class ConfigError(Exception):
    """环境变量或目录配置不可用；消息不得包含密码。"""


@dataclass(frozen=True, slots=True)
class Settings:
    """一次启动使用的不可变配置。"""

    app_env: str
    data_dir: Path
    backup_dir: Path
    backup_time: time
    backup_retention_sets: int
    session_idle_minutes: int
    session_absolute_hours: int
    mcp_key_rate_per_minute: int
    mcp_ip_rate_per_minute: int
    mcp_allowed_hosts: tuple[str, ...]
    timezone_name: str
    bootstrap_admin_username: str | None = field(default=None, repr=False)
    bootstrap_admin_password: str | None = field(default=None, repr=False)
    sqlite_busy_timeout_ms: int = 5000

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """从环境变量构造配置；不创建目录、不连接数据库。"""
        env = os.environ if environ is None else environ

        app_env = env.get("APP_ENV", "development").strip().lower()
        if app_env not in _APP_ENVS:
            raise ConfigError("APP_ENV 必须是 development、test 或 production")

        data_dir = _directory_value(env, "DATA_DIR", "./data")
        backup_dir = _directory_value(env, "BACKUP_DIR", "./backups")
        backup_time = _time_value(env.get("BACKUP_TIME", "02:00"))

        backup_retention_sets = _int_value(env, "BACKUP_RETENTION_SETS", 7)
        session_idle_minutes = _int_value(env, "SESSION_IDLE_MINUTES", 480)
        session_absolute_hours = _int_value(env, "SESSION_ABSOLUTE_HOURS", 24)
        if session_idle_minutes > session_absolute_hours * 60:
            raise ConfigError("SESSION_IDLE_MINUTES 不能大于 SESSION_ABSOLUTE_HOURS 对应的分钟数")

        mcp_key_rate = _int_value(env, "MCP_KEY_RATE_PER_MINUTE", 60)
        mcp_ip_rate = _int_value(env, "MCP_IP_RATE_PER_MINUTE", 120)
        mcp_allowed_hosts = _hosts_value(env.get("MCP_ALLOWED_HOSTS", "127.0.0.1,localhost"))

        timezone_name = env.get("TZ", "Asia/Shanghai").strip() or "Asia/Shanghai"
        _validate_timezone(timezone_name)

        username = env.get("BOOTSTRAP_ADMIN_USERNAME", "").strip() or None
        password = env.get("BOOTSTRAP_ADMIN_PASSWORD", "") or None
        if (username is None) != (password is None):
            raise ConfigError(
                "BOOTSTRAP_ADMIN_USERNAME 与 BOOTSTRAP_ADMIN_PASSWORD 必须同时设置或同时留空"
            )
        if password is not None and len(password) < 12:
            raise ConfigError("BOOTSTRAP_ADMIN_PASSWORD 至少 12 位")

        return cls(
            app_env=app_env,
            data_dir=data_dir,
            backup_dir=backup_dir,
            backup_time=backup_time,
            backup_retention_sets=backup_retention_sets,
            session_idle_minutes=session_idle_minutes,
            session_absolute_hours=session_absolute_hours,
            mcp_key_rate_per_minute=mcp_key_rate,
            mcp_ip_rate_per_minute=mcp_ip_rate,
            mcp_allowed_hosts=mcp_allowed_hosts,
            timezone_name=timezone_name,
            bootstrap_admin_username=username,
            bootstrap_admin_password=password,
        )

    @property
    def database_path(self) -> Path:
        """唯一数据库文件；文件名固定，不可由客户端或环境变量覆盖。"""
        return self.data_dir / DATABASE_FILENAME

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.database_path.as_posix()}"

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def prepare_directories(self) -> None:
        """创建并验证数据、备份目录；在应用启动时调用，不写入业务数据。"""
        for variable, directory in (("DATA_DIR", self.data_dir), ("BACKUP_DIR", self.backup_dir)):
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ConfigError(f"{variable} 目录不可用") from exc
            if not directory.is_dir():
                raise ConfigError(f"{variable} 必须是目录")
            _verify_directory_writable(variable, directory)


def _directory_value(environ: Mapping[str, str], name: str, default: str) -> Path:
    raw = environ.get(name, default)
    if not raw.strip():
        raise ConfigError(f"{name} 不能为空")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _int_value(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} 必须是整数") from exc
    if value < 1:
        raise ConfigError(f"{name} 必须大于等于 1")
    return value


def _time_value(raw: str) -> time:
    match = _BACKUP_TIME_PATTERN.fullmatch(raw.strip())
    if match is None:
        raise ConfigError("BACKUP_TIME 必须使用 24 小时制 HH:MM 格式")
    return time(int(match.group("hour")), int(match.group("minute")))


def _hosts_value(raw: str) -> tuple[str, ...]:
    hosts = tuple(dict.fromkeys(part.strip().lower() for part in raw.split(",") if part.strip()))
    if not hosts:
        raise ConfigError("MCP_ALLOWED_HOSTS 不能为空")
    if "*" in hosts:
        raise ConfigError("MCP_ALLOWED_HOSTS 不允许使用通配符 *")
    return hosts


def _validate_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("TZ 必须是有效的 IANA 时区名称") from exc


def _verify_directory_writable(variable: str, directory: Path) -> None:
    try:
        descriptor, probe_path = tempfile.mkstemp(prefix=".only-stock-write-", dir=directory)
        os.close(descriptor)
        Path(probe_path).unlink()
    except OSError as exc:
        raise ConfigError(f"{variable} 目录不可写") from exc
