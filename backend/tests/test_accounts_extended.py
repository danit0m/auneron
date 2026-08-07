from fastapi.testclient import TestClient


def create_account(
    client: TestClient,
    *,
    cliente: str,
    valor: float,
    vencimento: str,
    status: str,
) -> dict:
    response = client.post(
        "/accounts/",
        json={
            "cliente": cliente,
            "valor": valor,
            "vencimento": vencimento,
            "status": status,
        },
    )

    assert response.status_code == 201

    return response.json()


def test_list_accounts_filters_and_pagination(
    client: TestClient,
) -> None:
    create_account(
        client,
        cliente="Empresa Alpha Aberta",
        valor=1000.00,
        vencimento="2026-01-10",
        status="aberto",
    )
    create_account(
        client,
        cliente="Empresa Beta Atrasada",
        valor=2000.00,
        vencimento="2026-01-20",
        status="atrasado",
    )
    create_account(
        client,
        cliente="Empresa Alpha Paga",
        valor=3000.00,
        vencimento="2026-01-30",
        status="pago",
    )
    create_account(
        client,
        cliente="Empresa Gama Aberta",
        valor=4000.00,
        vencimento="2026-02-10",
        status="aberto",
    )

    status_response = client.get(
        "/accounts/",
        params={"status": "aberto"},
    )

    assert status_response.status_code == 200

    status_items = status_response.json()

    assert len(status_items) == 2
    assert all(
        item["status"] == "aberto"
        for item in status_items
    )

    client_response = client.get(
        "/accounts/",
        params={"cliente": "alpha"},
    )

    assert client_response.status_code == 200

    client_items = client_response.json()

    assert len(client_items) == 2
    assert all(
        "alpha" in item["cliente"].lower()
        for item in client_items
    )

    page_response = client.get(
        "/accounts/",
        params={
            "skip": 1,
            "limit": 2,
        },
    )

    assert page_response.status_code == 200

    page_items = page_response.json()

    assert [
        item["cliente"]
        for item in page_items
    ] == [
        "Empresa Beta Atrasada",
        "Empresa Alpha Paga",
    ]


def test_missing_account_operations_return_404(
    client: TestClient,
) -> None:
    account_id = 999999

    get_response = client.get(
        f"/accounts/{account_id}",
    )

    update_response = client.put(
        f"/accounts/{account_id}",
        json={"valor": 1000.00},
    )

    delete_response = client.delete(
        f"/accounts/{account_id}",
    )

    assert get_response.status_code == 404
    assert update_response.status_code == 404
    assert delete_response.status_code == 404

    assert get_response.json()["detail"]
    assert update_response.json()["detail"]
    assert delete_response.json()["detail"]


def test_invalid_account_pagination_returns_422(
    client: TestClient,
) -> None:
    invalid_parameters = [
        {"skip": -1},
        {"limit": 0},
        {"limit": 201},
    ]

    for parameters in invalid_parameters:
        response = client.get(
            "/accounts/",
            params=parameters,
        )

        assert response.status_code == 422
