import importlib

import pytest

_MODULES = [
    "app",
    "app.auth",
    "app.auth.account",
    "app.auth.api_keys",
    "app.auth.dependencies",
    "app.auth.login_names",
    "app.auth.models",
    "app.auth.passwords",
    "app.auth.rate_limit",
    "app.auth.router",
    "app.auth.schemas",
    "app.auth.sessions",
    "app.cli",
    "app.config",
    "app.db",
    "app.db.base",
    "app.db.database",
    "app.db.migrations",
    "app.db.models",
    "app.db.sqlite",
    "app.db.types",
    "app.error_handlers",
    "app.errors",
    "app.health",
    "app.logging",
    "app.main",
    "app.mcp",
    "app.middleware",
    "app.modules",
    "app.modules.audit",
    "app.modules.backups",
    "app.modules.categories",
    "app.modules.departments",
    "app.modules.documents",
    "app.modules.inventory",
    "app.modules.manufacturers",
    "app.modules.materials",
    "app.modules.projects",
    "app.modules.users",
    "app.modules.users.models",
    "app.modules.users.service",
    "app.modules.warehouses",
    "app.readiness",
    "app.request_context",
    "app.runtime",
    "app.services",
    "app.services.backup_scheduler",
]


@pytest.mark.parametrize("module_name", _MODULES)
def test_package_is_importable(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None
