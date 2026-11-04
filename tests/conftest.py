"""测试夹具。

所有测试使用 tmp_path 下的独立数据/备份目录，不读写开发数据库或真实备份目录；
需要数据库结构的测试通过 Alembic 迁移建立 schema。
"""

import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api_keys import create_api_key
from app.auth.login_names import validate_login_name
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.database import create_engine_from_settings, create_session_factory
from app.db.migrations import upgrade_to_head
from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.main import create_app
from app.modules.categories.models import Category
from app.modules.departments.models import Department
from app.modules.manufacturers.models import Manufacturer
from app.modules.masterdata.service import create_entity
from app.modules.materials.models import Material
from app.modules.projects.models import Project
from app.modules.users.models import Role, User, UserStatus
from app.modules.warehouses.models import Warehouse

ADMIN_USERNAME = "admin"
ADMIN_INITIAL_PASSWORD = "initial-password-123"
ADMIN_PASSWORD = "changed-password-456"


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {
        "APP_ENV": "test",
        "DATA_DIR": str(tmp_path / "data"),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "MCP_ALLOWED_HOSTS": "127.0.0.1,localhost",
        "BOOTSTRAP_ADMIN_USERNAME": "",
        "BOOTSTRAP_ADMIN_PASSWORD": "",
    }


@pytest.fixture
def settings(env: dict[str, str]) -> Settings:
    return Settings.from_env(env)


@pytest.fixture
def migrated(settings: Settings) -> Settings:
    upgrade_to_head(settings)
    return settings


@pytest.fixture
def app(migrated: Settings) -> FastAPI:
    return create_app(migrated)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_env(env: dict[str, str]) -> dict[str, str]:
    values = dict(env)
    values.update(
        {
            "BOOTSTRAP_ADMIN_USERNAME": ADMIN_USERNAME,
            "BOOTSTRAP_ADMIN_PASSWORD": ADMIN_INITIAL_PASSWORD,
        }
    )
    return values


@pytest.fixture
def admin_settings(admin_env: dict[str, str]) -> Settings:
    return Settings.from_env(admin_env)


@pytest.fixture
def admin_app(admin_settings: Settings) -> FastAPI:
    upgrade_to_head(admin_settings)
    return create_app(admin_settings)


@pytest.fixture
def admin_client(admin_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(admin_app) as test_client:
        yield test_client


@pytest.fixture
def logged_in_admin(
    admin_client: TestClient,
) -> tuple[TestClient, dict[str, Any]]:
    """登录首位管理员并完成强制改密，返回带新会话的客户端与身份响应。"""
    login = admin_client.post(
        "/auth/login",
        json={"login_name": ADMIN_USERNAME, "password": ADMIN_INITIAL_PASSWORD},
    )
    assert login.status_code == 200
    token = login.json()["csrf_token"]
    changed = admin_client.post(
        "/auth/change-password",
        json={
            "old_password": ADMIN_INITIAL_PASSWORD,
            "new_password": ADMIN_PASSWORD,
        },
        headers={"X-CSRF-Token": token},
    )
    assert changed.status_code == 200
    return admin_client, changed.json()


@pytest.fixture
def db_engine(settings: Settings) -> Iterator[Engine]:
    settings.prepare_directories()
    engine = create_engine_from_settings(settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def migrated_engine(migrated: Settings) -> Iterator[Engine]:
    engine = create_engine_from_settings(migrated)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(migrated_engine)


@pytest.fixture
def user_factory(
    session_factory: sessionmaker[Session],
) -> Callable[..., User]:
    def create(
        login_name: str = "operator",
        *,
        password: str = "operator-password-123",
        display_name: str | None = None,
        role: Role = Role.OPERATOR,
        status: UserStatus = UserStatus.ACTIVE,
        force_password_change: bool = False,
        now: datetime | None = None,
    ) -> User:
        created_at = now or utc_now()
        with session_factory() as session, begin_immediate(session):
            user = User(
                login_name=validate_login_name(login_name),
                password_hash=hash_password(password),
                display_name=display_name or login_name,
                role=role.value,
                status=status.value,
                force_password_change=force_password_change,
                password_version=1,
                version=1,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(user)
            session.flush()
            session.expunge(user)
            return user

    return create


@dataclass(frozen=True, slots=True)
class MasterDataFixture:
    """P4 单据测试所需的启用基础资料。"""

    actor: User
    warehouse: Warehouse
    warehouse_second: Warehouse
    department: Department
    project: Project
    project_second: Project
    manufacturer: Manufacturer
    category: Category
    category_child: Category
    material: Material
    material_second: Material
    material_integer: Material


@pytest.fixture
def master_data(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
) -> MasterDataFixture:
    """通过业务服务建立启用仓库、部门、项目、厂家、两级分类与物资。"""
    actor = user_factory("data-admin", role=Role.COMPANY_ADMIN, display_name="资料管理员")

    def create(key: str, name: str, **fixed: Any) -> Any:
        return create_entity(
            session_factory,
            key,
            name=name,
            actor_user_id=actor.id,
            request_id=None,
            **fixed,
        )

    warehouse = create("warehouse", "主仓")
    warehouse_second = create("warehouse", "二号仓")
    department = create("department", "生产部")
    project = create("project", "一号项目")
    project_second = create("project", "二号项目")
    manufacturer = create("manufacturer", "钢厂")
    category = create("category", "原材料")
    category_child = create("category", "钢材", parent_id=category.id)
    material = create(
        "material",
        "螺纹钢",
        category_id=category_child.id,
        unit="千克",
        quantity_type="DECIMAL",
    )
    material_second = create(
        "material",
        "圆钢",
        category_id=category.id,
        unit="个",
        quantity_type="DECIMAL",
    )
    material_integer = create(
        "material",
        "整根钢",
        category_id=category_child.id,
        unit="根",
        quantity_type="INTEGER",
    )
    return MasterDataFixture(
        actor=actor,
        warehouse=warehouse,
        warehouse_second=warehouse_second,
        department=department,
        project=project,
        project_second=project_second,
        manufacturer=manufacturer,
        category=category,
        category_child=category_child,
        material=material,
        material_second=material_second,
        material_integer=material_integer,
    )


@dataclass(frozen=True, slots=True)
class MCPAccounts:
    """MCP 测试所需的两种角色账户与有效 Key。"""

    ca: User
    op: User
    ca_key: str
    op_key: str


@pytest.fixture
def mcp_accounts(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
) -> MCPAccounts:
    ca = user_factory("mcp-ca", role=Role.COMPANY_ADMIN, display_name="MCP 管理员")
    op = user_factory("mcp-op", role=Role.OPERATOR, display_name="MCP 操作员")
    _, ca_key = create_api_key(session_factory, ca.id, "ca-mcp")
    _, op_key = create_api_key(session_factory, op.id, "op-mcp")
    return MCPAccounts(ca=ca, op=op, ca_key=ca_key, op_key=op_key)


@pytest.fixture
def run_concurrently() -> Callable[[Sequence[Callable[[], object]]], list[object]]:
    """并发测试辅助：所有任务用 Barrier 对齐后同时执行。"""

    def run(callables: Sequence[Callable[[], object]]) -> list[object]:
        barrier = threading.Barrier(len(callables))

        def invoke(callable_: Callable[[], object]) -> object:
            barrier.wait()
            return callable_()

        with ThreadPoolExecutor(max_workers=len(callables)) as executor:
            futures = [executor.submit(invoke, callable_) for callable_ in callables]
            return [future.result() for future in futures]

    return run
