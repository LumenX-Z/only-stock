"""并发禁用/降级最后公司管理员的保护测试。"""

from collections.abc import Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.errors import AppError
from app.modules.users.models import Role, User, UserStatus
from app.modules.users.service import disable_user, update_user

Result = tuple[str, str]


def _attempt(action: Callable[[], object]) -> Result:
    try:
        action()
    except AppError as error:
        return ("error", error.code)
    return ("ok", "")


def _active_admins(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.role == Role.COMPANY_ADMIN.value,
                User.status == UserStatus.ACTIVE.value,
            )
        )
    return int(count or 0)


def test_concurrent_mutual_demotion_keeps_one_active_admin(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    first = user_factory("admin-one", role=Role.COMPANY_ADMIN, force_password_change=False)
    second = user_factory("admin-two", role=Role.COMPANY_ADMIN, force_password_change=False)

    def demote(actor_id: str, target_id: str) -> Callable[[], Result]:
        return lambda: _attempt(
            lambda: update_user(
                session_factory,
                actor_id,
                target_id,
                role=Role.OPERATOR.value,
                version=1,
            )
        )

    results = run_concurrently([demote(first.id, second.id), demote(second.id, first.id)])

    assert sorted(results) == [("error", "LAST_ADMIN_PROTECTED"), ("ok", "")]
    assert _active_admins(session_factory) == 1


def test_concurrent_mutual_disable_keeps_one_active_admin(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    first = user_factory("disable-one", role=Role.COMPANY_ADMIN, force_password_change=False)
    second = user_factory("disable-two", role=Role.COMPANY_ADMIN, force_password_change=False)

    def disable(actor_id: str, target_id: str) -> Callable[[], Result]:
        return lambda: _attempt(
            lambda: disable_user(
                session_factory,
                actor_id,
                target_id,
                version=1,
            )
        )

    results = run_concurrently([disable(first.id, second.id), disable(second.id, first.id)])

    assert sorted(results) == [("error", "LAST_ADMIN_PROTECTED"), ("ok", "")]
    assert _active_admins(session_factory) == 1


def test_last_admin_cannot_be_demoted_after_other_admin_disabled(
    session_factory: sessionmaker[Session],
    user_factory: Callable[..., User],
) -> None:
    first = user_factory("sole-one", role=Role.COMPANY_ADMIN, force_password_change=False)
    second = user_factory("sole-two", role=Role.COMPANY_ADMIN, force_password_change=False)

    disable_user(session_factory, first.id, second.id, version=1)

    result = _attempt(
        lambda: update_user(
            session_factory,
            first.id,
            first.id,
            role=Role.OPERATOR.value,
            version=1,
        )
    )

    assert result == ("error", "LAST_ADMIN_PROTECTED")
    assert _active_admins(session_factory) == 1
