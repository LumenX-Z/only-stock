"""MCP 认证、Host/Origin、限流与凭证脱敏测试。

依据 docs/04-认证、会话与凭证安全.md 5.5、docs/08-MCP服务与工具.md 8.6.1 与
docs/16-验收标准.md ACC-MCP-003、ACC-MCP-012。
"""

import hashlib
import logging
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api_keys import API_KEY_PREFIX, create_api_key, revoke_api_key
from app.auth.login_names import validate_login_name
from app.auth.models import ApiKey
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.sqlite import begin_immediate
from app.db.types import utc_now
from app.main import create_app
from app.modules.audit.models import AuditLog
from app.modules.users.models import Role, User, UserStatus
from tests.conftest import MCPAccounts
from tests.mcp_support import MCP_HOST, initialize_body

_HEADERS = {
    "Host": MCP_HOST,
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _post(client: TestClient, key: str | None = None, **overrides: str) -> object:
    headers = dict(_HEADERS)
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    headers.update(overrides)
    return client.post("/mcp", json=initialize_body(), headers=headers)


def test_mcp_is_mounted_on_the_same_app_without_legacy_sse(app: FastAPI) -> None:
    openapi_paths = set(app.openapi()["paths"])
    assert "/health/ready" in openapi_paths
    assert "/mcp" not in openapi_paths
    assert app.state.mcp.session_manager is not None

    with TestClient(app) as client:
        assert client.get("/sse").status_code == 404
        assert client.get("/mcp/sse").status_code == 404


def test_missing_wrong_revoked_and_disabled_keys_return_401(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    session_factory: sessionmaker[Session],
) -> None:
    revoked = revoke_api_key(
        session_factory,
        mcp_accounts.op.id,
        _key_id(session_factory, mcp_accounts.op_key),
    )
    assert revoked.revoked_at is not None
    disabled_user, disabled_key = _create_user_with_key(session_factory, "mcp-disabled-user")
    _disable_user(session_factory, disabled_user.id)

    with TestClient(app) as client:
        responses = [
            _post(client),
            _post(client, "osk_not-a-real-key"),
            _post(client, mcp_accounts.op_key),
            _post(client, disabled_key),
        ]

    for response in responses:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
        assert response.json()["error"]["details"]["resource"] == "api_key"


def test_cookie_cannot_authenticate_mcp(app: FastAPI, user_factory: Callable[..., User]) -> None:
    user_factory(
        "cookie-user",
        password="cookie-password-123",
        force_password_change=False,
    )

    with TestClient(app) as client:
        login = client.post(
            "/auth/login",
            json={"login_name": "cookie-user", "password": "cookie-password-123"},
        )
        assert login.status_code == 200
        assert "only_stock_session" in client.cookies

        response = client.post("/mcp", json=initialize_body(), headers=dict(_HEADERS))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_unknown_host_and_origin_are_rejected(app: FastAPI, mcp_accounts: MCPAccounts) -> None:
    with TestClient(app) as client:
        bad_host = _post(client, mcp_accounts.ca_key, Host="evil.example")
        bad_origin = _post(client, mcp_accounts.ca_key, Origin="http://evil.example")
        good_origin = _post(client, mcp_accounts.ca_key, Origin="http://127.0.0.1:8000")

    assert bad_host.status_code == 421
    assert bad_host.json()["error"]["code"] == "INVALID_HOST"
    assert bad_origin.status_code == 403
    assert bad_origin.json()["error"]["code"] == "INVALID_ORIGIN"
    assert good_origin.status_code == 200


def test_key_rate_limit_returns_429(
    env: dict[str, str],
    mcp_accounts: MCPAccounts,
) -> None:
    settings = Settings.from_env(
        {**env, "MCP_KEY_RATE_PER_MINUTE": "2", "MCP_IP_RATE_PER_MINUTE": "1000"}
    )
    app = create_app(settings)

    with TestClient(app) as client:
        first = _post(client, mcp_accounts.ca_key)
        second = _post(client, mcp_accounts.ca_key)
        limited = _post(client, mcp_accounts.ca_key)

    assert first.status_code == 200
    assert second.status_code == 200
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["Retry-After"]) >= 1


def test_ip_rate_limit_applies_before_key_limit(
    env: dict[str, str],
    mcp_accounts: MCPAccounts,
) -> None:
    settings = Settings.from_env(
        {**env, "MCP_KEY_RATE_PER_MINUTE": "1000", "MCP_IP_RATE_PER_MINUTE": "2"}
    )
    app = create_app(settings)

    with TestClient(app) as client:
        assert _post(client, mcp_accounts.ca_key).status_code == 200
        assert _post(client, mcp_accounts.op_key).status_code == 200
        limited = _post(client, mcp_accounts.ca_key)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"


def test_request_body_size_is_limited(app: FastAPI, mcp_accounts: MCPAccounts) -> None:
    with TestClient(app) as client:
        oversized = client.post(
            "/mcp",
            content=b"{" + b" " * (4 * 1024 * 1024 + 1) + b"}",
            headers={
                **_HEADERS,
                "Authorization": f"Bearer {mcp_accounts.ca_key}",
            },
        )

    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_session_is_bound_to_the_credential_that_created_it(
    app: FastAPI, mcp_accounts: MCPAccounts
) -> None:
    with TestClient(app) as client:
        opened = _post(client, mcp_accounts.ca_key)
        assert opened.status_code == 200
        session_id = opened.headers["mcp-session-id"]
        other = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={
                **_HEADERS,
                "Authorization": f"Bearer {mcp_accounts.op_key}",
                "MCP-Session-Id": session_id,
            },
        )
        same = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            headers={
                **_HEADERS,
                "Authorization": f"Bearer {mcp_accounts.ca_key}",
                "MCP-Session-Id": session_id,
            },
        )

    assert other.status_code == 404
    assert same.status_code == 200


def test_logs_and_audit_do_not_contain_full_key(
    app: FastAPI,
    mcp_accounts: MCPAccounts,
    session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        assert _post(client, mcp_accounts.ca_key).status_code == 200

    assert mcp_accounts.ca_key not in caplog.text
    with session_factory() as session:
        for row in session.scalars(select(AuditLog)).all():
            assert mcp_accounts.ca_key not in str(row.__dict__)
            assert mcp_accounts.ca_key not in str(row.before_json)
            assert mcp_accounts.ca_key not in str(row.after_json)


def _create_user_with_key(
    session_factory: sessionmaker[Session],
    login_name: str,
) -> tuple[User, str]:
    now = utc_now()
    with session_factory() as session, begin_immediate(session):
        user = User(
            login_name=validate_login_name(login_name),
            password_hash=hash_password("disabled-password-123"),
            display_name=login_name,
            role=Role.OPERATOR.value,
            status=UserStatus.ACTIVE.value,
            force_password_change=False,
            password_version=1,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
        session.expunge(user)
    _, raw_key = create_api_key(session_factory, user.id, "disabled-mcp")
    return user, raw_key


def _disable_user(session_factory: sessionmaker[Session], user_id: str) -> None:
    with session_factory() as session, begin_immediate(session):
        user = session.get(User, user_id)
        assert user is not None
        user.status = UserStatus.DISABLED.value


def _key_id(session_factory: sessionmaker[Session], raw_key: str) -> str:
    assert raw_key.startswith(API_KEY_PREFIX)
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    with session_factory() as session:
        row = session.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))
    assert row is not None
    return row.id
