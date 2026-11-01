import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.account_data_entry_audit_event import AccountDataEntryAuditEvent
from app.models.user import User

# Mesmo usuário privilegiado criado pelo conftest (fixture client).
TEST_CLIENT_EMAIL = "developer.test@example.com"


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
    assert summary["importados"] == 1
    assert summary["duplicados"] == 0
    assert summary["erros"] == 1
    assert summary["valor_total"] == pytest.approx(
        1234.56,
    )

    accounts_response = client.get(
        "/accounts/",
    )

    assert len(accounts_response.json()) == 1


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
    assert "Status inicial não permitido no piloto" in (
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


def test_25o_import_rejects_non_aberto_initial_status_source_contract() -> None:
    from pathlib import Path
    source = Path("app/services/import_service.py").read_text(encoding="utf-8")
    assert 'if status and status != "aberto":' in source
    assert 'status = "aberto"' in source
    assert 'status="aberto"' in source


# ---- F1: duplicata normalizada dentro do mesmo arquivo legado ----------

# Linha 2 inválida com a mesma identidade (não reserva a chave); linha 3
# aceita; linha 4 equivalente pela identidade v1 (espaços, caixa, valor
# decimal e formato de data diferentes) => duplicado, não importado.
F1_CSV = (
    "cliente,valor,vencimento,status\n"
    "Cliente F1 Sintetico,1500.00,2026-12-31,pendente\n"
    "Cliente F1 Sintetico,1500.00,2026-12-31,aberto\n"
    "  CLIENTE   f1 SINTETICO ,1500,31/12/2026,\n"
)


def _counts(db_session: Session) -> tuple[int, int]:
    result = (
        db_session.scalar(select(func.count()).select_from(Account)),
        db_session.scalar(
            select(func.count()).select_from(AccountDataEntryAuditEvent)
        ),
    )
    db_session.rollback()
    return result


def test_upload_f1_in_file_equivalent_rows_insert_once(
    client: TestClient,
    db_session: Session,
) -> None:
    response = upload_csv(client, F1_CSV)

    assert response.status_code == 200
    result = response.json()["resultado"]
    assert result["summary"] == {
        "importados": 1,
        "duplicados": 1,
        "erros": 1,
        "valor_total": pytest.approx(1500.0),
    }
    assert [d["linha"] for d in result["detalhes_erros"]] == [2]
    assert _counts(db_session) == (1, 1)

    event = db_session.execute(select(AccountDataEntryAuditEvent)).scalar_one()
    account = db_session.execute(select(Account)).scalar_one()
    user = db_session.execute(
        select(User).where(User.email == TEST_CLIENT_EMAIL)
    ).scalar_one()
    db_session.rollback()

    assert account.cliente == "Cliente F1 Sintetico"
    assert (event.operation, event.channel) == ("create", "legacy_csv_upload")
    assert event.account_reference == account.id
    # Compatibilidade de ator: o usuário da sessão do fixture, sem fallback.
    assert event.actor_user_id == user.id
    assert event.actor_reference == f"user:{user.id}"
    assert event.auth_session_id is not None
    assert event.source_row == 3
    assert event.source_sha256 == hashlib.sha256(F1_CSV.encode("utf-8")).hexdigest()


def test_upload_f1_existing_db_duplicate_adds_no_account_or_audit(
    client: TestClient,
    db_session: Session,
) -> None:
    assert upload_csv(client, F1_CSV).status_code == 200
    assert _counts(db_session) == (1, 1)

    response = upload_csv(client, F1_CSV)

    assert response.status_code == 200
    summary = response.json()["resultado"]["summary"]
    assert (summary["importados"], summary["duplicados"], summary["erros"]) == (0, 2, 1)
    assert _counts(db_session) == (1, 1)
