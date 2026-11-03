"""测试夹具。

所有测试使用 tmp_path 下的独立数据/备份目录，不读写开发数据库或真实备份目录；
需要数据库结构的测试通过 Alembic 迁移建立 schema。
"""

import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.login_names import validate_login_name
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.database import create_engine_from_settings, create_session_factory
from app.db.migrations import upgrade_to_head
from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.main import create_app
from app.modules.users.models import Role, User, UserStatus

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
