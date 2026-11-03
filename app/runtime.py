"""应用运行时对象：配置、引擎、会话工厂、调度器与就绪检查器。"""

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import PROJECT_ROOT, Settings
from app.db.database import create_engine_from_settings, create_session_factory
from app.db.migrations import MigrationChecker
from app.readiness import ReadinessProbe
from app.services.backup_scheduler import BackupScheduler


@dataclass(slots=True)
class Runtime:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    scheduler: BackupScheduler
    readiness: ReadinessProbe


def build_runtime(settings: Settings) -> Runtime:
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    migration_checker = MigrationChecker(PROJECT_ROOT / "alembic.ini")
    scheduler = BackupScheduler(settings.timezone_name)
    readiness = ReadinessProbe(settings, engine, scheduler, migration_checker)
    return Runtime(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        scheduler=scheduler,
        readiness=readiness,
    )
