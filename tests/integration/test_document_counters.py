"""单号生成、业务日期与计数器回绕测试。

依据 docs/05-数据模型与实体约束.md 6.5：入库单号 ``RKYYYYMMDD-000001``、出库
单号 ``CKYYYYMMDD-000001``，数据库内唯一、不要求断号连续；业务日期按配置时区
计算，单号生成与计数器更新在同一写事务内完成。
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.errors import AppError
from app.modules.documents.inputs import DocumentInput, DocumentLineInput
from app.modules.documents.models import DocumentCounter
from app.modules.documents.service import create_draft
from tests.conftest import MasterDataFixture


def _create(
    session_factory: sessionmaker[Session],
    master: MasterDataFixture,
    settings: Settings,
    *,
    doc_type: str = "INBOUND",
    now: datetime,
    quantity: str = "1",
) -> str:
    line = DocumentLineInput(
        material_id=master.material.id,
        quantity=quantity,
        manufacturer_id=(master.manufacturer.id if doc_type == "INBOUND" else None),
    )
    record = create_draft(
        session_factory,
        DocumentInput(
            doc_type=doc_type,
            warehouse_id=master.warehouse.id,
            department_id=master.department.id,
            lines=(line,),
        ),
        actor=master.actor,
        request_id=str(uuid4()),
        timezone=settings.timezone,
        now=now,
    )
    return record.document.doc_no


def test_doc_numbers_follow_business_date_and_sequence(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
) -> None:
    # UTC 16:00 是 Asia/Shanghai 次日 00:00。
    first = _create(
        session_factory,
        master_data,
        settings,
        now=datetime(2026, 10, 23, 16, 0, tzinfo=UTC),
    )
    second = _create(
        session_factory,
        master_data,
        settings,
        now=datetime(2026, 10, 24, 15, 59, tzinfo=UTC),
    )
    third = _create(
        session_factory,
        master_data,
        settings,
        now=datetime(2026, 10, 24, 16, 0, tzinfo=UTC),
    )
    outbound = _create(
        session_factory,
        master_data,
        settings,
        doc_type="OUTBOUND",
        now=datetime(2026, 10, 23, 16, 0, tzinfo=UTC),
    )

    assert first == "RK20261024-000001"
    assert second == "RK20261024-000002"
    assert third == "RK20261025-000001"
    assert outbound == "CK20261024-000001"

    with session_factory() as session:
        counters = {
            (row.biz_date, row.doc_kind): row.next_value
            for row in session.scalars(select(DocumentCounter)).all()
        }
    assert counters[("2026-10-24", "INBOUND")] == 3
    assert counters[("2026-10-25", "INBOUND")] == 2
    assert counters[("2026-10-24", "OUTBOUND")] == 2


def test_exhausted_document_number_rolls_back(
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    settings: Settings,
) -> None:
    with session_factory() as session:
        session.add(
            DocumentCounter(biz_date="2030-01-01", doc_kind="INBOUND", next_value=1_000_000)
        )
        session.commit()

    with pytest.raises(AppError) as error:
        _create(
            session_factory,
            master_data,
            settings,
            now=datetime(2029, 12, 31, 16, 0, tzinfo=UTC),
        )
    assert error.value.status_code == 409
    assert error.value.code == "DOCUMENT_NUMBER_EXHAUSTED"

    with session_factory() as session:
        counter = session.get(DocumentCounter, ("2030-01-01", "INBOUND"))
        assert counter is not None
        assert counter.next_value == 1_000_000
