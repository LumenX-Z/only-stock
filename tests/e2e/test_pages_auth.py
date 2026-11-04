"""页面登录、强制改密、布局导航与角色可见性。"""

from typing import Any

from fastapi.testclient import TestClient

from app.modules.users.models import Role
from tests.page_support import change_password_page, login_page, post_page
from tests.support import operator_session

OPERATOR_PASSWORD = "operator-password-123"
OPERATOR_NEW_PASSWORD = "operator-password-456"


def test_login_page_redirects_and_shows_errors(client: TestClient) -> None:
    landing = client.get("/", follow_redirects=False)
    assert landing.status_code == 303
    assert landing.headers["location"].startswith("/login")

    page = client.get("/login")
    assert page.status_code == 200
    assert "欢迎回来" in page.text

    wrong = login_page(client, "admin", "wrong-password-123")
    assert wrong.status_code == 401
    assert "登录名或密码不正确" in wrong.text


def test_forced_password_change_flow(
    client: TestClient,
    user_factory: Any,
) -> None:
    user = user_factory(
        "page-forced",
        password=OPERATOR_PASSWORD,
        role=Role.OPERATOR,
        force_password_change=True,
    )
    login = login_page(client, user.login_name, OPERATOR_PASSWORD)
    assert login.status_code == 303
    assert login.headers["location"] == "/"

    redirected = client.get("/", follow_redirects=False)
    assert redirected.status_code == 303
    assert redirected.headers["location"] == "/change-password"

    changed = change_password_page(client, OPERATOR_PASSWORD, OPERATOR_NEW_PASSWORD)
    assert changed.status_code == 303
    assert changed.headers["location"] == "/"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "工作台" in dashboard.text
    assert "OnlyStock" in dashboard.text


def test_operator_navigation_hides_admin_entries_and_backend_rejects(
    admin_app: Any,
    admin_client: TestClient,
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    _, identity = logged_in_admin
    with operator_session(admin_app, admin_client, identity, login_name="page-op") as (
        operator_client,
        _operator_identity,
    ):
        dashboard = operator_client.get("/")
        assert dashboard.status_code == 200
        assert 'href="/users"' not in dashboard.text
        assert 'href="/audit"' not in dashboard.text
        assert 'href="/backups"' not in dashboard.text
        assert 'href="/api-keys"' in dashboard.text

        masterdata = operator_client.get("/masterdata/warehouses")
        assert masterdata.status_code == 200
        assert "只读视图" in masterdata.text
        assert 'id="create-warehouses"' not in masterdata.text

        forbidden_create = post_page(
            operator_client,
            "/masterdata/warehouses",
            {"name": "操作员越权仓库"},
            csrf_page="/masterdata/warehouses",
        )
        assert forbidden_create.status_code == 403
        assert "无权执行该操作" in forbidden_create.text

        for path in ("/users", "/audit", "/backups"):
            response = operator_client.get(path)
            assert response.status_code == 403, path
            assert "无权执行该操作" in response.text

    admin_dashboard = admin_client.get("/")
    assert admin_dashboard.status_code == 200
    assert 'href="/users"' in admin_dashboard.text
    assert 'href="/audit"' in admin_dashboard.text
    assert 'href="/backups"' in admin_dashboard.text
