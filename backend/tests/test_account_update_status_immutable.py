"""
AREA2 R7 -- PUT /accounts/{id} nao e caminho de mutacao de status.

Garantia fixada: status de Account so muda pelos servicos governados
(mark_overdue humano/L3 e mark_paid). AccountUpdate nao declara
``status`` e o pydantic ignora chaves extras, entao um ``status``
enviado no PUT e descartado antes da rota. O ramo que gravaria
AccountEvent em api/routes/accounts.py (``"status" in dados``) fica
inalcancavel; se alguem adicionar ``status`` a AccountUpdate, este teste
falha antes de o PUT virar um atalho fora do Governed Action Model.

O PUT e uma requisicao autorizada e bem-sucedida de verdade: altera um
campo legitimo (whatsapp) para provar que o handler rodou e comitou --
nao e uma rejeicao por validacao. O estado e conferido por SQL direto no
banco, nao apenas pela resposta HTTP.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.account import Account


ORIGINAL_WHATSAPP = "11999999999"
UPDATED_WHATSAPP = "11888888888"


def _create_account(
    client: TestClient,
    db_session: Session,
    *,
    status: str,
) -> int:
    marker = uuid4().hex[:12]
    response = client.post(
        "/accounts/",
        json={
            "cliente": f"Cliente Status Imutavel {marker}",
            "email": f"status.imutavel.{marker}@outlook.com",
            "whatsapp": ORIGINAL_WHATSAPP,
            "valor": 4321.09,
            "vencimento": "2026-12-31",
        },
    )
    assert response.status_code == 201, response.text
    account_id = response.json()["id"]

    # Mesmo padrao de tests/test_accounts_extended.py: preparacao direta
    # do cenario, fora do corredor sob teste.
    if status != "aberto":
        account = db_session.get(Account, account_id)
        assert account is not None
        account.status = status
        db_session.commit()

    return account_id


def _persisted_row(db_session: Session, account_id: int):
    return db_session.execute(
        text(
            "SELECT status, whatsapp FROM accounts "
            "WHERE id = :account_id"
        ),
        {"account_id": account_id},
    ).one()


def _account_event_count(db_session: Session, account_id: int) -> int:
    return db_session.execute(
        text(
            "SELECT COUNT(*) FROM account_events "
            "WHERE account_id = :account_id"
        ),
        {"account_id": account_id},
    ).scalar_one()


def _total_account_event_count(db_session: Session) -> int:
    return db_session.execute(
        text("SELECT COUNT(*) FROM account_events")
    ).scalar_one()


@pytest.mark.parametrize(
    ("initial_status", "requested_status"),
    [
        ("aberto", "pago"),
        ("aberto", "atrasado"),
        ("atrasado", "pago"),
    ],
)
def test_put_with_status_does_not_mutate_status_nor_create_account_event(
    client: TestClient,
    db_session: Session,
    initial_status: str,
    requested_status: str,
) -> None:
    account_id = _create_account(
        client, db_session, status=initial_status
    )

    before = _persisted_row(db_session, account_id)
    assert before.status == initial_status
    assert before.whatsapp == ORIGINAL_WHATSAPP
    assert _account_event_count(db_session, account_id) == 0
    total_events_before = _total_account_event_count(db_session)

    response = client.put(
        f"/accounts/{account_id}",
        json={
            "whatsapp": UPDATED_WHATSAPP,
            "status": requested_status,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["whatsapp"] == UPDATED_WHATSAPP
    assert body["status"] == initial_status

    after = _persisted_row(db_session, account_id)
    # O campo legitimo foi gravado: o PUT de fato executou e comitou.
    assert after.whatsapp == UPDATED_WHATSAPP
    # O status nao mudou e nenhum AccountEvent foi criado.
    assert after.status == initial_status
    assert _account_event_count(db_session, account_id) == 0
    assert _total_account_event_count(db_session) == total_events_before
