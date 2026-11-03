"""初始公司管理员创建与幂等性测试。"""

from collections.abc import Callable, Sequence

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.database import create_engine_from_settings, create_session_factory
from app.db.migrations import upgrade_to_head
from app.errors import AppError
from app.main import create_app
from app.modules.users.models import Role, User, UserStatus
from app.modules.users.service import bootstrap_initial_admin

ADMIN_USERNAME = "admin"
ADMIN_INITIAL_PASSWORD = "initial-password-123"


def test_bootstrap_creates_company_admin_with_forced_password_change(
    admin_env: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    settings = Settings.from_env(admin_env)
    upgrade_to_head(settings)

    with TestClient(create_app(settings)):
        pass

    with session_factory() as session:
        users = list(session.scalars(select(User)).all())

    assert len(users) == 1
    user = users[0]
    assert user.login_name == ADMIN_USERNAME
    assert user.display_name == ADMIN_USERNAME
    assert user.role == Role.COMPANY_ADMIN.value
    assert user.status == UserStatus.ACTIVE.value
    assert user.force_password_change is True
    assert user.password_hash.startswith("$argon2id$")
    assert ADMIN_INITIAL_PASSWORD not in user.password_hash


def test_repeated_startup_does_not_create_duplicate_admin(
    admin_env: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    settings = Settings.from_env(admin_env)
    upgrade_to_head(settings)

    for _ in range(2):
        with TestClient(create_app(settings)):
            pass

    with session_factory() as session:
        user_count = session.scalar(select(func.count()).select_from(User))

    assert user_count == 1


def test_bootstrap_is_ignored_when_database_has_users(
    admin_env: dict[str, str],
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
) -> None:
    user_factory("existing-operator", display_name="既有操作员")
    settings = Settings.from_env(admin_env)

    created = bootstrap_initial_admin(session_factory, settings)

    assert created is None
    with session_factory() as session:
        login_names = set(session.scalars(select(User.login_name)).all())
    assert login_names == {"existing-operator"}


def test_concurrent_bootstrap_creates_exactly_one_admin(
    admin_env: dict[str, str],
    settings: Settings,
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    upgrade_to_head(settings)
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    bootstrap_settings = Settings.from_env(admin_env)
    try:
        results = run_concurrently(
            [
                lambda: bootstrap_initial_admin(session_factory, bootstrap_settings),
                lambda: bootstrap_initial_admin(session_factory, bootstrap_settings),
            ]
        )
        with session_factory() as session:
            user_count = session.scalar(select(func.count()).select_from(User))
    finally:
        engine.dispose()

    assert user_count == 1
    assert len([result for result in results if result is not None]) == 1


def test_bootstrap_failure_leaves_no_user_behind(
    admin_env: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    invalid_env = dict(admin_env)
    invalid_env["BOOTSTRAP_ADMIN_USERNAME"] = "a" * 65
    settings = Settings.from_env(invalid_env)
    upgrade_to_head(settings)

    with pytest.raises(AppError) as error:
        with TestClient(create_app(settings)):
            pass
    assert error.value.items[0]["field"] == "BOOTSTRAP_ADMIN_USERNAME"

    with session_factory() as session:
        user_count = session.scalar(select(func.count()).select_from(User))
    assert user_count == 0


def test_bootstrap_insert_failure_rolls_back_all_changes(
    admin_env: dict[str, str], session_factory: sessionmaker[Session]
) -> None:
    settings = Settings.from_env(admin_env)
    upgrade_to_head(settings)
    with session_factory() as session:
        session.execute(
            text(
                "CREATE TRIGGER fail_users_insert BEFORE INSERT ON users "
                "BEGIN SELECT RAISE(ABORT, 'forced bootstrap failure'); END"
            )
        )
        session.commit()

    with pytest.raises(Exception, match="forced bootstrap failure"):
        with TestClient(create_app(settings)):
            pass

    with session_factory() as session:
        user_count = session.scalar(select(func.count()).select_from(User))
    assert user_count == 0


def test_non_empty_database_ignores_invalid_bootstrap_values(
    admin_env: dict[str, str],
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
) -> None:
    user_factory("existing-operator")
    invalid_env = dict(admin_env)
    invalid_env["BOOTSTRAP_ADMIN_USERNAME"] = "a" * 65
    settings = Settings.from_env(invalid_env)
    upgrade_to_head(settings)

    with TestClient(create_app(settings)):
        pass

    with session_factory() as session:
        login_names = set(session.scalars(select(User.login_name)).all())
    assert login_names == {"existing-operator"}
