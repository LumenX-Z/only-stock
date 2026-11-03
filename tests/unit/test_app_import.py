"""基线冒烟测试：确认应用入口可导入。"""

from app.main import app


def test_app_importable() -> None:
    assert app.title == "only-stock"
