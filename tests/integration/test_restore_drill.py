"""隔离环境恢复演练：备份点 → 追加数据 → 离线恢复 → 登录/库存/单据/MCP 验证。"""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api_keys import create_api_key
from app.config import Settings
from app.main import create_app
from app.modules.backups.restore import restore_backup_set
from app.modules.backups.service import create_backup
from app.modules.documents.service import DocumentFilters, list_documents
from app.modules.inventory.service import list_inventory
from app.modules.users.models import Role, User
from tests.conftest import MasterDataFixture
from tests.mcp_support import mcp_sessions, tool_names
from tests.support import (
    create_document,
    inbound_payload,
    line,
    login,
    outbound_payload,
    submit_document,
)

DRILL_PASSWORD = "drill-password-123"


def _checkpoint(engine: Engine, settings: Settings) -> None:
    engine.dispose()
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def _inventory(session_factory: sessionmaker[Session]) -> list[int]:
    with session_factory() as session:
        rows, _total = list_inventory(session, page=1, page_size=100)
        return [row.balance_milli for row in rows]


def _document_count(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        _rows, _projects, total = list_documents(
            session,
            filters=DocumentFilters(),
            page=1,
            page_size=20,
        )
        return total


@pytest.mark.asyncio
async def test_restore_drill_keeps_login_inventory_documents_and_mcp(
    migrated: Settings,
    migrated_engine: Engine,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    ca = _create_admin(session_factory, "drill-admin")
    _key_id, raw_key = create_api_key(session_factory, ca.id, "drill-mcp")

    app = create_app(migrated)
    with TestClient(app) as client:
        identity = login(client, ca.login_name, DRILL_PASSWORD)
        created = create_document(
            client,
            identity,
            inbound_payload(master_data, lines=[line(master_data, quantity="1.250")]),
        )
        submit_document(client, identity, created["id"], created["version"])
        assert client.get("/health/ready").status_code == 200

    backup = create_backup(
        migrated,
        session_factory,
        actor_user_id=ca.id,
        request_id=None,
        source="API",
    )
    assert backup.status == "SUCCESS"

    with TestClient(create_app(migrated)) as client:
        identity = login(client, ca.login_name, DRILL_PASSWORD)
        outbound = create_document(
            client,
            identity,
            outbound_payload(master_data, lines=[line(master_data, quantity="0.500")]),
        )
        submit_document(client, identity, outbound["id"], outbound["version"])

    before_inventory = _inventory(session_factory)
    before_documents = _document_count(session_factory)
    assert before_documents == 2
    assert before_inventory == [750]
    _checkpoint(migrated_engine, migrated)

    result = restore_backup_set(migrated, backup.id)
    assert result.integrity == "ok"

    restarted = create_app(migrated)
    with TestClient(restarted) as client:
        assert client.get("/health/ready").status_code == 200
        identity = login(client, ca.login_name, DRILL_PASSWORD)
        assert identity["login_name"] == "drill-admin"
        assert client.get("/api/v1/inventory").status_code == 200

    assert _inventory(session_factory) == [1250]
    assert _document_count(session_factory) == 1

    mcp_app = create_app(migrated)
    async with mcp_sessions(mcp_app, {"ca": raw_key}) as sessions:
        tools = await sessions["ca"].list_tools()
        names = tool_names(tools.tools)
        assert len(names) == 13
        assert "inventory_list" in names
        listed = await sessions["ca"].call_tool("document_list", {"page_size": 10})
        assert listed.is_error is False
        assert listed.structured_content is not None
        assert listed.structured_content["total"] == 1


def _create_admin(session_factory: sessionmaker[Session], login_name: str) -> User:
    from app.auth.passwords import hash_password
    from app.db.sqlite import begin_immediate
    from app.db.types import utc_now

    now = utc_now()
    with session_factory() as session, begin_immediate(session):
        user = User(
            login_name=login_name,
            password_hash=hash_password(DRILL_PASSWORD),
            display_name="演练管理员",
            role=Role.COMPANY_ADMIN.value,
            status="ACTIVE",
            force_password_change=False,
            password_version=1,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
        session.expunge(user)
        return user
