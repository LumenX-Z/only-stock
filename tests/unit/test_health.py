import shutil
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings


def test_live_returns_ok(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_checks_database_migrations_directories_and_scheduler(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {
        "data_dir": True,
        "backup_dir": True,
        "database": True,
        "migrations": True,
        "backup_scheduler": True,
    }


def test_ready_does_not_leak_paths(client: TestClient, settings: Settings) -> None:
    response = client.get("/health/ready")

    assert str(settings.data_dir) not in response.text
    assert str(settings.database_path) not in response.text
    assert "inventory.sqlite3" not in response.text


def test_request_id_is_generated_and_not_taken_from_client(client: TestClient) -> None:
    first = client.get("/health/live")
    second = client.get("/health/live", headers={"X-Request-ID": "attacker-supplied"})

    first_id = first.headers["X-Request-ID"]
    second_id = second.headers["X-Request-ID"]
    UUID(first_id)
    UUID(second_id)
    assert second_id != "attacker-supplied"
    assert first_id != second_id


def test_live_does_not_access_database(client: TestClient, settings: Settings) -> None:
    shutil.rmtree(settings.data_dir)

    assert client.get("/health/live").status_code == 200


def test_ready_reports_missing_data_directory(client: TestClient, settings: Settings) -> None:
    shutil.rmtree(settings.data_dir)

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert "data_dir" in body["failed"]
    assert str(settings.data_dir) not in response.text


def test_ready_reports_stopped_backup_scheduler(client: TestClient, app: FastAPI) -> None:
    app.state.runtime.scheduler.shutdown()

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert "backup_scheduler" in response.json()["failed"]
