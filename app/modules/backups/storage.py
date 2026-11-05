"""备份集文件常量与校验辅助。

依据 docs/13-Docker部署与运维.md 10.6：备份集固定包含
``inventory.sqlite3``、``manifest.json`` 与 ``integrity-report.json``；
恢复前必须重新校验 manifest、大小、SHA-256 和 ``PRAGMA integrity_check``。
"""

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DATABASE_FILE = "inventory.sqlite3"
MANIFEST_FILE = "manifest.json"
INTEGRITY_REPORT_FILE = "integrity-report.json"
FAILURES_DIR = "failures"
RECOVERY_PRE_DIR = "recovery-pre"
RESTORE_REPORT_FILE = "restore-report.json"

_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def integrity_check(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        return str(result[0]) if result else "unknown"
    finally:
        connection.close()


def write_json(path: Path, payload: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def utc_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
