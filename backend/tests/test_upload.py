import pytest
from fastapi.testclient import TestClient


def upload_csv(
    client: TestClient,
    content: str,
    *,
    filename: str = "clientes.csv",
):
    return client.post(
        "/upload/",
        files={
            "file": (
                filename,
                content.encode("utf-8"),
                "text/csv",
            )
        },
    )


def test_upload_valid_csv(
    client: TestClient,
) -> None:
    content = (
        "cliente,email,whatsapp,valor,vencimento,status\n"
        "Cliente CSV 1,csv1@outlook.com,11911111111,"
        "1234.56,2026-12-31,aberto\n"
        "Cliente CSV 2,csv2@outlook.com,11922222222,"
        "2500.00,31/12/2026,pago\n"
    )

    response = upload_csv(
        client,
        content,
    )

    assert response.status_code == 200

    payload = response.json()
    summary = payload["resultado"]["summary"]

    assert payload["status"] == "sucesso"
    assert payload["arquivo"] == "clientes.csv"
    assert summary["importados"] == 2
    assert summary["duplicados"] == 0
    assert summary["erros"] == 0
    assert summary["valor_total"] == pytest.approx(
        3734.56,
    )

    accounts_response = client.get(
        "/accounts/",
    )

    assert len(accounts_response.json()) == 2


def test_upload_detects_duplicate_rows(
    client: TestClient,
) -> None:
    content = (
        "cliente,valor,vencimento,status\n"
        "Cliente Duplicado,1500.00,2026-12-31,aberto\n"
    )

    first_response = upload_csv(
        client,
        content,
    )

    second_response = upload_csv(
        client,
        content,
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    first_summary = (
        first_response.json()["resultado"]["summary"]
    )
    second_summary = (
        second_response.json()["resultado"]["summary"]
    )

    assert first_summary["importados"] == 1
    assert first_summary["duplicados"] == 0

    assert second_summary["importados"] == 0
    assert second_summary["duplicados"] == 1


def test_upload_reports_invalid_rows(
    client: TestClient,
) -> None:
    content = (
        "cliente,valor,vencimento,status\n"
        "Cliente Valido,1000.00,2026-12-31,aberto\n"
        "Cliente Invalido,2000.00,2026-12-31,pendente\n"
    )

    response = upload_csv(
        client,
        content,
    )

    assert response.status_code == 200

    result = response.json()["resultado"]
    summary = result["summary"]

    assert summary["importados"] == 1
    assert summary["erros"] == 1
    assert len(result["detalhes_erros"]) == 1
    assert result["detalhes_erros"][0]["linha"] == 3
    assert "Status inválido" in (
        result["detalhes_erros"][0]["erro"]
    )


def test_upload_rejects_missing_required_columns(
    client: TestClient,
) -> None:
    content = (
        "cliente,valor\n"
        "Cliente Incompleto,1000.00\n"
    )

    response = upload_csv(
        client,
        content,
    )

    assert response.status_code == 400
    assert (
        "Campos obrigatórios ausentes"
        in response.json()["detail"]
    )


def test_upload_rejects_non_csv_file(
    client: TestClient,
) -> None:
    response = upload_csv(
        client,
        "conteudo qualquer",
        filename="clientes.txt",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Envie um arquivo CSV."
    )


def test_upload_requires_file(
    client: TestClient,
) -> None:
    response = client.post(
        "/upload/",
    )

    assert response.status_code == 422
