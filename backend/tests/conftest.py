import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    (
        "postgresql+psycopg://"
        "auneron:auneron_dev_password"
        "@localhost:5432/auneron_test"
    ),
)

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from app.database.database import SessionLocal, engine
from app.main import app


def _validate_test_database() -> None:
    database_name = engine.url.database

    if database_name != "auneron_test":
        raise RuntimeError(
            "Proteção acionada: os testes somente podem usar "
            "o banco auneron_test. "
            f"Banco recebido: {database_name!r}."
        )

    if os.environ.get("APP_ENV") != "test":
        raise RuntimeError(
            "Proteção acionada: APP_ENV precisa ser test."
        )


_validate_test_database()


def _clean_database() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    knowledge,
                    accounts
                RESTART IDENTITY
                CASCADE
                """
            )
        )


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None, None, None]:
    _clean_database()

    yield

    _clean_database()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    session = SessionLocal()

    try:
        yield session
    finally:
        session.close()