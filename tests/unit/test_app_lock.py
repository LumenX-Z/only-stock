"""应用运行锁：排他、释放与探测。"""

from pathlib import Path

import pytest

from app.services.app_lock import AppLock, AppLockBusyError


def test_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    first = AppLock(tmp_path)
    second = AppLock(tmp_path)

    first.acquire()
    try:
        assert first.held
        assert AppLock.is_held(tmp_path)
        with pytest.raises(AppLockBusyError):
            second.acquire()
    finally:
        first.release()

    assert not first.held
    assert not AppLock.is_held(tmp_path)
    second.acquire()
    second.release()


def test_acquire_is_idempotent_for_same_instance(tmp_path: Path) -> None:
    lock = AppLock(tmp_path)

    lock.acquire()
    lock.acquire()
    lock.release()

    assert not lock.held


def test_lock_file_is_created_in_data_directory(tmp_path: Path) -> None:
    lock = AppLock(tmp_path)

    lock.acquire()
    try:
        assert lock.path == tmp_path / "app.lock"
        assert lock.path.is_file()
    finally:
        lock.release()
