"""在线备份集创建与查询。

依据 docs/13-Docker部署与运维.md 10.6：
- 使用 SQLite Online Backup API 生成一致性副本，不直接复制主文件；
- 对副本执行 ``PRAGMA integrity_check``，生成 ``manifest.json`` 与
  ``integrity-report.json``，全部成功后原子重命名；
- 失败保留错误记录且不计入可恢复集；只保留最近 N 个成功集。
"""

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.api.listing import SortOrder
from app.config import Settings
from app.db.types import utc_now
from app.errors import AppError
from app.modules.audit.service import record_audit
from app.modules.backups.storage import (
    DATABASE_FILE,
    FAILURES_DIR,
    INTEGRITY_REPORT_FILE,
    MANIFEST_FILE,
    integrity_check,
    sha256_file,
    write_json,
)
from app.version import APP_VERSION

logger = logging.getLogger(__name__)

_BACKUP_LOCK = threading.Lock()
_MAX_FAILURE_RECORDS = 20


@dataclass(frozen=True, slots=True)
class BackupInfo:
    id: str
    created_at: datetime
    status: str
    size_bytes: int
    sha256: str | None
    integrity: str | None
    error: str | None = None


def create_backup(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    actor_user_id: str | None,
    request_id: str | None,
    source: str = "API",
) -> BackupInfo:
    if not _BACKUP_LOCK.acquire(blocking=False):
        raise AppError(
            "BACKUP_IN_PROGRESS",
            "已有备份正在执行，请稍后重试",
            status_code=409,
            resource="backup",
        )
    try:
        return _create_backup(
            settings,
            session_factory,
            actor_user_id=actor_user_id,
            request_id=request_id,
            source=source,
        )
    finally:
        _BACKUP_LOCK.release()


def _create_backup(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    actor_user_id: str | None,
    request_id: str | None,
    source: str,
) -> BackupInfo:
    settings.prepare_directories()
    created_at = datetime.now(UTC)
    backup_id = f"{created_at:%Y%m%dT%H%M%SZ}-{os.urandom(4).hex()}"
    final_dir = settings.backup_dir / backup_id
    temp_dir = settings.backup_dir / f".tmp-{backup_id}"
    temp_dir.mkdir(mode=0o700, parents=False)
    database_path = settings.database_path
    if not database_path.is_file():
        _record_failure(settings, backup_id, created_at, "数据库文件不存在")
        raise AppError(
            "BACKUP_FAILED",
            "备份失败：数据库文件不存在",
            status_code=500,
            resource="backup",
        )
    try:
        backup_path = temp_dir / DATABASE_FILE
        _copy_database(database_path, backup_path)
        integrity = integrity_check(backup_path)
        if integrity != "ok":
            raise RuntimeError(f"integrity_check 返回 {integrity}")
        size_bytes = backup_path.stat().st_size
        sha256 = sha256_file(backup_path)
        manifest = {
            "backup_set_id": backup_id,
            "created_at": created_at.isoformat(),
            "app_version": APP_VERSION,
            "database_file": DATABASE_FILE,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "integrity_check": integrity,
        }
        report = {
            "backup_set_id": backup_id,
            "checked_at": created_at.isoformat(),
            "integrity_check": integrity,
        }
        write_json(temp_dir / MANIFEST_FILE, manifest)
        write_json(temp_dir / INTEGRITY_REPORT_FILE, report)
        os.replace(temp_dir, final_dir)
    except AppError:
        _cleanup(temp_dir)
        raise
    except Exception as exc:
        _cleanup(temp_dir)
        logger.exception("backup failed backup_set_id=%s", backup_id)
        _record_failure(settings, backup_id, created_at, _safe_error(exc))
        raise AppError(
            "BACKUP_FAILED",
            "备份失败，未生成可恢复备份集",
            status_code=500,
            resource="backup",
            items=[{"backup_set_id": backup_id, "reason": "备份或完整性校验失败"}],
        ) from exc

    _apply_retention(settings)
    _audit_backup(
        session_factory,
        backup_id=backup_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        source=source,
        size_bytes=size_bytes,
        sha256=sha256,
    )
    return BackupInfo(
        id=backup_id,
        created_at=created_at,
        status="SUCCESS",
        size_bytes=size_bytes,
        sha256=sha256,
        integrity=integrity,
    )


_SORT_FIELDS = {
    "created_at": lambda item: item.created_at,
    "id": lambda item: item.id,
    "status": lambda item: item.status,
    "size_bytes": lambda item: item.size_bytes,
}


