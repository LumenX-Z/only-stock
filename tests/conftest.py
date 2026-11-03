"""P1 测试夹具。

所有测试使用 tmp_path 下的独立数据/备份目录，不读写开发数据库
（./data/inventory.sqlite3）或真实备份目录。
"""

import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.config import Settings
from app.db.database import create_engine_from_settings
from app.main import create_app


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {
        "APP_ENV": "test",
        "DATA_DIR": str(tmp_path / "data"),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "MCP_ALLOWED_HOSTS": "127.0.0.1,localhost",
        "BOOTSTRAP_ADMIN_USERNAME": "",
        "BOOTSTRAP_ADMIN_PASSWORD": "",
    }


@pytest.fixture
def settings(env: dict[str, str]) -> Settings:
    return Settings.from_env(env)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_engine(settings: Settings) -> Iterator[Engine]:
    settings.prepare_directories()
    engine = create_engine_from_settings(settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def run_concurrently() -> Callable[[Sequence[Callable[[], object]]], list[object]]:
    """并发测试辅助：所有任务用 Barrier 对齐后同时执行。"""

    def run(callables: Sequence[Callable[[], object]]) -> list[object]:
        barrier = threading.Barrier(len(callables))

        def invoke(callable_: Callable[[], object]) -> object:
            barrier.wait()
            return callable_()

        with ThreadPoolExecutor(max_workers=len(callables)) as executor:
            futures = [executor.submit(invoke, callable_) for callable_ in callables]
            return [future.result() for future in futures]

    return run
