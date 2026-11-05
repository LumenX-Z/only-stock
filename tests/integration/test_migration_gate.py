"""迁移未完成时的业务闸门与恢复后的就绪状态。"""

from uuid import UUID

from fastapi.testclient import TestClient

from app.config import Settings
from app.db.migration_runner import run_migrations
from app.main import create_app


def test_unmigrated_database_blocks_business_requests(
    settings: Settings,
) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        live = client.get("/health/live")
        assert live.status_code == 200

        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert "migrations" in ready.json()["failed"]

        blocked = client.get("/api/v1/warehouses")
        assert blocked.status_code == 503
        body = blocked.json()["error"]
        assert body["code"] == "SYSTEM_NOT_READY"
        assert body["details"]["resource"] is None
        assert body["details"]["items"] == []
        assert UUID(blocked.headers["X-Request-ID"])

        login = client.post(
            "/auth/login",
            json={"login_name": "nobody", "password": "long-enough-password"},
        )
        assert login.status_code == 503
        assert login.json()["error"]["code"] == "SYSTEM_NOT_READY"

        # 健康检查和 OpenAPI 不依赖业务表，保持可用。
        assert client.get("/openapi.json").status_code == 200


def test_business_requests_resume_after_migration(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/warehouses").status_code == 503

    run_migrations(settings)

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        assert client.get("/health/ready").status_code == 200
        # 闸门已打开，未认证请求回到正常的 401，而不是 503。
        assert client.get("/api/v1/warehouses").status_code == 401
