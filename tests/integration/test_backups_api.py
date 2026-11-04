"""备份 API：手动备份、状态查询、失败记录与权限。"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from tests.support import csrf, operator_session


def test_admin_can_create_and_list_backups(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    admin_settings: Settings,
) -> None:
    client, identity = logged_in_admin

    empty = client.get("/api/v1/backups")
    assert empty.status_code == 200
    assert empty.json()["total"] == 0

    created = client.post("/api/v1/backups", headers=csrf(identity))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "SUCCESS"
    assert body["size_bytes"] > 0
    assert len(body["sha256"]) == 64
    assert body["integrity"] == "ok"

    listing = client.get("/api/v1/backups")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == body["id"]
    assert payload["page"] == 1
    assert payload["page_size"] == 20

    manifest = admin_settings.backup_dir / body["id"] / "manifest.json"
    report = admin_settings.backup_dir / body["id"] / "integrity-report.json"
    assert manifest.is_file()
    assert report.is_file()
    assert '"integrity_check": "ok"' in manifest.read_text(encoding="utf-8")


def test_backup_failure_is_recorded_and_listed(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, identity = logged_in_admin

    def fail_copy(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("模拟备份失败")

    monkeypatch.setattr("app.modules.backups.service._copy_database", fail_copy)
    response = client.post("/api/v1/backups", headers=csrf(identity))
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "BACKUP_FAILED"
    assert error["details"]["resource"] == "backup"

    listing = client.get("/api/v1/backups")
    items = listing.json()["items"]
    failed = [item for item in items if item["status"] == "FAILED"]
    assert failed
    assert failed[0]["error"]


def test_operator_cannot_create_or_list_backups(
    admin_app: Any,
    admin_client: TestClient,
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    with operator_session(admin_app, admin_client, logged_in_admin[1], login_name="backup-op") as (
        operator,
        _,
    ):
        assert operator.get("/api/v1/backups").status_code == 403
        operator_csrf = operator.get("/auth/me").json()["csrf_token"]
        rejected = operator.post("/api/v1/backups", headers={"X-CSRF-Token": operator_csrf})
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "FORBIDDEN"
