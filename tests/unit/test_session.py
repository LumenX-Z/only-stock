from types import SimpleNamespace

from sqlalchemy import Engine, text

from app.db.database import create_session_factory, get_session


def test_request_session_is_closed_after_use(db_engine: Engine) -> None:
    runtime = SimpleNamespace(session_factory=create_session_factory(db_engine))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(runtime=runtime)),
    )

    generator = get_session(request)  # type: ignore[arg-type]
    session = next(generator)
    assert session.execute(text("SELECT 1")).scalar_one() == 1
    assert session.in_transaction()

    generator.close()
    assert not session.in_transaction()
