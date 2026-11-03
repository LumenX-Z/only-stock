"""用户管理 API 集成测试：创建、列表、修改、禁用、重置与最后管理员保护。"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api_keys import create_api_key
from app.auth.models import ApiKey
from app.auth.passwords import verify_password
from app.modules.users.models import Role, User, UserStatus


def _csrf(identity: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": str(identity["csrf_token"])}


def _create_user(
    client: TestClient,
    identity: dict[str, Any],
    *,
    login_name: str = "new-operator",
    role: str = "OPERATOR",
    password: str = "initial-password-123",
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/users",
        json={
            "login_name": login_name,
            "display_name": f"显示名-{login_name}",
            "role": role,
            "initial_password": password,
        },
        headers=_csrf(identity),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_user_returns_identity_without_password_and_forces_change(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    created = _create_user(client, identity)

    assert created["login_name"] == "new-operator"
    assert created["role"] == "OPERATOR"
    assert created["status"] == "ACTIVE"
    assert created["force_password_change"] is True
    assert created["version"] == 1
    assert "password" not in created
    assert "initial_password" not in created

    with session_factory() as session:
        user = session.get(User, created["id"])
    assert user is not None
    assert verify_password(user.password_hash, "initial-password-123") is True

    login = client.post(
        "/auth/login",
        json={"login_name": "new-operator", "password": "initial-password-123"},
    )
    assert login.status_code == 200
    assert login.json()["force_password_change"] is True
    assert client.get("/api/v1/warehouses").status_code == 403


def test_duplicate_login_name_is_case_insensitive(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    _create_user(client, identity, login_name="Duplicated")

    response = client.post(
        "/api/v1/users",
        json={
            "login_name": " duplicated ",
            "display_name": "重复",
            "role": "OPERATOR",
            "initial_password": "initial-password-123",
        },
        headers=_csrf(identity),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LOGIN_NAME_CONFLICT"
    assert client.get("/api/v1/users").json()["total"] == 2


def test_invalid_role_and_short_password_are_rejected(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    bad_role = client.post(
        "/api/v1/users",
        json={
            "login_name": "bad-role",
            "display_name": "bad",
            "role": "SUPER_ADMIN",
            "initial_password": "initial-password-123",
        },
        headers=_csrf(identity),
    )
    short = client.post(
        "/api/v1/users",
        json={
            "login_name": "short-password",
            "display_name": "short",
            "role": "OPERATOR",
            "initial_password": "short",
        },
        headers=_csrf(identity),
    )

    assert bad_role.status_code == 422
    assert short.status_code == 422
    assert client.get("/api/v1/users").json()["total"] == 1


def test_list_users_supports_pagination_and_filters(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    user_factory: Callable[..., User],
) -> None:
    user_factory("operator-a", role=Role.OPERATOR)
    user_factory("operator-b", role=Role.OPERATOR)
    user_factory("disabled-user", status=UserStatus.DISABLED)
    client, _ = logged_in_admin

    page = client.get("/api/v1/users?page=1&page_size=2")
    filtered = client.get("/api/v1/users?role=OPERATOR&status=ACTIVE")
    found = client.get("/api/v1/users?login_name=OPERATOR-A")

    assert page.status_code == 200
    assert page.json()["total"] == 4
    assert len(page.json()["items"]) == 2
    assert filtered.json()["total"] == 2
    assert all(item["role"] == "OPERATOR" for item in filtered.json()["items"])
    assert found.json()["total"] == 1
    assert found.json()["items"][0]["login_name"] == "operator-a"


def test_update_display_name_and_version_conflict(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    created = _create_user(client, identity)

    updated = client.patch(
        f"/api/v1/users/{created['id']}",
        json={"display_name": "新显示名", "version": 1},
        headers=_csrf(identity),
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "新显示名"
    assert updated.json()["version"] == 2

    stale = client.patch(
        f"/api/v1/users/{created['id']}",
        json={"display_name": "过期修改", "version": 1},
        headers=_csrf(identity),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"


def test_role_change_revokes_target_sessions_and_keys(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    target = user_factory("second-admin", role=Role.COMPANY_ADMIN, force_password_change=False)
    key, _ = create_api_key(session_factory, target.id, "target-key")

    response = client.patch(
        f"/api/v1/users/{target.id}",
        json={"role": "OPERATOR", "version": 1},
        headers=_csrf(identity),
    )

    assert response.status_code == 200
    assert response.json()["role"] == "OPERATOR"
    with session_factory() as session:
        stored = session.get(User, target.id)
        stored_key = session.get(ApiKey, key.id)
    assert stored is not None and stored.role == Role.OPERATOR.value
    assert stored_key is not None
    assert stored_key.revoked_at is not None
    assert stored_key.revoke_reason == "ROLE_CHANGED"


def test_disable_user_revokes_credentials_and_blocks_login(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    target = user_factory("to-disable", force_password_change=False)
    key, _ = create_api_key(session_factory, target.id, "disable-key")

    response = client.post(f"/api/v1/users/{target.id}/disable", headers=_csrf(identity))

    assert response.status_code == 200
    assert response.json()["status"] == "DISABLED"
    with session_factory() as session:
        stored_key = session.get(ApiKey, key.id)
    assert stored_key is not None and stored_key.revoked_at is not None
    assert stored_key.revoke_reason == "ACCOUNT_DISABLED"

    login = client.post(
        "/auth/login", json={"login_name": "to-disable", "password": "operator-password-123"}
    )
    assert login.status_code == 403
    assert login.json()["error"]["code"] == "ACCOUNT_DISABLED"


def test_reset_password_forces_change_and_does_not_echo_password(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    created = _create_user(client, identity)
    new_password = "reset-password-456"

    response = client.post(
        f"/api/v1/users/{created['id']}/reset-password",
        json={"new_password": new_password},
        headers=_csrf(identity),
    )

    assert response.status_code == 200
    assert new_password not in response.text
    assert response.json()["force_password_change"] is True
    old_login = client.post(
        "/auth/login",
        json={"login_name": "new-operator", "password": "initial-password-123"},
    )
    assert old_login.status_code == 401
    new_login = client.post(
        "/auth/login", json={"login_name": "new-operator", "password": new_password}
    )
    assert new_login.status_code == 200
    assert new_login.json()["force_password_change"] is True


def test_last_company_admin_cannot_be_demoted_or_disabled(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin

    demote = client.patch(
        f"/api/v1/users/{identity['id']}",
        json={"role": "OPERATOR", "version": identity["version"]},
        headers=_csrf(identity),
    )
    disable = client.post(f"/api/v1/users/{identity['id']}/disable", headers=_csrf(identity))

    assert demote.status_code == 409
    assert demote.json()["error"]["code"] == "LAST_ADMIN_PROTECTED"
    assert disable.status_code == 409
    assert disable.json()["error"]["code"] == "LAST_ADMIN_PROTECTED"
    me = client.get("/auth/me").json()
    assert me["role"] == "COMPANY_ADMIN"
    assert me["status"] == "ACTIVE"


def test_second_admin_can_be_demoted(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    user_factory: Callable[..., User],
) -> None:
    client, identity = logged_in_admin
    target = user_factory("extra-admin", role=Role.COMPANY_ADMIN, force_password_change=False)

    response = client.patch(
        f"/api/v1/users/{target.id}",
        json={"role": "OPERATOR", "version": 1},
        headers=_csrf(identity),
    )

    assert response.status_code == 200
    assert response.json()["role"] == "OPERATOR"


def test_operator_cannot_access_user_management(
    admin_client: TestClient,
    user_factory: Callable[..., User],
) -> None:
    target = user_factory("operator", role=Role.OPERATOR)
    login = admin_client.post(
        "/auth/login", json={"login_name": "operator", "password": "operator-password-123"}
    )
    identity = login.json()

    assert admin_client.get("/api/v1/users").status_code == 403
    assert (
        admin_client.post(
            "/api/v1/users",
            json={
                "login_name": "forbidden",
                "display_name": "forbidden",
                "role": "OPERATOR",
                "initial_password": "initial-password-123",
            },
            headers=_csrf(identity),
        ).status_code
        == 403
    )
    assert (
        admin_client.post(f"/api/v1/users/{target.id}/disable", headers=_csrf(identity)).status_code
        == 403
    )


def test_openapi_contains_user_management_paths(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin
    paths = set(client.get("/openapi.json").json()["paths"])

    assert {
        "/api/v1/users",
        "/api/v1/users/{user_id}",
        "/api/v1/users/{user_id}/disable",
        "/api/v1/users/{user_id}/reset-password",
        "/api/v1/warehouses",
        "/api/v1/categories",
        "/api/v1/materials",
        "/api/v1/departments",
        "/api/v1/projects",
        "/api/v1/manufacturers",
        "/api/v1/audit-logs",
    } <= paths


def test_user_creation_failure_leaves_no_half_account(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    with session_factory() as session:
        session.execute(
            text(
                "CREATE TRIGGER fail_user_insert BEFORE INSERT ON users "
                "BEGIN SELECT RAISE(ABORT, 'forced user failure'); END"
            )
        )
        session.commit()

    with pytest.raises(IntegrityError, match="forced user failure"):
        client.post(
            "/api/v1/users",
            json={
                "login_name": "half-created",
                "display_name": "半成品",
                "role": "OPERATOR",
                "initial_password": "initial-password-123",
            },
            headers=_csrf(identity),
        )

    with session_factory() as session:
        session.execute(text("DROP TRIGGER fail_user_insert"))
        session.commit()
        count = session.scalar(
            select(func.count()).select_from(User).where(User.login_name == "half-created")
        )
    assert count == 0
