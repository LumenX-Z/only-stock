"""每日备份调度器的生命周期封装。

P1 只负责启动/停止与就绪状态；具体备份任务、保留策略和 manifest 校验
按 TASKS.md P9 与 docs/13-Docker部署与运维.md 10.6 补齐。
"""

from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler


class BackupScheduler:
    def __init__(self, timezone_name: str) -> None:
        self._scheduler = BackgroundScheduler(timezone=ZoneInfo(timezone_name))

    @property
    def running(self) -> bool:
        return bool(self._scheduler.running)

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
