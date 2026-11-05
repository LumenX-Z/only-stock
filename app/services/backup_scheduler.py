"""每日备份调度器。

依据 docs/13-Docker部署与运维.md 10.2、10.6：单进程内由 APScheduler 在
``BACKUP_TIME``（默认 02:00，使用配置时区）执行一次完整在线备份。手工备份与
定时备份共用 ``create_backup``，因此完整性校验、manifest、失败记录和保留集
规则完全一致。任务异常只记录日志，不使调度器停止。
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.modules.backups.service import create_backup

logger = logging.getLogger(__name__)

DAILY_BACKUP_JOB_ID = "daily-backup"


class BackupScheduler:
    def __init__(
        self,
        timezone_name: str,
        *,
        settings: Settings | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self._timezone = ZoneInfo(timezone_name)
        self._scheduler = BackgroundScheduler(timezone=self._timezone)
        self._settings = settings
        self._session_factory = session_factory

    @property
    def running(self) -> bool:
        return bool(self._scheduler.running)

    @property
    def next_run_time(self) -> datetime | None:
        job = self._scheduler.get_job(DAILY_BACKUP_JOB_ID)
        return job.next_run_time if job is not None else None

    def start(self) -> None:
        if self._scheduler.running:
            return
        if self._settings is not None and self._session_factory is not None:
            self._scheduler.add_job(
                self._run_daily_backup,
                trigger="cron",
                hour=self._settings.backup_time.hour,
                minute=self._settings.backup_time.minute,
                timezone=self._timezone,
                id=DAILY_BACKUP_JOB_ID,
                name="每日完整在线备份",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
        self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def _run_daily_backup(self) -> None:
        if self._settings is None or self._session_factory is None:
            return
        try:
            info = create_backup(
                self._settings,
                self._session_factory,
                actor_user_id=None,
                request_id=None,
                source="API",
            )
        except Exception:
            # create_backup 已记录失败集；定时任务只补充日志，不中断后续调度。
            logger.exception("scheduled backup failed")
        else:
            logger.info("scheduled backup succeeded backup_set_id=%s", info.id)
