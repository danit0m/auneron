from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import API_KEY_HEADER_NAME


def test_public_endpoints_do_not_require_api_key(
    unauthenticated_client: TestClient,
) -> None:
    for endpoint in (
        "/",
        "/health",
        "/docs",
        "/openapi.json",
    ):
        response = unauthenticated_client.get(
            endpoint
        )

        assert response.status_code == 200, (
            endpoint,
            response.text,
        )


def test_protected_read_requires_api_key(
    unauthenticated_client: TestClient,
) -> None:
    response = unauthenticated_client.get(
        "/accounts/"
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == (
        "ApiKey"
    )
    assert response.json()["detail"] == (
        "Credencial de API inválida ou ausente."
    )


def test_invalid_api_key_is_rejected(
    unauthenticated_client: TestClient,
) -> None:
    response = unauthenticated_client.get(
        "/accounts/",
        headers={
            API_KEY_HEADER_NAME: (
                "invalid-api-key-00000000000000000000"
            ),
        },
    )

    assert response.status_code == 401


def test_valid_api_key_allows_protected_read(
    client: TestClient,
) -> None:
    response = client.get(
        "/accounts/"
    )

    assert response.status_code == 200
    assert response.json() == []


def test_unauthorized_write_does_not_mutate_database(
    unauthenticated_client: TestClient,
    db_session: Session,
) -> None:
    response = unauthenticated_client.post(
        "/accounts/",
        json={
            "cliente": "Cliente Sem Credencial",
            "valor": 1000.00,
            "vencimento": "2026-12-31",
            "status": "aberto",
        },
    )

    assert response.status_code == 401

    count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM accounts"
        )
    ).scalar_one()

    assert count == 0


def test_openapi_declares_api_key_security(
    unauthenticated_client: TestClient,
) -> None:
    response = unauthenticated_client.get(
        "/openapi.json"
    )

    assert response.status_code == 200

    schema = response.json()

    security_scheme = (
        schema["components"]["securitySchemes"][
            "AuneronApiKey"
        ]
    )

    assert security_scheme["type"] == "apiKey"
    assert security_scheme["in"] == "header"
    assert security_scheme["name"] == (
        API_KEY_HEADER_NAME
    )

    account_security = (
        schema["paths"]["/accounts/"]["get"][
            "security"
        ]
    )

    assert {
        "AuneronApiKey": []
    } in account_security
