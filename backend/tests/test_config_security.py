import pytest
from pydantic import ValidationError

from app.core.config import Settings


TEST_URL = (
    "postgresql+psycopg://"
    "auneron:test_password"
    "@localhost:5432/auneron_test"
)

MAIN_URL = (
    "postgresql+psycopg://"
    "auneron:test_password"
    "@localhost:5432/auneron"
)


def test_settings_accepts_isolated_test_database() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL=TEST_URL,
    )

    assert settings.environment == "test"
    assert settings.database_name == "auneron_test"


def test_settings_rejects_main_database_in_test() -> None:
    with pytest.raises(
        ValidationError,
        match="auneron_test",
    ):
        Settings(
            _env_file=None,
            APP_ENV="test",
            DATABASE_URL=MAIN_URL,
        )


def test_settings_rejects_test_database_outside_test() -> None:
    with pytest.raises(
        ValidationError,
        match="APP_ENV=test",
    ):
        Settings(
            _env_file=None,
            APP_ENV="development",
            DATABASE_URL=TEST_URL,
        )


def test_settings_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "DATABASE_URL",
        raising=False,
    )

    with pytest.raises(
        ValidationError,
        match="DATABASE_URL",
    ):
        Settings(
            _env_file=None,
            APP_ENV="development",
        )
