"""XLSX 上传流式落盘。

依据 docs/14-安全、可靠性与性能要求.md 11.1：上传文件写入服务端随机临时文件，
超出大小限制立即拒绝；调用方负责解析后删除临时文件。
"""

import tempfile
from pathlib import Path

from fastapi import UploadFile

from app.modules.xlsx.constants import MAX_UPLOAD_BYTES
from app.modules.xlsx.errors import file_error

_UPLOAD_CHUNK = 1024 * 1024


def spool_upload(file: UploadFile) -> Path:
    """流式写入服务端随机临时文件（最小权限），超出限制立即拒绝。"""
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="only-stock-xlsx-", suffix=".xlsx", delete=False
        ) as handle:
            path = Path(handle.name)
            total = 0
            while True:
                chunk = file.file.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise file_error(
                        "XLSX_FILE_TOO_LARGE",
                        f"XLSX 文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB，已拒绝",
                        items=[
                            {
                                "field": "file",
                                "limit_bytes": MAX_UPLOAD_BYTES,
                            }
                        ],
                    )
                handle.write(chunk)
        return path
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise
