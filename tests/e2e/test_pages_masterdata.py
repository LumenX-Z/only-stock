"""基础资料页面：创建、名称冲突、编码只读与分类停用/启用边界。"""

import re
from typing import Any

from fastapi.testclient import TestClient

from tests.page_support import create_masterdata, post_page


def _entity_id(html: str, resource: str, name: str) -> str:
    pattern = rf"/masterdata/{resource}/([0-9a-f-]{{36}})/disable"
    for match in re.finditer(pattern, html):
        start = max(0, match.start() - 600)
        chunk = html[start : match.start()]
        if name in chunk:
            return match.group(1)
    raise AssertionError(f"未找到 {resource} {name} 的操作 ID")


def test_warehouse_create_conflict_and_code_readonly(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin

    page = client.get("/masterdata/warehouses")
    assert page.status_code == 200
    create_section = page.text.split('id="create-warehouses"', 1)[1]
    assert 'name="code"' not in create_section
    assert "新建仓库" in page.text

    created = create_masterdata(
        client,
        "warehouses",
        {"name": "页面仓库", "location": "B 区"},
    )
    assert created.status_code == 303

    listing = client.get("/masterdata/warehouses")
    assert "页面仓库" in listing.text
    assert re.search(r"CK-\d{5}", listing.text)
    assert "B 区" in listing.text

    conflict = create_masterdata(
        client,
        "warehouses",
        {"name": "页面仓库", "location": "另一个位置"},
    )
    assert conflict.status_code == 409
    assert "NAME_CONFLICT" in conflict.text
    assert "页面仓库" in conflict.text
    assert "启用" in conflict.text
    assert 'value="页面仓库"' in conflict.text


def test_category_material_blockers_and_parent_enable(
    logged_in_admin: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = logged_in_admin

    assert create_masterdata(client, "categories", {"name": "页面一级"}).status_code == 303
    categories = client.get("/masterdata/categories")
    root_id = _entity_id(categories.text, "categories", "页面一级")

    assert (
        create_masterdata(
            client,
            "categories",
            {"name": "页面二级", "parent_id": root_id},
        ).status_code
        == 303
    )
    categories = client.get("/masterdata/categories")
    child_id = _entity_id(categories.text, "categories", "页面二级")
    assert "页面一级 / 页面二级" not in categories.text  # 树形展示不是路径文本
    assert "所属一级分类：页面一级" in categories.text

    material_page = client.get("/masterdata/materials")
    category_options = re.findall(
        r'<option value="([0-9a-f-]{36})"[^>]*>页面一级 / 页面二级',
        material_page.text,
    )
    assert category_options
    created_material = create_masterdata(
        client,
        "materials",
        {
            "name": "页面物资",
            "category_id": category_options[0],
            "unit": "千克",
            "quantity_type": "DECIMAL",
        },
    )
    assert created_material.status_code == 303
    materials = client.get("/masterdata/materials")
    assert "页面物资" in materials.text
    assert "页面一级 / 页面二级" in materials.text
    assert re.search(r"WZ-\d{5}", materials.text)

    blocked = post_page(
        client,
        f"/masterdata/categories/{root_id}/disable",
        {},
        csrf_page="/masterdata/categories",
    )
    assert blocked.status_code == 303
    after_block = client.get("/masterdata/categories")
    assert "停用失败" in after_block.text
    assert "页面二级" in after_block.text
    material_id = _entity_id(materials.text, "materials", "页面物资")

    assert (
        post_page(
            client,
            f"/masterdata/materials/{material_id}/disable",
            {},
            csrf_page="/masterdata/materials",
        ).status_code
        == 303
    )
    assert (
        post_page(
            client,
            f"/masterdata/categories/{child_id}/disable",
            {},
            csrf_page="/masterdata/categories",
        ).status_code
        == 303
    )
    assert (
        post_page(
            client,
            f"/masterdata/categories/{root_id}/disable",
            {},
            csrf_page="/masterdata/categories",
        ).status_code
        == 303
    )
    disabled = client.get("/masterdata/categories")
    assert disabled.text.count("已停用") >= 2

    enable_child = post_page(
        client,
        f"/masterdata/categories/{child_id}/enable",
        {},
        csrf_page="/masterdata/categories",
    )
    assert enable_child.status_code == 303
    blocked_enable = client.get("/masterdata/categories")
    assert "启用失败" in blocked_enable.text
    assert "前往启用上级分类" in blocked_enable.text
    assert "PARENT_INACTIVE" not in blocked_enable.text  # 页面展示中文原因与上级编码

    enable_root = post_page(
        client,
        f"/masterdata/categories/{root_id}/enable",
        {},
        csrf_page="/masterdata/categories",
    )
    assert enable_root.status_code == 303
    enable_child_again = post_page(
        client,
        f"/masterdata/categories/{child_id}/enable",
        {},
        csrf_page="/masterdata/categories",
    )
    assert enable_child_again.status_code == 303
    enabled = client.get("/masterdata/categories")
    assert "启用失败" not in enabled.text
