"""备份 API 响应模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.api.schemas import PageMeta


class BackupOut(BaseModel):
    id: str
    created_at: datetime
    status: Literal["SUCCESS", "FAILED"]
    size_bytes: int
    sha256: str | None = None
    integrity: str | None = None
    error: str | None = None


class BackupListResponse(PageMeta):
    items: list[BackupOut]
