import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.core.config import Settings
from app.core.config import settings
from app.database import database


def _current_setting_ms(
    setting_name: str,
) -> int:
    with database.engine.connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT setting::bigint
                FROM pg_settings
                WHERE name = :setting_name
                """
            ),
            {
                "setting_name": setting_name,
            },
        ).scalar_one()

    return int(value)


def test_postgresql_application_name_is_configured() -> None:
    with database.engine.connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT current_setting(
                    'application_name'
                )
                """
            )
        ).scalar_one()

    assert value == (
        settings.database_application_name
    )


def test_statement_timeout_is_configured() -> None:
    assert _current_setting_ms(
        "statement_timeout"
    ) == settings.database_statement_timeout_ms


def test_lock_timeout_is_configured() -> None:
    assert _current_setting_ms(
        "lock_timeout"
    ) == settings.database_lock_timeout_ms


def test_idle_transaction_timeout_is_configured() -> None:
    assert _current_setting_ms(
        "idle_in_transaction_session_timeout"
    ) == (
        settings.database_idle_transaction_timeout_ms
    )


def test_engine_builder_uses_operational_guards(
    monkeypatch,
) -> None:
    captured: dict = {}
    sentinel = object()

    def fake_create_engine(
        url,
        **kwargs,
    ):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(
        database,
        "create_engine",
        fake_create_engine,
    )

    result = database._build_engine()

    assert result is sentinel
    assert captured["url"] == (
        settings.database_url
    )

    kwargs = captured["kwargs"]

    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_use_lifo"] is True
    assert kwargs["pool_reset_on_return"] == (
        "rollback"
    )
    assert kwargs["connect_args"][
        "connect_timeout"
    ] == settings.database_connect_timeout


def test_get_db_rolls_back_and_closes_on_error(
    monkeypatch,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.rolled_back = False
            self.closed = False

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    fake_session = FakeSession()

    monkeypatch.setattr(
        database,
        "SessionLocal",
        lambda: fake_session,
    )

    dependency = database.get_db()

    assert next(dependency) is fake_session

    with pytest.raises(
        RuntimeError,
        match="forced failure",
    ):
        dependency.throw(
            RuntimeError(
                "forced failure"
            )
        )

    assert fake_session.rolled_back is True
    assert fake_session.closed is True


def test_operational_settings_reject_unsafe_values() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="test",
            DATABASE_URL=(
                "postgresql+psycopg://"
                "auneron:test@localhost:5432/"
                "auneron_test"
            ),
            database_connect_timeout=0,
        )

