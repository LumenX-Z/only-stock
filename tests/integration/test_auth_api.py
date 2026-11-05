"""登录、会话、CSRF 与本人改密接口测试。"""

from collections.abc import Callable
from datetime import timedelta
from http.cookies import SimpleCookie

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.account import reset_password
from app.auth.models import ApiKey, AuthSession
from app.auth.sessions import SESSION_COOKIE_NAME, hash_secret
from app.config import Settings
from app.db.types import utc_now
from app.modules.users.models import User, UserStatus

ADMIN_USERNAME = "admin"
ADMIN_INITIAL_PASSWORD = "initial-password-123"
ADMIN_PASSWORD = "changed-password-456"
SECOND_PASSWORD = "second-changed-password-789"


def _login(
    client: TestClient,
    login_name: str = ADMIN_USERNAME,
    password: str = ADMIN_INITIAL_PASSWORD,
) -> dict[str, object]:
    response = client.post("/auth/login", json={"login_name": login_name, "password": password})
    assert response.status_code == 200
    return response.json()


def _csrf(identity: dict[str, object]) -> dict[str, str]:
    return {"X-CSRF-Token": str(identity["csrf_token"])}


def _cookie_attributes(response_headers: str) -> list[str]:
    parsed = SimpleCookie()
    parsed.load(response_headers)
    cookie = parsed["only_stock_session"]
    attributes = [part.strip().lower() for part in response_headers.split(";")]
    assert cookie.value
    return attributes


