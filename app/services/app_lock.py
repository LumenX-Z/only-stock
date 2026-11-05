"""应用运行锁。

依据 docs/13-Docker部署与运维.md 10.5、10.7：应用启动时对数据目录内的
``app.lock`` 持有排他锁；迁移执行器和离线恢复 CLI 在锁被占用时拒绝执行，
保证升级与恢复确实发生在应用停止之后。锁使用 ``flock``，进程退出后由操作
系统释放，不依赖可能过期的 PID 文件。
"""

import fcntl
import os
from pathlib import Path

LOCK_FILENAME = "app.lock"


class AppLockBusyError(RuntimeError):
    """同一数据目录已有进程持有运行锁。"""

    def __init__(self, message: str = "应用正在运行或已有独占运维任务") -> None:
        super().__init__(message)


class AppLock:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._descriptor: int | None = None

    @property
    def path(self) -> Path:
        return self._data_dir / LOCK_FILENAME

    @property
    def held(self) -> bool:
        return self._descriptor is not None

    def acquire(self) -> None:
        if self._descriptor is not None:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise AppLockBusyError() from exc
        self._descriptor = descriptor

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @classmethod
    def is_held(cls, data_dir: Path) -> bool:
        """探测锁是否被其他进程持有；不改变持锁进程状态。"""
        path = data_dir / LOCK_FILENAME
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        except OSError:
            return False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            os.close(descriptor)
        return False
