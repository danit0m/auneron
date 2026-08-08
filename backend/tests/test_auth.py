from hashlib import sha256

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.models.auth_session import AuthSession
from app.models.user import User


TEST_EMAIL = "admin.test@example.com"
TEST_PASSWORD = "Senha-Forte-Auneron-123!"


def create_test_user(
    db_session: Session,
    *,
    email: str = TEST_EMAIL,
    password: str = TEST_PASSWORD,
    role: str = "developer",
    active: bool = True,
) -> User:
    user = User(
        name="Administrador de Teste",
        email=email.lower(),
        password_hash=hash_password(
            password
        ),
        role=role,
        active=active,
    )

    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    return user


def login(
    service_client: TestClient,
    *,
    email: str = TEST_EMAIL,
    password: str = TEST_PASSWORD,
):
    return service_client.post(
        "/auth/login",
        json={
            "email": email,
            "password": password,
        },
    )


def test_login_creates_http_only_session_cookie(
    service_client: TestClient,
    db_session: Session,
) -> None:
    user = create_test_user(
        db_session
    )

    response = login(
        service_client
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["user"]["id"] == user.id
    assert payload["user"]["email"] == (
        TEST_EMAIL
    )
    assert payload["user"]["role"] == (
        "developer"
    )
    assert payload["authenticated_at"]
    assert payload["expires_at"]
    assert payload["elevated_until"] is None

    set_cookie = response.headers[
        "set-cookie"
    ].lower()

    assert "auneron_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie

    raw_token = service_client.cookies.get(
        "auneron_session"
    )

    assert raw_token

    stored_session = (
        db_session.query(AuthSession)
        .filter(
            AuthSession.user_id == user.id
        )
        .one()
    )

    assert stored_session.token_hash != (
        raw_token
    )
    assert stored_session.token_hash == (
        sha256(
            raw_token.encode("utf-8")
        ).hexdigest()
    )


def test_login_rejects_invalid_password(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_test_user(
        db_session
    )

    response = login(
        service_client,
        password="senha-incorreta",
    )

    assert response.status_code == 401
    assert response.json()["detail"] == (
        "E-mail ou senha inválidos."
    )


def test_login_rejects_inactive_user(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_test_user(
        db_session,
        active=False,
    )

    response = login(
        service_client
    )

    assert response.status_code == 401


def test_me_returns_current_session(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_test_user(
        db_session
    )

    assert login(
        service_client
    ).status_code == 200

    response = service_client.get(
        "/auth/me"
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == (
        TEST_EMAIL
    )


def test_logout_revokes_session(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_test_user(
        db_session
    )

    assert login(
        service_client
    ).status_code == 200

    response = service_client.post(
        "/auth/logout"
    )

    assert response.status_code == 204

    me_response = service_client.get(
        "/auth/me"
    )

    assert me_response.status_code == 401


def test_elevation_requires_current_password(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_test_user(
        db_session
    )

    assert login(
        service_client
    ).status_code == 200

    invalid = service_client.post(
        "/auth/elevate",
        json={
            "password": "senha-incorreta",
        },
    )

    assert invalid.status_code == 401

    valid = service_client.post(
        "/auth/elevate",
        json={
            "password": TEST_PASSWORD,
        },
    )

    assert valid.status_code == 200
    assert valid.json()["elevated_until"]

    me_response = service_client.get(
        "/auth/me"
    )

    assert me_response.status_code == 200
    assert (
        me_response.json()[
            "elevated_until"
        ]
        is not None
    )


def test_auth_routes_require_service_api_key(
    unauthenticated_client: TestClient,
    db_session: Session,
) -> None:
    create_test_user(
        db_session
    )

    response = unauthenticated_client.post(
        "/auth/login",
        json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        },
    )

    assert response.status_code == 401
    assert response.headers[
        "WWW-Authenticate"
    ] == "ApiKey"