def list_backups(
    settings: Settings,
    *,
    page: int = 1,
    page_size: int = 20,
    sort: str | None = None,
    order: SortOrder | None = None,
) -> tuple[list[BackupInfo], int]:
    items: list[BackupInfo] = []
    if settings.backup_dir.is_dir():
        for child in settings.backup_dir.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            manifest_path = child / MANIFEST_FILE
            if not manifest_path.is_file():
                continue
            items.append(_read_manifest(child.name, manifest_path))
        failures_dir = settings.backup_dir / FAILURES_DIR
        if failures_dir.is_dir():
            for failure in failures_dir.glob("*.json"):
                items.append(_read_failure(failure))
    field = (sort or "created_at").strip()
    if field not in _SORT_FIELDS:
        raise AppError(
            "VALIDATION_ERROR",
            "请求参数校验失败",
            status_code=422,
            resource="backup",
            items=[
                {
                    "field": "sort",
                    "value": sort,
                    "reason": "不支持的排序字段",
                    "allowed": sorted(_SORT_FIELDS),
                }
            ],
        )
    direction = order or SortOrder.DESC
    items.sort(key=_SORT_FIELDS[field], reverse=direction == SortOrder.DESC)
    start = (page - 1) * page_size
    return items[start : start + page_size], len(items)


def _read_manifest(backup_id: str, manifest_path: Path) -> BackupInfo:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(str(manifest["created_at"]))
        return BackupInfo(
            id=backup_id,
            created_at=created_at,
            status="SUCCESS",
            size_bytes=int(manifest.get("size_bytes", 0)),
            sha256=str(manifest.get("sha256") or ""),
            integrity=str(manifest.get("integrity_check") or ""),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return BackupInfo(
            id=backup_id,
            created_at=datetime.now(UTC),
            status="FAILED",
            size_bytes=0,
            sha256=None,
            integrity=None,
            error="manifest 无法读取",
        )


def _read_failure(path: Path) -> BackupInfo:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BackupInfo(
            id=str(data.get("backup_set_id") or path.stem),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            status="FAILED",
            size_bytes=0,
            sha256=None,
            integrity=None,
            error=str(data.get("error") or "备份失败"),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return BackupInfo(
            id=path.stem,
            created_at=datetime.now(UTC),
            status="FAILED",
            size_bytes=0,
            sha256=None,
            integrity=None,
            error="失败记录无法读取",
        )


def _copy_database(source_path: Path, target_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(target_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _record_failure(settings: Settings, backup_id: str, created_at: datetime, error: str) -> None:
    try:
        failures = settings.backup_dir / FAILURES_DIR
        failures.mkdir(mode=0o700, exist_ok=True)
        path = failures / f"{backup_id}.json"
        write_json(
            path,
            {
                "backup_set_id": backup_id,
                "created_at": created_at.isoformat(),
                "error": error,
            },
        )
        records = sorted(failures.glob("*.json"), key=lambda item: item.name)
        for stale in records[:-_MAX_FAILURE_RECORDS]:
            stale.unlink(missing_ok=True)
    except OSError:
        logger.exception("backup failure record could not be written")


def _apply_retention(settings: Settings) -> None:
    try:
        successful = sorted(
            [
                child
                for child in settings.backup_dir.iterdir()
                if child.is_dir()
                and not child.name.startswith(".")
                and (child / MANIFEST_FILE).is_file()
            ],
            key=lambda item: item.name,
            reverse=True,
        )
        for stale in successful[settings.backup_retention_sets :]:
            for path in sorted(stale.rglob("*"), reverse=True):
                path.unlink(missing_ok=True)
            stale.rmdir()
    except OSError:
        logger.exception("backup retention cleanup failed")


def _audit_backup(
    session_factory: sessionmaker[Session],
    *,
    backup_id: str,
    actor_user_id: str | None,
    request_id: str | None,
    source: str,
    size_bytes: int,
    sha256: str,
) -> None:
    try:
        with session_factory() as session:
            record_audit(
                session,
                actor_user_id=actor_user_id,
                action="BACKUP_CREATED",
                entity_type="backup",
                entity_id=backup_id,
                after={"backup_set_id": backup_id, "size_bytes": size_bytes, "sha256": sha256},
                request_id=request_id,
                source=source,
                now=utc_now(),
            )
            session.commit()
    except Exception:
        # 备份文件已成功生成；审计失败不应回滚已完成的备份集。
        logger.exception("backup audit failed backup_set_id=%s", backup_id)


def _cleanup(path: Path) -> None:
    try:
        if path.is_dir():
            for child in sorted(path.rglob("*"), reverse=True):
                child.unlink(missing_ok=True)
            path.rmdir()
    except OSError:
        logger.exception("backup temp cleanup failed")


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip()
    return message[:200] if message else exc.__class__.__name__
