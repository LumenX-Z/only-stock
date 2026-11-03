from pathlib import Path

from sqlalchemy import Engine

from app.db.migrations import MigrationChecker

_REVISION = """\
revision = "{revision}"
down_revision = {down_revision}
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
"""


def _write_alembic_project(tmp_path: Path, revisions: dict[str, str | None]) -> Path:
    versions = tmp_path / "alembic" / "versions"
    versions.mkdir(parents=True)
    for revision, down_revision in revisions.items():
        (versions / f"{revision}_test.py").write_text(
            _REVISION.format(revision=revision, down_revision=repr(down_revision)),
            encoding="utf-8",
        )
    ini = tmp_path / "alembic.ini"
    ini.write_text(
        f"[alembic]\nscript_location = {tmp_path / 'alembic'}\n",
        encoding="utf-8",
    )
    return ini


def test_migration_checker_requires_single_head_in_database(
    db_engine: Engine, tmp_path: Path
) -> None:
    checker = MigrationChecker(_write_alembic_project(tmp_path, {"0001": None}))

    assert checker.is_current(db_engine) is False

    with db_engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_migrations (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.exec_driver_sql("INSERT INTO schema_migrations (version_num) VALUES ('0001')")
        connection.commit()

    assert checker.is_current(db_engine) is True


def test_migration_checker_rejects_multiple_heads(db_engine: Engine, tmp_path: Path) -> None:
    checker = MigrationChecker(_write_alembic_project(tmp_path, {"0001": None, "0002": None}))

    assert checker.is_current(db_engine) is False


def test_missing_alembic_config_is_not_ready(db_engine: Engine, tmp_path: Path) -> None:
    checker = MigrationChecker(tmp_path / "missing.ini")

    assert checker.is_current(db_engine) is False
