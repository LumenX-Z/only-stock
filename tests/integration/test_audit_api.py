"""审计日志 API 与只追加约束集成测试。"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.types import utc_now
from app.modules.audit.models import AuditLog
from app.modules.users.models import Role, User, UserStatus


def _csrf(identity: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": str(identity["csrf_token"])}


def _audit_actions(client: TestClient, identity: dict[str, Any], **params: Any) -> list[str]:
    response = client.get("/api/v1/audit-logs", params=params)
    assert response.status_code == 200, response.text
    return [item["action"] for item in response.json()["items"]]


def test_login_user_and_masterdata_changes_are_audited(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin

    created_user = client.post(
        "/api/v1/users",
        json={
            "login_name": "audit-target",
            "display_name": "审计目标",
            "role": "OPERATOR",
            "initial_password": "initial-password-123",
        },
        headers=_csrf(identity),
    )
    assert created_user.status_code == 201
    warehouse = client.post("/api/v1/warehouses", json={"name": "审计仓"}, headers=_csrf(identity))
    assert warehouse.status_code == 201
    assert (
        client.post(
            f"/api/v1/warehouses/{warehouse.json()['id']}/disable", headers=_csrf(identity)
        ).status_code
        == 200
    )

    actions = _audit_actions(client, identity, entity_type="warehouse")
    assert actions == ["WAREHOUSE_DISABLED", "WAREHOUSE_CREATED"]
    assert "USER_CREATED" in _audit_actions(client, identity, entity_type="user")
    assert "LOGIN_SUCCEEDED" in _audit_actions(client, identity, action="LOGIN_SUCCEEDED")


def test_audit_filters_and_pagination(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    for index in range(3):
        assert (
            client.post(
                "/api/v1/projects", json={"name": f"项目-{index}"}, headers=_csrf(identity)
            ).status_code
            == 201
        )

    page = client.get("/api/v1/audit-logs?page=1&page_size=2")
    filtered = client.get("/api/v1/audit-logs?entity_type=project&action=PROJECT_CREATED")
    by_source = client.get("/api/v1/audit-logs?source=API")

    assert page.status_code == 200
    assert page.json()["total"] >= 3
    assert len(page.json()["items"]) == 2
    assert filtered.json()["total"] == 3
    assert all(item["entity_type"] == "project" for item in filtered.json()["items"])
    assert by_source.status_code == 200
    assert by_source.json()["total"] == page.json()["total"]


def test_audit_does_not_contain_credentials(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    secret_password = "audit-secret-password-123"
    created = client.post(
        "/api/v1/users",
        json={
            "login_name": "secret-user",
            "display_name": "secret",
            "role": "OPERATOR",
            "initial_password": secret_password,
        },
        headers=_csrf(identity),
    )
    assert created.status_code == 201
    key_response = client.post(
        "/api/v1/api-keys", json={"name": "audit-key"}, headers=_csrf(identity)
    )
    raw_key = key_response.json()["key"]

    with session_factory() as session:
        rows = list(session.scalars(select(AuditLog)).all())
    dumped = " ".join(
        f"{row.action} {row.before_json or ''} {row.after_json or ''}" for row in rows
    )
    assert secret_password not in dumped
    assert raw_key not in dumped
    assert "password_hash" not in dumped
    assert "key_hash" not in dumped


def test_audit_logs_are_append_only(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    assert (
        client.post(
            "/api/v1/departments", json={"name": "审计部"}, headers=_csrf(identity)
        ).status_code
        == 201
    )

    with session_factory() as session:
        audit_id = session.scalar(
            select(AuditLog.id).where(AuditLog.action == "DEPARTMENT_CREATED")
        )
        assert audit_id is not None
        with pytest.raises(IntegrityError, match="append-only"):
            session.execute(
                text("UPDATE audit_logs SET action = 'TAMPERED' WHERE id = :id"),
                {"id": audit_id},
            )
        session.rollback()

    with session_factory() as session:
        with pytest.raises(IntegrityError, match="append-only"):
            session.execute(text("DELETE FROM audit_logs"))
        session.rollback()

    with session_factory() as session:
        row = session.get(AuditLog, audit_id)
    assert row is not None
    assert row.action == "DEPARTMENT_CREATED"


def test_reset_password_and_key_lifecycle_are_audited(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    user_factory: Callable[..., User],
) -> None:
    client, identity = logged_in_admin
    target = user_factory("audit-reset", force_password_change=False)

    reset = client.post(
        f"/api/v1/users/{target.id}/reset-password",
        json={"new_password": "reset-password-456"},
        headers=_csrf(identity),
    )
    assert reset.status_code == 200
    created_key = client.post(
        "/api/v1/api-keys", json={"name": "lifecycle"}, headers=_csrf(identity)
    )
    revoked = client.post(
        f"/api/v1/api-keys/{created_key.json()['id']}/revoke", headers=_csrf(identity)
    )

    assert revoked.status_code == 200
    assert "PASSWORD_RESET" in _audit_actions(client, identity, action="PASSWORD_RESET")
    assert "API_KEY_CREATED" in _audit_actions(client, identity, action="API_KEY_CREATED")
    assert "API_KEY_REVOKED" in _audit_actions(client, identity, action="API_KEY_REVOKED")


def test_raw_audit_insert_requires_known_source(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
) -> None:
    user = user_factory("source-check", role=Role.OPERATOR, status=UserStatus.ACTIVE)
    with session_factory() as session:
        session.add(
            AuditLog(
                actor_user_id=user.id,
                action="TEST",
                entity_type="user",
                entity_id=user.id,
                before_json=None,
                after_json=None,
                request_id=None,
                source="UNKNOWN",
                api_key_id=None,
                created_at=utc_now(),
                id="audit-id",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_audit_api_rejects_operator(
    admin_client: TestClient,
    user_factory: Callable[..., User],
) -> None:
    user_factory("audit-operator", role=Role.OPERATOR)
    admin_client.post(
        "/auth/login",
        json={"login_name": "audit-operator", "password": "operator-password-123"},
    )

    assert admin_client.get("/api/v1/audit-logs").status_code == 403


def test_audit_actor_and_source_fields(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    client.post("/api/v1/warehouses", json={"name": "来源仓"}, headers=_csrf(identity))

    response = client.get("/api/v1/audit-logs?entity_type=warehouse")
    item = response.json()["items"][0]

    assert item["source"] == "API"
    assert item["actor_user_id"] == identity["id"]
    assert item["request_id"]
    assert item["after"]["name"] == "来源仓"
    assert item["before"] is None