def test_login_sets_session_cookie_and_returns_identity(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/auth/login",
        json={"login_name": "ADMIN", "password": ADMIN_INITIAL_PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["login_name"] == ADMIN_USERNAME
    assert body["role"] == "COMPANY_ADMIN"
    assert body["status"] == "ACTIVE"
    assert body["force_password_change"] is True
    assert body["csrf_token"]

    set_cookie = response.headers["set-cookie"]
    attributes = _cookie_attributes(set_cookie)
    assert "httponly" in attributes
    assert "path=/" in attributes
    assert "samesite=lax" in attributes
    assert "secure" not in attributes
    assert not any(attribute.startswith("max-age") for attribute in attributes)

    me = admin_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["csrf_token"] == body["csrf_token"]


def test_login_failure_does_not_reveal_whether_account_exists(
    admin_client: TestClient,
) -> None:
    known = admin_client.post(
        "/auth/login",
        json={"login_name": ADMIN_USERNAME, "password": "wrong-password-123"},
    )
    unknown = admin_client.post(
        "/auth/login",
        json={"login_name": "nobody", "password": "wrong-password-123"},
    )

    assert known.status_code == 401
    assert unknown.status_code == 401
    assert known.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert known.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_disabled_account_cannot_login(
    admin_client: TestClient,
    user_factory: Callable[..., User],
) -> None:
    user_factory("disabled-user", status=UserStatus.DISABLED)

    response = admin_client.post(
        "/auth/login",
        json={"login_name": "disabled-user", "password": "operator-password-123"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"


def test_forced_password_change_blocks_api_keys(admin_client: TestClient) -> None:
    _login(admin_client)

    response = admin_client.get("/api/v1/api-keys")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"


def test_me_requires_authentication(admin_client: TestClient) -> None:
    response = admin_client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_unauthenticated_api_clears_stale_session_cookie(admin_client: TestClient) -> None:
    admin_client.cookies.set(SESSION_COOKIE_NAME, "stale-session-token")

    response = admin_client.get("/auth/me")

    assert response.status_code == 401
    cleared = response.headers["set-cookie"].lower()
    assert SESSION_COOKIE_NAME in cleared
    assert "max-age=0" in cleared


def test_logout_revokes_session_and_clears_cookie(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin

    response = client.post("/auth/logout", headers=_csrf(identity))

    assert response.status_code == 204
    assert "only_stock_session=" in response.headers["set-cookie"]
    assert client.get("/auth/me").status_code == 401

    with session_factory() as session:
        sessions = list(session.scalars(select(AuthSession)).all())
    assert sessions
    assert all(row.revoked_at is not None for row in sessions)


def test_logout_requires_csrf(
    admin_client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _login(admin_client)

    response = admin_client.post("/auth/logout")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"
    with session_factory() as session:
        sessions = list(session.scalars(select(AuthSession)).all())
    assert len(sessions) == 1
    assert sessions[0].revoked_at is None


def test_change_password_rotates_current_session(admin_client: TestClient) -> None:
    identity = _login(admin_client)
    old_token = str(identity["csrf_token"])
    old_session = admin_client.cookies.get("only_stock_session")
    assert old_session

    response = admin_client.post(
        "/auth/change-password",
        json={
            "old_password": ADMIN_INITIAL_PASSWORD,
            "new_password": ADMIN_PASSWORD,
        },
        headers={"X-CSRF-Token": old_token},
    )

    assert response.status_code == 200
    changed = response.json()
    assert changed["force_password_change"] is False
    assert changed["csrf_token"] != old_token
    new_session = admin_client.cookies.get("only_stock_session")
    assert new_session
    assert new_session != old_session

    old_csrf = admin_client.post(
        "/auth/change-password",
        json={"old_password": ADMIN_PASSWORD, "new_password": SECOND_PASSWORD},
        headers={"X-CSRF-Token": old_token},
    )
    assert old_csrf.status_code == 403
    assert old_csrf.json()["error"]["code"] == "CSRF_INVALID"

    admin_client.cookies.clear()
    admin_client.cookies.set("only_stock_session", old_session)
    assert admin_client.get("/auth/me").status_code == 401
    admin_client.cookies.clear()
    admin_client.cookies.set("only_stock_session", new_session)
    assert admin_client.get("/auth/me").status_code == 200


def test_change_password_revokes_other_sessions_and_all_api_keys(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    current_session_before = client.cookies.get("only_stock_session")

    created = client.post(
        "/api/v1/api-keys", json={"name": "before-change"}, headers=_csrf(identity)
    )
    assert created.status_code == 201

    response = client.post(
        "/auth/change-password",
        json={"old_password": ADMIN_PASSWORD, "new_password": SECOND_PASSWORD},
        headers=_csrf(identity),
    )

    assert response.status_code == 200
    current_session_after = client.cookies.get("only_stock_session")
    assert current_session_after
    assert current_session_after != current_session_before

    with session_factory() as session:
        sessions = list(session.scalars(select(AuthSession)).all())
        keys = list(session.scalars(select(ApiKey)).all())
    assert len(sessions) == 3
    assert sum(1 for row in sessions if row.revoked_at is None) == 1
    assert hash_secret(current_session_after) in {
        row.id_hash for row in sessions if row.revoked_at is None
    }
    assert keys[0].revoked_at is not None
    assert keys[0].revoke_reason == "PASSWORD_CHANGED"


def test_change_password_rejects_wrong_old_password(
    logged_in_admin: tuple[TestClient, dict[str, object]],
) -> None:
    client, identity = logged_in_admin

    response = client.post(
        "/auth/change-password",
        json={"old_password": "wrong-old-password", "new_password": SECOND_PASSWORD},
        headers=_csrf(identity),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_OLD_PASSWORD"
    assert client.get("/auth/me").status_code == 200


def test_change_password_rejects_short_or_missing_csrf(
    logged_in_admin: tuple[TestClient, dict[str, object]],
) -> None:
    client, identity = logged_in_admin

    short = client.post(
        "/auth/change-password",
        json={"old_password": ADMIN_PASSWORD, "new_password": "short"},
        headers=_csrf(identity),
    )
    assert short.status_code == 422
    assert short.json()["error"]["code"] == "VALIDATION_ERROR"

    no_csrf = client.post(
        "/auth/change-password",
        json={"old_password": ADMIN_PASSWORD, "new_password": SECOND_PASSWORD},
    )
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "CSRF_INVALID"


def test_session_idle_expiry_revokes_session(
    admin_client: TestClient,
    session_factory: sessionmaker[Session],
    admin_settings: Settings,
) -> None:
    _login(admin_client)
    with session_factory() as session:
        row = session.scalars(select(AuthSession)).one()
        expired_at = utc_now() - timedelta(minutes=admin_settings.session_idle_minutes + 1)
        row.created_at = expired_at
        row.last_seen_at = expired_at
        session.commit()

    assert admin_client.get("/auth/me").status_code == 401
    with session_factory() as session:
        row = session.scalars(select(AuthSession)).one()
    assert row.revoked_at is not None


def test_session_absolute_expiry_revokes_session(
    admin_client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    _login(admin_client)
    with session_factory() as session:
        row = session.scalars(select(AuthSession)).one()
        created_at = utc_now() - timedelta(hours=25)
        row.created_at = created_at
        row.last_seen_at = created_at
        row.absolute_expires_at = utc_now() - timedelta(seconds=1)
        session.commit()

    assert admin_client.get("/auth/me").status_code == 401
    with session_factory() as session:
        row = session.scalars(select(AuthSession)).one()
    assert row.revoked_at is not None


def test_disabled_user_with_existing_session_is_rejected_and_revoked(
    admin_client: TestClient,
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
) -> None:
    user = user_factory("session-operator")
    login = admin_client.post(
        "/auth/login",
        json={"login_name": "session-operator", "password": "operator-password-123"},
    )
    assert login.status_code == 200

    with session_factory() as session:
        stored = session.get(User, user.id)
        stored.status = UserStatus.DISABLED.value
        session.commit()

    response = admin_client.get("/auth/me")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"
    with session_factory() as session:
        rows = list(session.scalars(select(AuthSession)).all())
    assert all(row.revoked_at is not None for row in rows)


def test_login_rate_limit_returns_429(admin_client: TestClient) -> None:
    for _ in range(5):
        response = admin_client.post(
            "/auth/login",
            json={"login_name": ADMIN_USERNAME, "password": "wrong-password-123"},
        )
        assert response.status_code == 401

    limited = admin_client.post(
        "/auth/login",
        json={"login_name": ADMIN_USERNAME, "password": ADMIN_INITIAL_PASSWORD},
    )

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert limited.json()["error"]["details"]["items"][0]["retry_after_seconds"] > 0


def test_openapi_contains_documented_auth_and_api_key_paths(admin_client: TestClient) -> None:
    schema = admin_client.get("/openapi.json").json()

    paths = set(schema["paths"])
    assert {
        "/auth/login",
        "/auth/logout",
        "/auth/me",
        "/auth/change-password",
        "/api/v1/api-keys",
        "/api/v1/api-keys/{key_id}/revoke",
    } <= paths
    assert "/api-keys" not in paths


def test_admin_reset_then_user_must_change_password_before_api_keys(
    admin_client: TestClient,
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    user = user_factory("reset-flow", force_password_change=False)
    reset_password(session_factory, user.id, "reset-password-456")

    login = admin_client.post(
        "/auth/login",
        json={"login_name": "reset-flow", "password": "reset-password-456"},
    )
    assert login.status_code == 200
    assert login.json()["force_password_change"] is True
    assert admin_client.get("/api/v1/api-keys").status_code == 403

    changed = admin_client.post(
        "/auth/change-password",
        json={
            "old_password": "reset-password-456",
            "new_password": "after-reset-password-789",
        },
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
    )
    assert changed.status_code == 200
    assert changed.json()["force_password_change"] is False
    assert admin_client.get("/api/v1/api-keys").status_code == 200
