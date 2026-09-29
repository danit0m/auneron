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


VALID_PRODUCTION_DATABASE_URL = (
    "postgresql+psycopg://"
    "auneron:strong-password@postgres:5432/auneron"
)

VALID_PRODUCTION_API_KEY = (
    "Q7d9R2m8L4x1P6v3N5k0T8z2B9c4W7y1"
)

# Valor canônico congelado em SEC-AUTH-1.1C (PACKAGED_PROXY_IPS).
VALID_PRODUCTION_FORWARDED_ALLOW_IPS = (
    "172.28.1.10/32,172.28.0.10/32"
)


def production_settings(
    monkeypatch: pytest.MonkeyPatch,
    **overrides,
) -> Settings:
    values = {
        "APP_ENV": "production",
        "DATABASE_URL": (
            VALID_PRODUCTION_DATABASE_URL
        ),
        "API_KEY": (
            VALID_PRODUCTION_API_KEY
        ),
        "CORS_ORIGINS": "",
        "DEBUG": False,
        "DATABASE_ECHO": False,
        "EXPECTED_DATABASE_NAME": "auneron",
        "EXPECTED_DATABASE_HOST": "postgres",
        "FORWARDED_ALLOW_IPS": (
            VALID_PRODUCTION_FORWARDED_ALLOW_IPS
        ),
    }
    values.update(overrides)

    # O validador de produção compara Settings.forwarded_allow_ips com
    # os.environ["FORWARDED_ALLOW_IPS"] (SEC-AUTH-1.1C, bloqueador 3) --
    # o ambiente do processo precisa refletir o mesmo valor que está
    # sendo passado ao construtor, inclusive quando um teste sobrescreve
    # FORWARDED_ALLOW_IPS via overrides. monkeypatch restaura sozinho ao
    # final de cada teste, sem vazar para os demais.
    monkeypatch.setenv(
        "FORWARDED_ALLOW_IPS",
        values["FORWARDED_ALLOW_IPS"],
    )

    return Settings(
        _env_file=None,
        **values,
    )


def test_g3_accepts_matching_database_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = production_settings(monkeypatch)

    assert settings.database_name == "auneron"
    assert settings.database_host == "postgres"


def test_g3_rejects_database_name_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        production_settings(
            monkeypatch,
            EXPECTED_DATABASE_NAME="outro_banco",
        )
    except ValidationError as error:
        assert "identidade" in str(
            error
        ).lower()
    else:
        raise AssertionError(
            "Nome de banco divergente foi "
            "aceito em production."
        )


def test_g3_rejects_database_host_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        production_settings(
            monkeypatch,
            EXPECTED_DATABASE_HOST="outro-host",
        )
    except ValidationError as error:
        assert "identidade" in str(
            error
        ).lower()
    else:
        raise AssertionError(
            "Host de banco divergente foi "
            "aceito em production."
        )


def test_g3_requires_expected_database_name_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        production_settings(
            monkeypatch,
            EXPECTED_DATABASE_NAME=None,
        )
    except ValidationError as error:
        assert "EXPECTED_DATABASE_NAME" in str(
            error
        )
    else:
        raise AssertionError(
            "EXPECTED_DATABASE_NAME ausente foi "
            "aceita em production."
        )


def test_g3_requires_expected_database_host_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        production_settings(
            monkeypatch,
            EXPECTED_DATABASE_HOST=None,
        )
    except ValidationError as error:
        assert "EXPECTED_DATABASE_HOST" in str(
            error
        )
    else:
        raise AssertionError(
            "EXPECTED_DATABASE_HOST ausente foi "
            "aceita em production."
        )


def test_g3_rejects_database_url_without_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        production_settings(
            monkeypatch,
            DATABASE_URL=(
                "postgresql+psycopg:///auneron"
            ),
        )
    except ValidationError as error:
        assert "identidade" in str(
            error
        ).lower()
    else:
        raise AssertionError(
            "DATABASE_URL sem host foi aceita "
            "em production."
        )


def test_g3_not_enforced_outside_production() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="development",
        DATABASE_URL=(
            "postgresql+psycopg://"
            "auneron:password@localhost:5432/auneron"
        ),
    )

    assert settings.expected_database_name is None
    assert settings.expected_database_host is None


def test_g3_not_enforced_in_test_environment() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL=TEST_URL,
    )

    assert settings.expected_database_name is None
    assert settings.expected_database_host is None


def test_g3_error_messages_do_not_leak_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for overrides in (
        {"EXPECTED_DATABASE_NAME": "outro_banco"},
        {"EXPECTED_DATABASE_HOST": "outro-host"},
    ):
        try:
            production_settings(monkeypatch, **overrides)
        except ValidationError as error:
            message = str(error)

            assert (
                "strong-password"
                not in message
            )
            assert (
                VALID_PRODUCTION_DATABASE_URL
                not in message
            )
        else:
            raise AssertionError(
                "Identidade divergente foi "
                "aceita em production."
            )
