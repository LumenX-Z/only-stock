"""API Key、审计与备份页面。"""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from tests.page_support import post_page


def test_api_key_page_displays_secret_once_and_revokes(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin

    created = post_page(client, "/api-keys", {"name": "页面 Key"}, csrf_page="/api-keys")
    assert created.status_code == 303
    location = created.headers["location"]
    assert location.startswith("/api-keys?created=")

    shown = client.get(location)
    assert shown.status_code == 200
    assert "API Key 创建成功" in shown.text
    secret_match = re.search(r'id="new-api-key">([^<]+)<', shown.text)
    assert secret_match
    secret = secret_match.group(1)
    assert secret.startswith("osk_")

    refreshed = client.get(location)
    assert refreshed.status_code == 200
    assert "已不可恢复" in refreshed.text
    assert secret not in refreshed.text

    listing = client.get("/api-keys")
    assert secret[-4:] in listing.text
    key_id = re.search(r"/api-keys/([0-9a-f-]{36})/revoke", listing.text).group(1)
    revoked = post_page(
        client,
        f"/api-keys/{key_id}/revoke",
        {},
        csrf_page="/api-keys",
    )
    assert revoked.status_code == 303
    after_revoke = client.get("/api-keys")
    assert "已吊销" in after_revoke.text
    assert secret not in after_revoke.text


def test_audit_page_filters_and_permissions(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin

    page = client.get("/audit")
    assert page.status_code == 200
    assert "审计日志" in page.text
    assert "LOGIN_SUCCEEDED" in page.text

    filtered = client.get("/audit", params={"entity_type": "user", "action": "LOGIN_SUCCEEDED"})
    assert filtered.status_code == 200
    assert "LOGIN_SUCCEEDED" in filtered.text

    invalid_date = client.get("/audit", params={"start_date": "not-a-date"})
    assert invalid_date.status_code == 200


def test_backup_page_creates_set_and_shows_failure(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    admin_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = logged_in_admin

    page = client.get("/backups")
    assert page.status_code == 200
    assert "还没有备份集" in page.text

    created = post_page(client, "/backups", {}, csrf_page="/backups")
    assert created.status_code == 303
    listing = client.get("/backups")
    assert "成功" in listing.text
    assert "integrity" in listing.text.lower() or "ok" in listing.text
    manifests = list(admin_settings.backup_dir.glob("*/manifest.json"))
    assert manifests
    manifest = manifests[0].read_text(encoding="utf-8")
    assert '"integrity_check": "ok"' in manifest
    assert '"sha256"' in manifest

    def fail_copy(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("模拟备份失败")

    monkeypatch.setattr("app.modules.backups.service._copy_database", fail_copy)
    failed = post_page(client, "/backups", {}, csrf_page="/backups")
    assert failed.status_code == 500
    assert "备份失败" in failed.text
    assert "已成功创建" not in failed.text
    failure_records = list((admin_settings.backup_dir / "failures").glob("*.json"))
    assert failure_records
