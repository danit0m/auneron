from uuid import uuid4

from fastapi.testclient import TestClient


def valid_account_payload() -> dict:
    marker = uuid4().hex[:12]

    return {
        "cliente": f"Cliente Regressao {marker}",
        "email": f"regressao.{marker}@outlook.com",
        "whatsapp": "11999999999",
        "valor": 12345.67,
        "vencimento": "2026-12-31",
    }


def test_create_get_and_update_account(
    client: TestClient,
) -> None:
    creation_response = client.post(
        "/accounts/",
        json=valid_account_payload(),
    )

    assert creation_response.status_code == 201

    created_account = creation_response.json()

    assert created_account["id"] > 0
    assert created_account["status"] == "aberto"
    assert created_account["valor"] == 12345.67
    assert created_account["created_at"]

    account_id = created_account["id"]

    get_response = client.get(
        f"/accounts/{account_id}",
    )

    assert get_response.status_code == 200
    assert get_response.json()["id"] == account_id

    update_response = client.put(
        f"/accounts/{account_id}",
        json={
            "valor": 13000.01,
            "status": "atrasado",
        },
    )

    assert update_response.status_code == 200

    updated_account = update_response.json()

    assert updated_account["status"] == "aberto"
    assert updated_account["valor"] == 13000.01


def test_create_ignores_client_supplied_status(
    client: TestClient,
) -> None:
    payload = valid_account_payload()
    payload["status"] = "atrasado"

    response = client.post(
        "/accounts/",
        json=payload,
    )

    assert response.status_code == 201
    assert response.json()["status"] == "aberto"


def test_rejects_zero_value(
    client: TestClient,
) -> None:
    payload = valid_account_payload()
    payload["valor"] = 0

    response = client.post(
        "/accounts/",
        json=payload,
    )

    assert response.status_code == 422


def test_rejects_blank_client_name(
    client: TestClient,
) -> None:
    payload = valid_account_payload()
    payload["cliente"] = "   "

    response = client.post(
        "/accounts/",
        json=payload,
    )

    assert response.status_code == 422