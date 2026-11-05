"""模板下拉选项加载。

依据 docs/07-XLSX模板、快速录入与导出.md 7.8.1：
- 下拉来源为模板生成时的当前基础资料名称；
- 仓库、部门、项目、厂家仅启用记录；入库分类、物资仅启用记录，
  出库分类、物资包含停用记录（仍按出库流程校验）；
- 下拉只是输入提示，解析时仍按 7.8.2 重新校验名称和状态。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.categories.models import Category
from app.modules.departments.models import Department
from app.modules.documents.models import DocType
from app.modules.manufacturers.models import Manufacturer
from app.modules.materials.models import Material
from app.modules.projects.models import Project
from app.modules.warehouses.models import Warehouse
from app.modules.xlsx.templates import TemplateOptions


def load_template_options(session: Session, doc_type: str) -> TemplateOptions:
    """按单据类型读取下拉选项；名称去重并排序，保证生成结果可复现。"""
    inbound = doc_type == DocType.INBOUND.value
    return TemplateOptions(
        warehouse_names=_names(session, Warehouse, Warehouse.name, active_only=True),
        department_names=_names(session, Department, Department.name, active_only=True),
        project_names=_names(session, Project, Project.name, active_only=True),
        level1_names=_names(
            session,
            Category,
            Category.name,
            active_only=inbound,
            conditions=(Category.parent_id.is_(None),),
        ),
        level2_names=_names(
            session,
            Category,
            Category.name,
            active_only=inbound,
            conditions=(Category.parent_id.is_not(None),),
        ),
        material_names=_names(session, Material, Material.name, active_only=inbound),
        manufacturer_names=(
            _names(session, Manufacturer, Manufacturer.name, active_only=True) if inbound else ()
        ),
    )


def _names(
    session: Session,
    model: type,
    column,
    *,
    active_only: bool,
    conditions: tuple = (),
) -> tuple[str, ...]:
    statement = select(column)
    if conditions:
        statement = statement.where(*conditions)
    if active_only:
        statement = statement.where(model.is_active.is_(True))
    statement = statement.order_by(column)
    return tuple(dict.fromkeys(session.scalars(statement).all()))
