from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.models.user import User


PASSWORD = "Senha-RBAC-Auneron-123!"


def create_user_and_login(
    client: TestClient,
    db_session: Session,
    *,
    role: str,
    email: str,
) -> None:
    user = User(
        name=f"Usuário {role}",
        email=email,
        password_hash=hash_password(
            PASSWORD
        ),
        role=role,
        active=True,
    )

    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": PASSWORD,
        },
    )

    assert response.status_code == 200


def test_service_api_key_alone_cannot_read_accounts(
    service_client: TestClient,
) -> None:
    response = service_client.get(
        "/accounts/"
    )

    assert response.status_code == 401
    assert response.headers[
        "WWW-Authenticate"
    ] == "Session"


def test_viewer_can_read_but_cannot_manage_accounts(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_user_and_login(
        service_client,
        db_session,
        role="viewer",
        email="viewer.test@example.com",
    )

    read_response = service_client.get(
        "/accounts/"
    )

    assert read_response.status_code == 200

    write_response = service_client.post(
        "/accounts/",
        json={
            "cliente": "Cliente Bloqueado",
            "valor": 1000.0,
            "vencimento": "2026-12-31",
            "status": "aberto",
        },
    )

    assert write_response.status_code == 403


def test_analyst_can_manage_accounts(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_user_and_login(
        service_client,
        db_session,
        role="analyst",
        email="analyst.test@example.com",
    )

    response = service_client.post(
        "/accounts/",
        json={
            "cliente": "Cliente Analista",
            "valor": 1500.0,
            "vencimento": "2026-12-31",
            "status": "aberto",
        },
    )

    assert response.status_code == 201


def test_executive_can_view_decisions_but_not_admin_metrics(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_user_and_login(
        service_client,
        db_session,
        role="executive",
        email="executive.test@example.com",
    )

    executive_response = service_client.get(
        "/orchestrator/decision/latest"
    )

    assert executive_response.status_code == 200

    admin_response = service_client.get(
        "/orchestrator/metrics"
    )

    assert admin_response.status_code == 403


def test_administrator_requires_elevation_for_admin_metrics(
    service_client: TestClient,
    db_session: Session,
) -> None:
    create_user_and_login(
        service_client,
        db_session,
        role="administrator",
        email="administrator.test@example.com",
    )

    before_elevation = service_client.get(
        "/orchestrator/metrics"
    )

    assert before_elevation.status_code == 403

    elevation_response = service_client.post(
        "/auth/elevate",
        json={
            "password": PASSWORD,
        },
    )

    assert elevation_response.status_code == 200

    after_elevation = service_client.get(
        "/orchestrator/metrics"
    )

    assert after_elevation.status_code == 200
