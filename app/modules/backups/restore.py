"""离线恢复与回滚。

依据 docs/13-Docker部署与运维.md 10.7：

- 必须先校验 manifest、文件大小、SHA-256 与 ``PRAGMA integrity_check``，
  全部通过后才改动数据目录；
- 通过 ``app.lock`` 排他锁确认应用已停止；
- 将当前数据库及同目录 ``-wal``、``-shm`` 移动到
  ``<data>/recovery-pre/<UTC时间戳>/``，再原子安装备份文件；
- 安装失败时尽量把原数据库移回，并保证失败阶段可诊断；
- 回滚只接受 ``recovery-pre`` 下的完整数据库目录，不接受任意文件路径。
"""

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.modules.backups.storage import (
    DATABASE_FILE,
    INTEGRITY_REPORT_FILE,
    MANIFEST_FILE,
    RECOVERY_PRE_DIR,
    RESTORE_REPORT_FILE,
    integrity_check,
    sha256_file,
    utc_stamp,
    write_json,
)
from app.services.app_lock import AppLock, AppLockBusyError
from app.version import APP_VERSION

logger = logging.getLogger(__name__)

_BACKUP_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
_DATABASE_SUFFIXES = ("-wal", "-shm")


class RestoreError(RuntimeError):
    """恢复/回滚失败；``stage`` 标明失败阶段，消息不包含数据库内容。"""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True, slots=True)
class VerifiedBackup:
    backup_set_id: str
    database_path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RestoreResult:
    action: str
    backup_set_id: str | None
    restored_at: datetime
    recovery_dir: Path
    sha256: str
    integrity: str


