"""模型集中导入点。

Alembic 通过本模块取得包含全部模型的 ``Base.metadata``；后续阶段新增模型时
在此处导入，确保自动生成迁移不会漏表。
"""

from app.auth.models import ApiKey, AuthSession
from app.db.base import Base
from app.modules.audit.models import AuditLog
from app.modules.categories.models import Category
from app.modules.departments.models import Department
from app.modules.documents.models import DocumentCounter, StockDocument, StockDocumentLine
from app.modules.manufacturers.models import Manufacturer
from app.modules.masterdata.base import CodeCounter
from app.modules.materials.models import Material
from app.modules.projects.models import Project
from app.modules.users.models import User
from app.modules.warehouses.models import Warehouse

__all__ = [
    "ApiKey",
    "AuditLog",
    "AuthSession",
    "Base",
    "Category",
    "CodeCounter",
    "Department",
    "DocumentCounter",
    "Manufacturer",
    "Material",
    "Project",
    "StockDocument",
    "StockDocumentLine",
    "User",
    "Warehouse",
]
