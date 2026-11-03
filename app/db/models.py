"""模型集中导入点。

Alembic 通过本模块取得包含全部模型的 ``Base.metadata``；后续阶段新增模型时
在此处导入，确保自动生成迁移不会漏表。
"""

from app.auth.models import ApiKey, AuthSession
from app.db.base import Base
from app.modules.users.models import User

__all__ = ["ApiKey", "AuthSession", "Base", "User"]
