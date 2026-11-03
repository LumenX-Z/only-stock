"""服务端角色授权依赖。

依据 docs/03-角色、权限与账户生命周期.md 4.1、4.2 与 docs/09-HTTP-API通用约定与
健康检查.md 8.2：``CA`` 为 ``COMPANY_ADMIN``，``OP`` 为 ``OPERATOR``，``CU`` 为
任一启用用户。页面按钮隐藏和工具清单过滤都不是权限边界，每个请求在此重新校验
账户状态与角色。
"""

from typing import Annotated

from fastapi import Depends

from app.auth.dependencies import AuthContext, require_password_changed
from app.errors import AppError
from app.modules.users.models import Role

RequirePasswordChanged = Annotated[AuthContext, Depends(require_password_changed)]


def require_company_admin(
    auth: RequirePasswordChanged,
) -> AuthContext:
    """只允许公司管理员；操作员调用返回 403。"""
    if auth.user.role != Role.COMPANY_ADMIN.value:
        raise AppError(
            "FORBIDDEN",
            "无权执行该操作",
            status_code=403,
            resource="permission",
            items=[{"required_role": Role.COMPANY_ADMIN.value}],
        )
    return auth


CompanyAdmin = Annotated[AuthContext, Depends(require_company_admin)]