def verify_backup_set(settings: Settings, backup_set_id: str) -> VerifiedBackup:
    """只读校验备份集；失败抛出 ``RestoreError``，不触碰数据目录。"""
    if not _BACKUP_ID_PATTERN.fullmatch(backup_set_id or ""):
        raise RestoreError("validate", "备份集 ID 格式无效")
    backup_root = settings.backup_dir.resolve()
    backup_dir = (backup_root / backup_set_id).resolve()
    if backup_dir.parent != backup_root or not backup_dir.is_dir():
        raise RestoreError("validate", "备份集不存在")

    manifest_path = backup_dir / MANIFEST_FILE
    if not manifest_path.is_file():
        raise RestoreError("validate", "备份集缺少 manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RestoreError("validate", "manifest.json 无法解析") from exc
    if not isinstance(manifest, dict):
        raise RestoreError("validate", "manifest.json 结构无效")
    if str(manifest.get("backup_set_id") or "") != backup_set_id:
        raise RestoreError("validate", "manifest 备份集 ID 不匹配")

    database_file = str(manifest.get("database_file") or "")
    if database_file != DATABASE_FILE:
        raise RestoreError("validate", "manifest 数据库文件名无效")
    database_path = (backup_dir / database_file).resolve()
    if database_path.parent != backup_dir or not database_path.is_file():
        raise RestoreError("validate", "备份数据库文件不存在")

    if str(manifest.get("integrity_check") or "") != "ok":
        raise RestoreError("validate", "manifest 记录完整性检查未通过")
    expected_size = manifest.get("size_bytes")
    expected_sha = str(manifest.get("sha256") or "")
    if not isinstance(expected_size, int) or len(expected_sha) != 64:
        raise RestoreError("validate", "manifest 文件大小或 SHA-256 无效")
    actual_size = database_path.stat().st_size
    if actual_size != expected_size:
        raise RestoreError("validate", "备份文件大小与 manifest 不一致")
    actual_sha = sha256_file(database_path)
    if actual_sha != expected_sha:
        raise RestoreError("validate", "备份文件 SHA-256 与 manifest 不一致")

    report_path = backup_dir / INTEGRITY_REPORT_FILE
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RestoreError("validate", "integrity-report.json 无法解析") from exc
        if isinstance(report, dict) and str(report.get("integrity_check") or "") != "ok":
            raise RestoreError("validate", "integrity-report.json 记录完整性检查未通过")

    integrity = integrity_check(database_path)
    if integrity != "ok":
        raise RestoreError("validate", "备份数据库完整性检查未通过")
    return VerifiedBackup(
        backup_set_id=backup_set_id,
        database_path=database_path,
        size_bytes=actual_size,
        sha256=actual_sha,
    )


def restore_backup_set(settings: Settings, backup_set_id: str) -> RestoreResult:
    lock = _acquire_lock(settings)
    try:
        verified = verify_backup_set(settings, backup_set_id)
        return _install(
            settings,
            source=verified.database_path,
            action="restore",
            backup_set_id=backup_set_id,
            sha256=verified.sha256,
        )
    finally:
        lock.release()


def rollback_from(settings: Settings, recovery_source: str | Path) -> RestoreResult:
    lock = _acquire_lock(settings)
    try:
        source_dir = _recovery_source_dir(settings, recovery_source)
        source = source_dir / DATABASE_FILE
        if not source.is_file():
            raise RestoreError("validate", "回滚目录缺少完整数据库文件")
        integrity = integrity_check(source)
        if integrity != "ok":
            raise RestoreError("validate", "回滚数据库完整性检查未通过")
        return _install(
            settings,
            source=source,
            action="rollback",
            backup_set_id=None,
            sha256=sha256_file(source),
            rollback_from=source_dir,
        )
    finally:
        lock.release()


def _acquire_lock(settings: Settings) -> AppLock:
    settings.prepare_directories()
    lock = AppLock(settings.data_dir)
    try:
        lock.acquire()
    except AppLockBusyError as exc:
        raise RestoreError("lock", "应用正在运行，请先停止 app 再执行恢复或回滚") from exc
    except OSError as exc:
        raise RestoreError("lock", "无法取得数据目录锁") from exc
    return lock


def _recovery_source_dir(settings: Settings, raw: str | Path) -> Path:
    if not str(raw).strip():
        raise RestoreError("validate", "回滚目录不能为空")
    recovery_root = (settings.data_dir / RECOVERY_PRE_DIR).resolve()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise RestoreError("validate", "回滚目录必须是绝对路径")
    resolved = candidate.resolve()
    if resolved.parent != recovery_root or not resolved.is_dir():
        raise RestoreError("validate", "回滚目录必须位于数据目录的 recovery-pre 下")
    return resolved


def _install(
    settings: Settings,
    *,
    source: Path,
    action: str,
    backup_set_id: str | None,
    sha256: str,
    rollback_from: Path | None = None,
) -> RestoreResult:
    restored_at = datetime.now(UTC)
    recovery_dir = _new_recovery_dir(settings, restored_at)
    database_path = settings.database_path
    temp_path = settings.data_dir / f".restore-{utc_stamp(restored_at)}-{os.urandom(4).hex()}.tmp"
    try:
        _copy_with_fsync(source, temp_path)
        moved = _move_current_database(settings, recovery_dir)
        try:
            os.replace(temp_path, database_path)
            database_path.chmod(0o600)
            _fsync_directory(settings.data_dir)
        except OSError as exc:
            _restore_current_database(settings, recovery_dir)
            raise RestoreError("install", "安装备份数据库失败，已尝试恢复原数据库") from exc
    except RestoreError:
        temp_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise RestoreError("install", "写入恢复临时文件失败") from exc

    report = {
        "action": action,
        "backup_set_id": backup_set_id,
        "restored_at": restored_at.isoformat(),
        "app_version": APP_VERSION,
        "source_sha256": sha256,
        "integrity_check": "ok",
        "previous_database_moved_to": str(recovery_dir),
        "previous_files": moved,
        "rollback_from": str(rollback_from) if rollback_from is not None else None,
    }
    try:
        write_json(recovery_dir / RESTORE_REPORT_FILE, report)
    except OSError:
        logger.exception("restore report could not be written")
    logger.info(
        "database %s completed backup_set_id=%s recovery_dir=%s",
        action,
        backup_set_id or "-",
        recovery_dir.name,
    )
    return RestoreResult(
        action=action,
        backup_set_id=backup_set_id,
        restored_at=restored_at,
        recovery_dir=recovery_dir,
        sha256=sha256,
        integrity="ok",
    )


def _new_recovery_dir(settings: Settings, moment: datetime) -> Path:
    root = settings.data_dir / RECOVERY_PRE_DIR
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.chmod(0o700)
    base = utc_stamp(moment)
    candidate = root / base
    suffix = 1
    while candidate.exists():
        candidate = root / f"{base}-{suffix}"
        suffix += 1
    candidate.mkdir(mode=0o700)
    return candidate


def _move_current_database(settings: Settings, recovery_dir: Path) -> list[str]:
    moved: list[str] = []
    database_path = settings.database_path
    try:
        for suffix in _DATABASE_SUFFIXES:
            candidate = database_path.with_name(database_path.name + suffix)
            if candidate.exists():
                os.replace(candidate, recovery_dir / candidate.name)
                moved.append(candidate.name)
        if database_path.exists():
            os.replace(database_path, recovery_dir / database_path.name)
            moved.append(database_path.name)
    except OSError as exc:
        _restore_current_database(settings, recovery_dir)
        raise RestoreError("move", "移动当前数据库到恢复区失败") from exc
    return moved


def _restore_current_database(settings: Settings, recovery_dir: Path) -> None:
    database_path = settings.database_path
    try:
        for suffix in ("", *_DATABASE_SUFFIXES):
            candidate = recovery_dir / (database_path.name + suffix)
            if (
                candidate.exists()
                and not database_path.with_name(database_path.name + suffix).exists()
            ):
                os.replace(candidate, database_path.with_name(database_path.name + suffix))
        _fsync_directory(settings.data_dir)
    except OSError:
        logger.exception("failed to move previous database back after install failure")


def _copy_with_fsync(source: Path, target: Path) -> None:
    shutil.copyfile(source, target)
    target.chmod(0o600)
    with target.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
