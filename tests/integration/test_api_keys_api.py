"""本人 API Key 的创建、列表、吊销与上限测试。"""

import hashlib
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api_keys import create_api_key, revoke_api_key
from app.auth.models import ApiKey
from app.errors import AppError
from app.modules.users.models import User


def _csrf(identity: dict[str, object]) -> dict[str, str]:
    return {"X-CSRF-Token": str(identity["csrf_token"])}


def test_create_returns_full_key_once_and_stores_only_hash(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin

    response = client.post(
        "/api/v1/api-keys", json={"name": "  MCP client  "}, headers=_csrf(identity)
    )

    assert response.status_code == 201
    body = response.json()
    raw_key = body["key"]
    assert raw_key.startswith("osk_")
    assert body["name"] == "MCP client"
    assert body["key_suffix"] == raw_key[-4:]
    assert body["revoked_at"] is None

    listed = client.get("/api/v1/api-keys")
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert "key" not in item
    assert item["key_suffix"] == raw_key[-4:]

    with session_factory() as session:
        row = session.scalars(select(ApiKey)).one()
        assert row.key_hash == hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        assert raw_key not in row.key_hash
        assert row.key_hash != raw_key


def test_api_key_limit_is_ten_active_keys(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin

    first_key_id = ""
    for index in range(10):
        response = client.post(
            "/api/v1/api-keys",
            json={"name": f"key-{index}"},
            headers=_csrf(identity),
        )
        assert response.status_code == 201
        if index == 0:
            first_key_id = response.json()["id"]

    rejected = client.post("/api/v1/api-keys", json={"name": "eleventh"}, headers=_csrf(identity))

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "API_KEY_LIMIT_REACHED"
    with session_factory() as session:
        active = session.scalar(
            select(func.count()).select_from(ApiKey).where(ApiKey.revoked_at.is_(None))
        )
    assert active == 10

    assert (
        client.post(f"/api/v1/api-keys/{first_key_id}/revoke", headers=_csrf(identity)).status_code
        == 200
    )
    replacement = client.post(
        "/api/v1/api-keys", json={"name": "replacement"}, headers=_csrf(identity)
    )
    assert replacement.status_code == 201


def test_revoke_is_idempotent_and_list_can_include_revoked(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin
    created = client.post("/api/v1/api-keys", json={"name": "to-revoke"}, headers=_csrf(identity))
    key_id = created.json()["id"]

    first = client.post(f"/api/v1/api-keys/{key_id}/revoke", headers=_csrf(identity))
    second = client.post(f"/api/v1/api-keys/{key_id}/revoke", headers=_csrf(identity))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["revoked_at"] is not None
    assert first.json()["revoked_at"] == second.json()["revoked_at"]
    assert first.json()["revoke_reason"] == "USER_REVOKED"

    assert client.get("/api/v1/api-keys").json()["total"] == 0
    including = client.get("/api/v1/api-keys?include_revoked=true")
    assert including.status_code == 200
    assert including.json()["total"] == 1
    assert including.json()["items"][0]["revoked_at"] is not None

    with session_factory() as session:
        row = session.scalars(select(ApiKey)).one()
    assert row.revoked_at is not None


def test_only_owner_can_see_and_revoke_key(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    user_factory: Callable[..., User],
    session_factory: sessionmaker[Session],
) -> None:
    client, admin_identity = logged_in_admin
    operator = user_factory("key-operator")
    operator_key, _ = create_api_key(session_factory, operator.id, "operator-key")

    admin_list = client.get("/api/v1/api-keys")
    assert admin_list.json()["total"] == 0
    assert admin_identity["id"] != operator.id

    with pytest.raises(AppError) as error:
        revoke_api_key(session_factory, str(admin_identity["id"]), operator_key.id)
    assert error.value.status_code == 404
    assert error.value.code == "API_KEY_NOT_FOUND"


def test_key_name_validation(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    session_factory: sessionmaker[Session],
) -> None:
    client, identity = logged_in_admin

    blank = client.post("/api/v1/api-keys", json={"name": "   "}, headers=_csrf(identity))
    too_long = client.post("/api/v1/api-keys", json={"name": "a" * 51}, headers=_csrf(identity))

    assert blank.status_code == 422
    assert too_long.status_code == 422
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ApiKey)) == 0


def test_create_key_requires_csrf(
    logged_in_admin: tuple[TestClient, dict[str, object]],
    session_factory: sessionmaker[Session],
) -> None:
    client, _ = logged_in_admin

    response = client.post("/api/v1/api-keys", json={"name": "no-csrf"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ApiKey)) == 0


def test_list_pagination_and_filtering(
    logged_in_admin: tuple[TestClient, dict[str, object]],
) -> None:
    client, identity = logged_in_admin
    key_ids = []
    for index in range(3):
        created = client.post(
            "/api/v1/api-keys",
            json={"name": f"page-key-{index}"},
            headers=_csrf(identity),
        )
        key_ids.append(created.json()["id"])
    client.post(f"/api/v1/api-keys/{key_ids[0]}/revoke", headers=_csrf(identity))

    first_page = client.get("/api/v1/api-keys?page=1&page_size=2")
    second_page = client.get("/api/v1/api-keys?page=2&page_size=2")
    all_keys = client.get("/api/v1/api-keys?include_revoked=true&page_size=100")

    assert first_page.json()["total"] == 2
    assert len(first_page.json()["items"]) == 2
    assert second_page.json()["items"] == []
    assert all_keys.json()["total"] == 3
    assert len(all_keys.json()["items"]) == 3
