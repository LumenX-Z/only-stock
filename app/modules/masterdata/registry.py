"""六类基础资料注册表：资源键、表名、编码前缀与名称唯一范围。

依据 docs/05-数据模型与实体约束.md 6.2：仓库 ``CK``、部门 ``BM``、分类 ``FL``、
物资 ``WZ``、项目 ``XM``、厂家 ``CJ``；依据 6.4、6.8：仓库/部门/项目/厂家名称
全局唯一，分类按层级、物资按直接所属分类唯一。
"""

from dataclasses import dataclass

from app.db.base import Base
from app.modules.categories.models import Category
from app.modules.departments.models import Department
from app.modules.manufacturers.models import Manufacturer
from app.modules.materials.models import Material
from app.modules.projects.models import Project
from app.modules.warehouses.models import Warehouse

NAME_SCOPE_GLOBAL = "global"
NAME_SCOPE_CATEGORY = "category"
NAME_SCOPE_MATERIAL = "material"


@dataclass(frozen=True, slots=True)
class EntitySpec:
    key: str
    table: str
    prefix: str
    model: type[Base]
    label: str
    name_scope: str


SPECS: tuple[EntitySpec, ...] = (
    EntitySpec("warehouse", "warehouses", "CK", Warehouse, "仓库", NAME_SCOPE_GLOBAL),
    EntitySpec("department", "departments", "BM", Department, "部门", NAME_SCOPE_GLOBAL),
    EntitySpec("category", "categories", "FL", Category, "分类", NAME_SCOPE_CATEGORY),
    EntitySpec("material", "materials", "WZ", Material, "物资", NAME_SCOPE_MATERIAL),
    EntitySpec("project", "projects", "XM", Project, "项目", NAME_SCOPE_GLOBAL),
    EntitySpec("manufacturer", "manufacturers", "CJ", Manufacturer, "厂家", NAME_SCOPE_GLOBAL),
)

SPEC_BY_KEY: dict[str, EntitySpec] = {spec.key: spec for spec in SPECS}


def get_spec(key: str) -> EntitySpec:
    try:
        return SPEC_BY_KEY[key]
    except KeyError as exc:  # pragma: no cover - 编程错误，不来自用户输入
        raise ValueError(f"未知基础资料类型：{key}") from exc
