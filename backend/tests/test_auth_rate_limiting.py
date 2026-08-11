from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.config import settings
from app.models.user import User


TEST_EMAIL = "rate.limit@example.com"
TEST_PASSWORD = "Senha-Forte-Rate-Limit-123!"


def create_user(
    db_session: Session,
) -> User:
    user = User(
        name="Usuário Rate Limit",
        email=TEST_EMAIL,
        password_hash=hash_password(
            TEST_PASSWORD
        ),
        role="developer",
        active=True,
    )

    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    return user


def test_login_rate_limit_returns_429_and_retry_after(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_user(
        db_session
    )

    for attempt in range(
        settings.auth_login_account_max_failures
    ):
        response = service_client.post(
            "/auth/login",
            json={
                "email": TEST_EMAIL,
                "password": "senha-incorreta",
            },
        )

        if (
            attempt
            < settings.auth_login_account_max_failures
            - 1
        ):
            assert response.status_code == 401
        else:
            assert response.status_code == 429

    retry_after = int(
        response.headers["Retry-After"]
    )

    assert retry_after > 0
    assert (
        response.headers["Cache-Control"]
        == "no-store"
    )

    blocked_correct_password = (
        service_client.post(
            "/auth/login",
            json={
                "email": TEST_EMAIL,
                "password": TEST_PASSWORD,
            },
        )
    )

    assert (
        blocked_correct_password.status_code
        == 429
    )


def test_successful_login_clears_account_failures(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_user(
        db_session
    )

    for _ in range(
        settings.auth_login_account_max_failures
        - 1
    ):
        response = service_client.post(
            "/auth/login",
            json={
                "email": TEST_EMAIL,
                "password": "senha-incorreta",
            },
        )
        assert response.status_code == 401

    successful = service_client.post(
        "/auth/login",
        json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        },
    )

    assert successful.status_code == 200
    assert (
        successful.headers["Cache-Control"]
        == "no-store"
    )

    service_client.cookies.clear()

    for _ in range(
        settings.auth_login_account_max_failures
        - 1
    ):
        response = service_client.post(
            "/auth/login",
            json={
                "email": TEST_EMAIL,
                "password": "senha-incorreta",
            },
        )
        assert response.status_code == 401


def test_login_ip_limit_protects_password_spraying(
    service_client: TestClient,
) -> None:
    for attempt in range(
        settings.auth_login_ip_max_failures
    ):
        response = service_client.post(
            "/auth/login",
            json={
                "email": (
                    f"unknown-{attempt}@example.com"
                ),
                "password": "senha-incorreta",
            },
        )

        if (
            attempt
            < settings.auth_login_ip_max_failures
            - 1
        ):
            assert response.status_code == 401
        else:
            assert response.status_code == 429

    assert int(
        response.headers["Retry-After"]
    ) > 0


def test_elevation_rate_limit_returns_429(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_user(
        db_session
    )

    login_response = service_client.post(
        "/auth/login",
        json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        },
    )

    assert login_response.status_code == 200

    for attempt in range(
        settings.auth_elevation_user_max_failures
    ):
        response = service_client.post(
            "/auth/elevate",
            json={
                "password": "senha-incorreta",
            },
        )

        if (
            attempt
            < settings.auth_elevation_user_max_failures
            - 1
        ):
            assert response.status_code == 401
        else:
            assert response.status_code == 429

    assert int(
        response.headers["Retry-After"]
    ) > 0

    blocked_correct_password = (
        service_client.post(
            "/auth/elevate",
            json={
                "password": TEST_PASSWORD,
            },
        )
    )

    assert (
        blocked_correct_password.status_code
        == 429
    )


def test_auth_rate_limit_does_not_store_raw_credentials(
    service_client: TestClient,
    db_session: Session,
    caplog,
) -> None:
    create_user(
        db_session
    )

    with caplog.at_level(
        "WARNING",
        logger="auneron.security",
    ):
        response = service_client.post(
            "/auth/login",
            json={
                "email": TEST_EMAIL,
                "password": "segredo-nao-logar",
            },
        )

    assert response.status_code == 401

    combined = "\n".join(
        record.getMessage()
        + " "
        + str(record.__dict__)
        for record in caplog.records
    )

    assert "segredo-nao-logar" not in combined
    assert TEST_EMAIL not in combined
