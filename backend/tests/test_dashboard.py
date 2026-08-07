from fastapi.testclient import TestClient


def create_account(
    client: TestClient,
    *,
    cliente: str,
    valor: float,
    vencimento: str,
    status: str,
) -> None:
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


def test_dashboard_calculations(
    client: TestClient,
) -> None:
    create_account(
        client,
        cliente="Cliente Pago",
        valor=1000.00,
        vencimento="2026-03-01",
        status="pago",
    )
    create_account(
        client,
        cliente="Cliente Aberto",
        valor=2000.00,
        vencimento="2026-02-01",
        status="aberto",
    )
    create_account(
        client,
        cliente="Cliente Atrasado",
        valor=3000.00,
        vencimento="2026-01-01",
        status="atrasado",
    )

    response = client.get(
        "/dashboard/",
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["resumo"] == {
        "clientes_total": 3,
        "faturamento_total": 6000.00,
        "recebido": 1000.00,
        "pendente": 5000.00,
        "atrasado": 3000.00,
    }

    assert payload["indicadores"] == {
        "taxa_recebimento": "16.67%",
        "ticket_medio": 2000.00,
        "clientes_atrasados": 1,
    }

    assert payload["status_clientes"] == {
        "pago": 1,
        "aberto": 1,
        "atrasado": 1,
    }

    assert [
        item["cliente"]
        for item in payload["ranking_clientes"]
    ] == [
        "Cliente Atrasado",
        "Cliente Aberto",
        "Cliente Pago",
    ]

    assert len(payload["alertas"]) == 1
    assert (
        payload["alertas"][0]["cliente"]
        == "Cliente Atrasado"
    )

    assert [
        item["cliente"]
        for item in payload["vencimentos"]
    ] == [
        "Cliente Atrasado",
        "Cliente Aberto",
        "Cliente Pago",
    ]
