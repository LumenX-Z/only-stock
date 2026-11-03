"""认证、CSRF、Cookie 与凭证存储的安全测试。"""

import hashlib
import logging
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.account import (
    authenticate_and_create_session,
    change_password,
    reset_password,
    revoke_credentials_for_user,
)
from app.auth.api_keys import (
    authenticate_api_key,
    create_api_key,
    revoke_api_key,
)
from app.auth.models import ApiKey, AuthSession
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import create_session
from app.config import Settings
from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.errors import AppError
from app.modules.users.models import Role, User, UserStatus
from app.modules.users.service import apply_user_state_change

ADMIN_USERNAME = "admin"
ADMIN_INITIAL_PASSWORD = "initial-password-123"
ADMIN_PASSWORD = "changed-password-456"


def _csrf(identity: dict[str, object]) -> dict[str, str]:
    return {"X-CSRF-Token": str(identity["csrf_token"])}


def _active_session(session: Session) -> AuthSession:
    return session.scalars(select(AuthSession).where(AuthSession.revoked_at.is_(None))).one()


def test_database_stores_hashes_not_plaintext_credentials(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    raw_session = client.cookies.get("only_stock_session")
    assert raw_session
    csrf_token = str(identity["csrf_token"])

    created = client.post(
        "/api/v1/api-keys", json={"name": "secret-check"}, headers=_csrf(identity)
    )
    raw_key = created.json()["key"]

    with session_factory() as session:
        session_row = _active_session(session)
        key_row = session.scalars(select(ApiKey)).one()
        user = session.scalars(select(User)).one()

    assert session_row.id_hash == hashlib.sha256(raw_session.encode("utf-8")).hexdigest()
    assert session_row.id_hash != raw_session
    assert session_row.csrf_hash == hashlib.sha256(csrf_token.encode("utf-8")).hexdigest()
    assert session_row.csrf_hash != csrf_token
    assert key_row.key_hash == hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    assert key_row.key_hash != raw_key
    assert key_row.key_suffix == raw_key[-4:]
    assert user.password_hash.startswith("$argon2id$")
    assert ADMIN_PASSWORD not in user.password_hash
    assert ADMIN_INITIAL_PASSWORD not in user.password_hash


def test_database_file_does_not_contain_plaintext_secrets(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    admin_settings: Settings,
) -> None:
    client, identity = logged_in_admin
    raw_session = client.cookies.get("only_stock_session")
    csrf_token = str(identity["csrf_token"])
    created = client.post("/api/v1/api-keys", json={"name": "file-check"}, headers=_csrf(identity))
    raw_key = created.json()["key"]

    database_path = admin_settings.database_path
    contents = database_path.read_bytes()
    sidecar = database_path.with_name(database_path.name + "-wal")
    if sidecar.exists():
        contents += sidecar.read_bytes()

    for secret in (raw_session, csrf_token, raw_key, ADMIN_PASSWORD, ADMIN_INITIAL_PASSWORD):
        assert secret is not None
        assert secret.encode("utf-8") not in contents


def test_me_response_does_not_expose_password_hash_or_keys(
    logged_in_admin: tuple[TestClient, dict[str, object]],
) -> None:
    client, identity = logged_in_admin
    client.post("/api/v1/api-keys", json={"name": "response-check"}, headers=_csrf(identity))

    response = client.get("/auth/me")

    assert response.status_code == 200
    body = response.text
    assert "password_hash" not in body
    assert "$argon2" not in body
    assert "key_hash" not in body
    assert "osk_" not in body


def test_all_authenticated_writes_require_csrf(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    created = client.post("/api/v1/api-keys", json={"name": "csrf-target"}, headers=_csrf(identity))
    key_id = created.json()["id"]

    assert client.post("/auth/logout").status_code == 403
    assert (
        client.post(
            "/auth/change-password",
            json={"old_password": ADMIN_PASSWORD, "new_password": "another-password-123"},
        ).status_code
        == 403
    )
    assert client.post("/api/v1/api-keys", json={"name": "no-csrf"}).status_code == 403
    tampered = client.post(
        f"/api/v1/api-keys/{key_id}/revoke",
        headers={"X-CSRF-Token": "x" * 64},
    )
    assert tampered.status_code == 403
    assert tampered.json()["error"]["code"] == "CSRF_INVALID"

    with session_factory() as session:
        key_row = session.get(ApiKey, key_id)
        user = session.scalars(select(User)).one()
        sessions = list(session.scalars(select(AuthSession)).all())
    assert key_row is not None and key_row.revoked_at is None
    assert user.password_hash.startswith("$argon2id$")
    assert any(row.revoked_at is None for row in sessions)


def test_cookie_attributes_and_entropy(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/auth/login",
        json={"login_name": ADMIN_USERNAME, "password": ADMIN_INITIAL_PASSWORD},
    )

    set_cookie = response.headers["set-cookie"]
    attributes = [part.strip().lower() for part in set_cookie.split(";")]
    raw_session = admin_client.cookies.get("only_stock_session")

    assert raw_session is not None
    assert len(raw_session) >= 43
    assert "httponly" in attributes
    assert "path=/" in attributes
    assert "samesite=lax" in attributes
    assert "secure" not in attributes


def test_login_failures_are_rate_limited(admin_client: TestClient) -> None:
    for _ in range(5):
        assert (
            admin_client.post(
                "/auth/login",
                json={"login_name": ADMIN_USERNAME, "password": "wrong-password-123"},
            ).status_code
            == 401
        )

    response = admin_client.post(
        "/auth/login",
        json={"login_name": ADMIN_USERNAME, "password": ADMIN_INITIAL_PASSWORD},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"


def test_logs_do_not_contain_credentials(
    admin_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    secret_password = "log-secret-password-123"
    admin_client.post(
        "/auth/login",
        json={"login_name": ADMIN_USERNAME, "password": secret_password},
    )
    login = admin_client.post(
        "/auth/login",
        json={"login_name": ADMIN_USERNAME, "password": ADMIN_INITIAL_PASSWORD},
    )
    token = login.json()["csrf_token"]
    raw_session = admin_client.cookies.get("only_stock_session")
    changed = admin_client.post(
        "/auth/change-password",
        json={
            "old_password": ADMIN_INITIAL_PASSWORD,
            "new_password": "log-new-password-123",
        },
        headers={"X-CSRF-Token": token},
    )
    new_token = changed.json()["csrf_token"]
    created = admin_client.post(
        "/api/v1/api-keys",
        json={"name": "log-check"},
        headers={"X-CSRF-Token": new_token, "Authorization": "Bearer must-not-be-logged"},
    )
    raw_key = created.json()["key"]

    log_text = caplog.text
    for secret in (
        secret_password,
        ADMIN_INITIAL_PASSWORD,
        "log-new-password-123",
        raw_session or "",
        str(token),
        str(new_token),
        raw_key,
        "must-not-be-logged",
    ):
        assert secret not in log_text


def test_api_key_authentication_rejects_revoked_disabled_and_unknown_keys(
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
) -> None:
    user = user_factory("api-key-auth")
    key_row, raw_key = create_api_key(session_factory, user.id, "auth-check")
    with session_factory() as session:
        authenticated = authenticate_api_key(session, raw_key)
        session.commit()
    assert authenticated is not None
    assert authenticated.user.id == user.id

    with session_factory() as session:
        assert authenticate_api_key(session, raw_key + "tampered") is None
        assert authenticate_api_key(session, "not-a-key") is None

    revoke_api_key(session_factory, user.id, key_row.id)
    with session_factory() as session:
        assert authenticate_api_key(session, raw_key) is None

    second_row, second_raw = create_api_key(session_factory, user.id, "disabled-check")
    assert second_row.id
    with session_factory() as session:
        stored = session.get(User, user.id)
        assert stored is not None
        stored.status = "DISABLED"
        session.commit()
    with session_factory() as session:
        assert authenticate_api_key(session, second_raw) is None


def test_reset_password_revokes_all_sessions_and_keys(
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    user = user_factory("reset-target")
    with session_factory() as session:
        create_session(session, user, settings)
        session.commit()
    key_row, raw_key = create_api_key(session_factory, user.id, "reset-key")
    new_password = "reset-password-456"

    updated = reset_password(session_factory, user.id, new_password)

    assert updated.force_password_change is True
    assert verify_password(updated.password_hash, new_password) is True
    assert verify_password(updated.password_hash, "operator-password-123") is False
    assert new_password not in str(updated.__dict__)

    with session_factory() as session:
        sessions = list(session.scalars(select(AuthSession)).all())
        keys = list(session.scalars(select(ApiKey)).all())
    assert all(row.revoked_at is not None for row in sessions)
    assert all(row.revoked_at is not None for row in keys)
    assert keys[0].revoke_reason == "PASSWORD_RESET"
    assert key_row.id and raw_key


def test_role_change_revokes_credentials(
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    user = user_factory("state-change")
    with session_factory() as session:
        create_session(session, user, settings)
        session.commit()
    key_row, _ = create_api_key(session_factory, user.id, "state-key")

    sessions_revoked, keys_revoked = revoke_credentials_for_user(
        session_factory, user.id, "ROLE_CHANGED"
    )

    assert sessions_revoked == 1
    assert keys_revoked == 1
    with session_factory() as session:
        session_row = session.scalars(select(AuthSession)).one()
        key = session.get(ApiKey, key_row.id)
    assert session_row.revoked_at is not None
    assert key is not None and key.revoked_at is not None
    assert key.revoke_reason == "ROLE_CHANGED"


def test_invalid_old_password_does_not_revoke_keys(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    created = client.post(
        "/api/v1/api-keys", json={"name": "keep-on-failure"}, headers=_csrf(identity)
    )
    key_id = created.json()["id"]

    response = client.post(
        "/auth/change-password",
        json={"old_password": "wrong-old-password", "new_password": "new-password-456"},
        headers=_csrf(identity),
    )

    assert response.status_code == 400
    with session_factory() as session:
        key = session.get(ApiKey, key_id)
    assert key is not None and key.revoked_at is None


def test_forced_change_account_cannot_create_api_key(admin_client: TestClient) -> None:
    login = admin_client.post(
        "/auth/login",
        json={"login_name": ADMIN_USERNAME, "password": ADMIN_INITIAL_PASSWORD},
    )
    token = login.json()["csrf_token"]

    response = admin_client.post(
        "/api/v1/api-keys", json={"name": "blocked"}, headers={"X-CSRF-Token": token}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"


def test_change_password_service_rejects_invalid_session(
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    user = user_factory("wrong-old")
    with session_factory() as session:
        stored = session.get(User, user.id)
        assert stored is not None
        before_hash = stored.password_hash

    with pytest.raises(AppError) as error:
        change_password(
            session_factory,
            settings,
            "missing-session-token",
            "operator-password-123",
            "new-password-456",
        )
    assert error.value.status_code == 401

    with session_factory() as session:
        after = session.get(User, user.id)
    assert after is not None
    assert after.password_hash == before_hash


def test_user_state_change_and_credential_revocation_share_one_transaction(
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    user = user_factory("transactional-state")
    with session_factory() as session:
        create_session(session, user, settings)
        session.commit()
    key_row, _ = create_api_key(session_factory, user.id, "transaction-key")

    with session_factory() as session, begin_immediate(session):
        stored = session.get(User, user.id)
        assert stored is not None
        apply_user_state_change(
            session,
            stored,
            role=Role.COMPANY_ADMIN,
            now=utc_now(),
        )

    with session_factory() as session:
        stored = session.get(User, user.id)
        session_row = session.scalars(select(AuthSession)).one()
        key = session.get(ApiKey, key_row.id)
    assert stored is not None
    assert stored.role == Role.COMPANY_ADMIN.value
    assert stored.version == 2
    assert session_row.revoked_at is not None
    assert key is not None and key.revoked_at is not None
    assert key.revoke_reason == "ROLE_CHANGED"


def test_disable_user_state_change_revokes_credentials(
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
) -> None:
    user = user_factory("disable-state")

    with session_factory() as session, begin_immediate(session):
        stored = session.get(User, user.id)
        assert stored is not None
        apply_user_state_change(
            session,
            stored,
            status=UserStatus.DISABLED,
            now=utc_now(),
        )

    with session_factory() as session:
        stored = session.get(User, user.id)
    assert stored is not None
    assert stored.status == UserStatus.DISABLED.value
    assert stored.version == 2


def test_login_rechecks_password_hash_under_write_lock(
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = user_factory("race-login")

    def racing_verify(password_hash: str, password: str) -> bool:
        with session_factory() as session, begin_immediate(session):
            stored = session.get(User, user.id)
            assert stored is not None
            stored.password_hash = hash_password("concurrent-reset-password-456")
        return True

    monkeypatch.setattr("app.auth.account.verify_password", racing_verify)

    with pytest.raises(AppError) as error:
        authenticate_and_create_session(
            session_factory,
            settings,
            user.login_name,
            "operator-password-123",
        )

    assert error.value.code == "INVALID_CREDENTIALS"
    with session_factory() as session:
        assert session.scalar(select(AuthSession)) is None
