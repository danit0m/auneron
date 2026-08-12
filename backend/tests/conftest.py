import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL"
)
TEST_API_KEY = os.getenv(
    "TEST_API_KEY"
)

TEST_CLIENT_EMAIL = (
    "developer.test@example.com"
)
TEST_CLIENT_PASSWORD = (
    "Senha-Teste-Auneron-123!"
)

if not TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL não está definida. "
        "Crie backend/.env.test a partir de "
        "backend/.env.test.example ou defina a "
        "variável antes de executar os testes."
    )

if not TEST_API_KEY:
    raise RuntimeError(
        "TEST_API_KEY não está definida. "
        "Crie backend/.env.test a partir de "
        "backend/.env.test.example ou defina a "
        "variável antes de executar os testes."
    )

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["API_KEY"] = TEST_API_KEY

from app.core.authentication import hash_password
from app.core.rate_limiting import auth_rate_limiter
from app.core.security import API_KEY_HEADER_NAME
from app.database.database import SessionLocal
from app.database.database import engine
from app.main import app
from app.models.user import User


def _validate_test_database() -> None:
    database_name = engine.url.database

    if database_name != "auneron_test":
        raise RuntimeError(
            "Proteção acionada: os testes somente "
            "podem usar o banco auneron_test. "
            f"Banco recebido: {database_name!r}."
        )

    if os.environ.get("APP_ENV") != "test":
        raise RuntimeError(
            "Proteção acionada: APP_ENV precisa "
            "ser test."
        )


_validate_test_database()


def _clean_database() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    memory_evidence,
                    memory_items,
                    auth_sessions,
                    users,
                    knowledge,
                    accounts
                RESTART IDENTITY
                CASCADE
                """
            )
        )


def _prepare_privileged_client(
    test_client: TestClient,
) -> None:
    db = SessionLocal()

    try:
        user = User(
            name="Desenvolvedor de Teste",
            email=TEST_CLIENT_EMAIL,
            password_hash=hash_password(
                TEST_CLIENT_PASSWORD
            ),
            role="developer",
            active=True,
        )

        db.add(user)
        db.commit()
    finally:
        db.close()

    login_response = test_client.post(
        "/auth/login",
        json={
            "email": TEST_CLIENT_EMAIL,
            "password": (
                TEST_CLIENT_PASSWORD
            ),
        },
    )

    if login_response.status_code != 200:
        raise RuntimeError(
            "Não foi possível autenticar o "
            "cliente privilegiado de testes: "
            f"{login_response.status_code} "
            f"{login_response.text}"
        )

    elevation_response = test_client.post(
        "/auth/elevate",
        json={
            "password": (
                TEST_CLIENT_PASSWORD
            ),
        },
    )

    if elevation_response.status_code != 200:
        raise RuntimeError(
            "Não foi possível elevar o cliente "
            "privilegiado de testes: "
            f"{elevation_response.status_code} "
            f"{elevation_response.text}"
        )


@pytest.fixture(autouse=True)
def clean_database() -> Generator[
    None,
    None,
    None,
]:
    auth_rate_limiter.reset()
    _clean_database()

    yield

    auth_rate_limiter.reset()
    _clean_database()


@pytest.fixture
def client() -> Generator[
    TestClient,
    None,
    None,
]:
    with TestClient(app) as test_client:
        test_client.headers.update({
            API_KEY_HEADER_NAME: TEST_API_KEY,
        })

        _prepare_privileged_client(
            test_client
        )

        yield test_client


@pytest.fixture
def service_client() -> Generator[
    TestClient,
    None,
    None,
]:
    with TestClient(app) as test_client:
        test_client.headers.update({
            API_KEY_HEADER_NAME: TEST_API_KEY,
        })
        yield test_client


@pytest.fixture
def unauthenticated_client() -> Generator[
    TestClient,
    None,
    None,
]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_session() -> Generator[
    Session,
    None,
    None,
]:
    session = SessionLocal()

    try:
        yield session
    finally:
        session.close()
