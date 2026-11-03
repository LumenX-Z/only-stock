"""六类实体并发生成编码的事务与唯一性测试。"""

from collections.abc import Callable, Sequence
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.errors import AppError
from app.modules.masterdata.base import CodeCounter
from app.modules.masterdata.service import create_entity
from app.modules.users.models import Role, User


def _creator(
    session_factory: sessionmaker[Session],
    actor_id: str,
    key: str,
    **fixed: Any,
) -> Callable[[str], object]:
    def create(name: str) -> object:
        return create_entity(
            session_factory,
            key,
            name=name,
            actor_user_id=actor_id,
            request_id=None,
            **fixed,
        )

    return create


def test_concurrent_creation_generates_unique_sequential_codes(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    actor = user_factory("code-admin", role=Role.COMPANY_ADMIN)
    category = create_entity(
        session_factory,
        "category",
        name="并发分类",
        actor_user_id=actor.id,
        request_id=None,
    )
    material_category_id = category.id
    child = create_entity(
        session_factory,
        "category",
        name="并发子类",
        parent_id=material_category_id,
        actor_user_id=actor.id,
        request_id=None,
    )

    cases = [
        ("warehouse", "CK", 1, {}),
        ("department", "BM", 1, {}),
        ("project", "XM", 1, {}),
        ("manufacturer", "CJ", 1, {}),
        ("category", "FL", 3, {}),
        ("material", "WZ", 1, {"category_id": child.id, "unit": "个", "quantity_type": "DECIMAL"}),
    ]
    for key, prefix, start, fixed in cases:
        creator = _creator(session_factory, actor.id, key, **fixed)
        rows = run_concurrently(
            [
                lambda index=index, creator=creator, key=key: creator(f"{key}-{index}")
                for index in range(5)
            ]
        )
        codes = [row.code for row in rows]
        assert len(set(codes)) == 5
        assert sorted(codes) == [f"{prefix}-{value:05d}" for value in range(start, start + 5)]

    with session_factory() as session:
        counters = {
            row.entity_type: row.next_value for row in session.scalars(select(CodeCounter)).all()
        }
    assert counters == {
        "warehouse": 6,
        "department": 6,
        "project": 6,
        "manufacturer": 6,
        "category": 8,
        "material": 6,
    }


def test_failed_insert_rolls_back_counter(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
) -> None:
    actor = user_factory("rollback-admin", role=Role.COMPANY_ADMIN)
    with session_factory() as session:
        session.execute(
            text(
                "CREATE TRIGGER fail_warehouse_insert BEFORE INSERT ON warehouses "
                "BEGIN SELECT RAISE(ABORT, 'forced warehouse failure'); END"
            )
        )
        session.commit()

    with pytest.raises(Exception, match="forced warehouse failure"):
        create_entity(
            session_factory,
            "warehouse",
            name="失败仓",
            actor_user_id=actor.id,
            request_id=None,
        )

    with session_factory() as session:
        session.execute(text("DROP TRIGGER fail_warehouse_insert"))
        session.commit()

    created = create_entity(
        session_factory,
        "warehouse",
        name="成功仓",
        actor_user_id=actor.id,
        request_id=None,
    )
    assert created.code == "CK-00001"
    with session_factory() as session:
        counter = session.get(CodeCounter, "warehouse")
    assert counter is not None and counter.next_value == 2


def test_sequence_exhaustion_does_not_wrap(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
) -> None:
    actor = user_factory("exhaust-admin", role=Role.COMPANY_ADMIN)
    with session_factory() as session:
        session.add(CodeCounter(entity_type="warehouse", next_value=99_999))
        session.commit()

    last = create_entity(
        session_factory, "warehouse", name="最后一仓", actor_user_id=actor.id, request_id=None
    )
    assert last.code == "CK-99999"

    with pytest.raises(AppError) as error:
        create_entity(
            session_factory,
            "warehouse",
            name="超限仓",
            actor_user_id=actor.id,
            request_id=None,
        )
    assert error.value.code == "CODE_SEQUENCE_EXHAUSTED"
    with session_factory() as session:
        assert session.scalar(select(CodeCounter.next_value)) == 100_000
