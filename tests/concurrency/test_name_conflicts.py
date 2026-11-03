"""并发名称查重、分类停用与新增下级资料的竞争测试。"""

from collections.abc import Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.errors import AppError
from app.modules.categories.models import Category
from app.modules.masterdata.service import (
    create_entity,
    set_entity_active,
)
from app.modules.materials.models import Material
from app.modules.users.models import Role, User

Result = tuple[str, str]


def _attempt(action: Callable[[], object]) -> Result:
    try:
        action()
    except AppError as error:
        return ("error", error.code)
    return ("ok", "")


def test_concurrent_duplicate_warehouse_name_only_one_succeeds(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    actor = user_factory("name-admin", role=Role.COMPANY_ADMIN)

    def create(name: str) -> Callable[[], Result]:
        return lambda: _attempt(
            lambda: create_entity(
                session_factory,
                "warehouse",
                name=name,
                actor_user_id=actor.id,
                request_id=None,
            )
        )

    results = run_concurrently([create("并发仓"), create(" 并发仓 ")])

    assert sorted(results) == [("error", "NAME_CONFLICT"), ("ok", "")]


def test_concurrent_duplicate_category_name_in_same_parent(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    actor = user_factory("category-admin", role=Role.COMPANY_ADMIN)
    parent = create_entity(
        session_factory, "category", name="父类", actor_user_id=actor.id, request_id=None
    )

    def create() -> Callable[[], Result]:
        return lambda: _attempt(
            lambda: create_entity(
                session_factory,
                "category",
                name="子类",
                parent_id=parent.id,
                actor_user_id=actor.id,
                request_id=None,
            )
        )

    results = run_concurrently([create(), create()])

    assert sorted(results) == [("error", "NAME_CONFLICT"), ("ok", "")]


def test_same_name_in_different_scope_is_allowed_concurrently(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    actor = user_factory("scope-admin", role=Role.COMPANY_ADMIN)
    parent_a = create_entity(
        session_factory, "category", name="父类 A", actor_user_id=actor.id, request_id=None
    )
    parent_b = create_entity(
        session_factory, "category", name="父类 B", actor_user_id=actor.id, request_id=None
    )

    def create(parent_id: str) -> Callable[[], Result]:
        return lambda: _attempt(
            lambda: create_entity(
                session_factory,
                "category",
                name="同名子类",
                parent_id=parent_id,
                actor_user_id=actor.id,
                request_id=None,
            )
        )

    results = run_concurrently([create(parent_a.id), create(parent_b.id)])

    assert sorted(results) == [("ok", ""), ("ok", "")]


def test_concurrent_duplicate_material_name_in_same_category(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    actor = user_factory("material-admin", role=Role.COMPANY_ADMIN)
    category = create_entity(
        session_factory, "category", name="物资分类", actor_user_id=actor.id, request_id=None
    )

    def create() -> Callable[[], Result]:
        return lambda: _attempt(
            lambda: create_entity(
                session_factory,
                "material",
                name="同名物资",
                category_id=category.id,
                unit="个",
                quantity_type="DECIMAL",
                actor_user_id=actor.id,
                request_id=None,
            )
        )

    results = run_concurrently([create(), create()])

    assert sorted(results) == [("error", "NAME_CONFLICT"), ("ok", "")]


def test_disable_parent_and_create_material_race_keeps_invariant(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    actor = user_factory("race-admin", role=Role.COMPANY_ADMIN)
    category = create_entity(
        session_factory, "category", name="竞争分类", actor_user_id=actor.id, request_id=None
    )

    def disable() -> Callable[[], Result]:
        return lambda: _attempt(
            lambda: set_entity_active(
                session_factory,
                "category",
                category.id,
                active=False,
                actor_user_id=actor.id,
                request_id=None,
            )
        )

    def add_material() -> Callable[[], Result]:
        return lambda: _attempt(
            lambda: create_entity(
                session_factory,
                "material",
                name="竞争物资",
                category_id=category.id,
                unit="个",
                quantity_type="DECIMAL",
                actor_user_id=actor.id,
                request_id=None,
            )
        )

    results = run_concurrently([disable(), add_material()])

    with session_factory() as session:
        stored_category = session.get(Category, category.id)
        material_count = session.scalar(
            select(func.count())
            .select_from(Material)
            .where(Material.category_id == category.id, Material.is_active.is_(True))
        )
    assert stored_category is not None
    if material_count:
        assert stored_category.is_active is True
    assert len(results) == 2


def test_disable_parent_and_enable_child_race_keeps_invariant(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    actor = user_factory("enable-race-admin", role=Role.COMPANY_ADMIN)
    parent = create_entity(
        session_factory, "category", name="竞争父类", actor_user_id=actor.id, request_id=None
    )
    child = create_entity(
        session_factory,
        "category",
        name="竞争子类",
        parent_id=parent.id,
        actor_user_id=actor.id,
        request_id=None,
    )
    set_entity_active(
        session_factory,
        "category",
        child.id,
        active=False,
        actor_user_id=actor.id,
        request_id=None,
    )

    def disable_parent() -> Callable[[], Result]:
        return lambda: _attempt(
            lambda: set_entity_active(
                session_factory,
                "category",
                parent.id,
                active=False,
                actor_user_id=actor.id,
                request_id=None,
            )
        )

    def enable_child() -> Callable[[], Result]:
        return lambda: _attempt(
            lambda: set_entity_active(
                session_factory,
                "category",
                child.id,
                active=True,
                actor_user_id=actor.id,
                request_id=None,
            )
        )

    run_concurrently([disable_parent(), enable_child()])

    with session_factory() as session:
        stored_parent = session.get(Category, parent.id)
        stored_child = session.get(Category, child.id)
    assert stored_parent is not None and stored_child is not None
    assert not (stored_parent.is_active is False and stored_child.is_active is True)
