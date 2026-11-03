"""Alembic 迁移状态检查，供启动自检和 /health/ready 使用。

版本表使用 docs/05-数据模型与实体约束.md 6.2 指定的 ``schema_migrations``，
而不是 Alembic 默认的 ``alembic_version``。
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from app.config import PROJECT_ROOT, Settings

VERSION_TABLE = "schema_migrations"


def alembic_config(settings: Settings) -> Config:
    """构造指向指定数据目录的 Alembic 配置，不依赖进程环境变量。"""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    return config


def upgrade_to_head(settings: Settings) -> None:
    """对指定设置执行升级到单一 head；供部署脚本和测试使用。"""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(settings), "head")


class MigrationChecker:
    """读取迁移脚本 head，并与数据库当前版本比较。"""

    def __init__(self, alembic_ini: Path) -> None:
        self._configured = False
        self._multiple_heads = False
        self._heads: frozenset[str] = frozenset()

        if not alembic_ini.is_file():
            return

        script = ScriptDirectory.from_config(Config(str(alembic_ini)))
        heads = script.get_heads()
        self._heads = frozenset(heads)
        self._multiple_heads = len(heads) > 1
        self._configured = True

    def is_current(self, engine: Engine) -> bool:
        """无迁移脚本（P1）时视为已就绪；有脚本时要求数据库恰好处于单一 head。"""
        if not self._configured or self._multiple_heads:
            return False
        if not self._heads:
            return True

        inspector = inspect(engine)
        if not inspector.has_table(VERSION_TABLE):
            return False

        with engine.connect() as connection:
            current = (
                connection.execute(text(f"SELECT version_num FROM {VERSION_TABLE}")).scalars().all()
            )
        return set(current) == set(self._heads)
