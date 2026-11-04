"""用户管理页面：创建、编辑、禁用、重置密码与最后管理员保护。"""

import re
from typing import Any

from fastapi.testclient import TestClient

from app.modules.users.models import Role
from tests.page_support import csrf_for, login_page, post_page


def test_user_page_create_edit_disable_and_reset(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin

    page = client.get("/users")
    assert page.status_code == 200
    assert "创建用户" in page.text

    created = post_page(
        client,
        "/users",
        {
            "login_name": "page-user",
            "display_name": "页面用户",
            "role": Role.OPERATOR.value,
            "initial_password": "initial-password-123",
        },
        csrf_page="/users",
    )
    assert created.status_code == 303

    listing = client.get("/users", params={"login_name": "page-user"})
    assert "page-user" in listing.text
    assert "页面用户" in listing.text
    user_id = re.search(r"/users/([0-9a-f-]{36})/edit", listing.text).group(1)

    edit_page = client.get(f"/users/{user_id}/edit")
    assert edit_page.status_code == 200
    version = re.search(r'name="version" value="(\d+)"', edit_page.text).group(1)
    updated = post_page(
        client,
        f"/users/{user_id}/edit",
        {
            "display_name": "页面用户改名",
            "role": Role.OPERATOR.value,
            "version": version,
        },
        csrf_page=f"/users/{user_id}/edit",
    )
    assert updated.status_code == 303
    assert "页面用户改名" in client.get("/users").text

    reset = post_page(
        client,
        f"/users/{user_id}/reset-password",
        {"new_password": "reset-password-456"},
        csrf_page="/users",
    )
    assert reset.status_code == 303
    after_reset = client.get("/users")
    assert "密码已重置" in after_reset.text

    disabled = post_page(
        client,
        f"/users/{user_id}/disable",
        {},
        csrf_page="/users",
    )
    assert disabled.status_code == 303
    assert "已禁用" in client.get("/users").text


def test_last_admin_cannot_be_disabled_from_page(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, identity = logged_in_admin
    token = csrf_for(client, "/users")

    response = client.post(
        f"/users/{identity['id']}/disable",
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/users")
    assert "禁用失败" in page.text
    assert "至少保留一名启用的公司管理员" in page.text


def test_reset_password_forces_change_on_next_page_login(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
    client: TestClient,
) -> None:
    admin_client, _ = logged_in_admin
    created = post_page(
        admin_client,
        "/users",
        {
            "login_name": "page-reset",
            "display_name": "重置用户",
            "role": Role.OPERATOR.value,
            "initial_password": "initial-password-123",
        },
        csrf_page="/users",
    )
    assert created.status_code == 303
    listing = admin_client.get("/users", params={"login_name": "page-reset"})
    match = re.search(r"/users/([0-9a-f-]{36})/edit", listing.text)
    assert match
    user_id = match.group(1)

    assert (
        post_page(
            admin_client,
            f"/users/{user_id}/reset-password",
            {"new_password": "reset-password-456"},
            csrf_page="/users",
        ).status_code
        == 303
    )

    login = login_page(client, "page-reset", "reset-password-456")
    assert login.status_code == 303
    redirected = client.get("/", follow_redirects=False)
    assert redirected.status_code == 303
    assert redirected.headers["location"] == "/change-password"
