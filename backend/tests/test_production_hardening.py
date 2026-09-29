import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.http_security import (
    HSTS_VALUE,
    SecurityHeadersMiddleware,
)


VALID_PRODUCTION_DATABASE_URL = (
    "postgresql+psycopg://"
    "auneron:strong-password@db:5432/auneron"
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
        "EXPECTED_DATABASE_HOST": "db",
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


def test_valid_production_configuration_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = production_settings(monkeypatch)

    assert settings.environment == "production"
    assert settings.is_postgresql is True
    assert settings.auth_cookie_secure is True
    assert settings.cors_origin_list == []


def test_production_rejects_placeholder_api_key(
    monkeypatch: pytest.MonkeyPatch,
):
    try:
        production_settings(
            monkeypatch,
            API_KEY=(
                "CHANGE_ME_WITH_A_RANDOM_VALUE_"
                "AT_LEAST_32_CHARACTERS"
            ),
        )
    except ValidationError as error:
        assert "placeholder" in str(
            error
        ).lower()
    else:
        raise AssertionError(
            "Placeholder API key foi aceita."
        )


def test_production_rejects_low_diversity_api_key(
    monkeypatch: pytest.MonkeyPatch,
):
    try:
        production_settings(
            monkeypatch,
            API_KEY="A" * 64,
        )
    except ValidationError as error:
        assert "diversidade" in str(
            error
        ).lower()
    else:
        raise AssertionError(
            "API key previsível foi aceita."
        )


def test_production_requires_postgresql(
    monkeypatch: pytest.MonkeyPatch,
):
    try:
        production_settings(
            monkeypatch,
            DATABASE_URL=(
                "sqlite:///auneron.db"
            ),
        )
    except ValidationError as error:
        assert "postgresql" in str(
            error
        ).lower()
    else:
        raise AssertionError(
            "SQLite foi aceito em production."
        )


def test_production_rejects_debug_and_database_echo(
    monkeypatch: pytest.MonkeyPatch,
):
    for key in (
        "DEBUG",
        "DATABASE_ECHO",
    ):
        try:
            production_settings(
                monkeypatch,
                **{key: True},
            )
        except ValidationError:
            continue

        raise AssertionError(
            f"{key}=true foi aceito em production."
        )


def test_production_rejects_insecure_cors_origin(
    monkeypatch: pytest.MonkeyPatch,
):
    try:
        production_settings(
            monkeypatch,
            CORS_ORIGINS=(
                "http://app.example.com"
            ),
        )
    except ValidationError as error:
        assert "https" in str(
            error
        ).lower()
    else:
        raise AssertionError(
            "CORS HTTP foi aceito em production."
        )


def test_cors_rejects_wildcard_with_credentials():
    try:
        Settings(
            _env_file=None,
            APP_ENV="development",
            DATABASE_URL=(
                "postgresql+psycopg://"
                "auneron:password@localhost:5432/auneron"
            ),
            API_KEY=(
                VALID_PRODUCTION_API_KEY
            ),
            CORS_ORIGINS="*",
        )
    except ValidationError as error:
        assert "wildcard" in str(
            error
        ).lower()
    else:
        raise AssertionError(
            "Wildcard CORS foi aceito."
        )


def test_application_responses_receive_security_headers(
    unauthenticated_client: TestClient,
):
    response = unauthenticated_client.get(
        "/health"
    )

    assert response.status_code == 200
    assert (
        response.headers[
            "x-content-type-options"
        ]
        == "nosniff"
    )
    assert (
        response.headers[
            "x-frame-options"
        ]
        == "DENY"
    )
    assert (
        response.headers[
            "referrer-policy"
        ]
        == "strict-origin-when-cross-origin"
    )
    assert "camera=()" in response.headers[
        "permissions-policy"
    ]
    assert (
        "strict-transport-security"
        not in response.headers
    )


def test_production_security_headers_include_hsts():
    test_app = FastAPI()

    test_app.add_middleware(
        SecurityHeadersMiddleware,
        production=True,
    )

    @test_app.get("/probe")
    def probe():
        return {"status": "ok"}

    with TestClient(test_app) as client:
        response = client.get(
            "/probe"
        )

    assert response.status_code == 200
    assert (
        response.headers[
            "strict-transport-security"
        ]
        == HSTS_VALUE
    )
