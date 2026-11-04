"""页面端到端测试辅助：从 HTML 提取 CSRF、提交表单与页面登录。"""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient

_CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


def csrf_from(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match, "页面未渲染 CSRF 隐藏字段"
    return match.group(1)


def csrf_for(client: TestClient, path: str = "/") -> str:
    response = client.get(path)
    assert response.status_code == 200, response.text
    return csrf_from(response.text)


def login_page(
    client: TestClient,
    login_name: str,
    password: str,
    *,
    next_url: str = "/",
) -> Any:
    return client.post(
        "/login",
        data={"login_name": login_name, "password": password, "next": next_url},
        follow_redirects=False,
    )


def change_password_page(
    client: TestClient,
    old_password: str,
    new_password: str,
) -> Any:
    page = client.get("/change-password")
    assert page.status_code == 200, page.text
    return client.post(
        "/change-password",
        data={
            "old_password": old_password,
            "new_password": new_password,
            "confirm_password": new_password,
            "_csrf": csrf_from(page.text),
        },
        follow_redirects=False,
    )


def post_page(
    client: TestClient,
    path: str,
    data: dict[str, Any],
    *,
    csrf_page: str = "/",
    csrf: str | None = None,
) -> Any:
    token = csrf if csrf is not None else csrf_for(client, csrf_page)
    payload = {"_csrf": token, **data}
    return client.post(path, data=payload, follow_redirects=False)


def create_masterdata(
    client: TestClient,
    resource: str,
    data: dict[str, Any],
) -> Any:
    return post_page(
        client,
        f"/masterdata/{resource}",
        data,
        csrf_page=f"/masterdata/{resource}",
    )
