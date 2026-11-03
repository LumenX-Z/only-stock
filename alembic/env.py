"""Alembic 环境。

数据库 URL 统一来自 ``app.config.Settings``，版本表使用
docs/05-数据模型与实体约束.md 6.2 指定的 ``schema_migrations``。
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import Settings
from app.db.base import Base
from app.db.migrations import VERSION_TABLE

config = context.config
settings = Settings.from_env()
settings.data_dir.mkdir(parents=True, exist_ok=True)
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
            version_table=VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
