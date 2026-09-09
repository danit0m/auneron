from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.client_classification import LABEL_ATRASO_RECORRENTE
from app.core.client_classification import LABEL_INSUFFICIENT_DATA
from app.core.client_classification import REASON_BELOW_MINIMUM
from app.core.client_classification import apply_client_classification
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.services.memory_service import MemoryService


def _unique_email() -> str:
    return f"classificacao.endpoint.{uuid4().hex[:12]}@example.com"


def _make_account(
    db: Session,
    *,
    email: str | None,
    vencimento: date,
    status: str = "pago",
) -> Account:
    account = Account(
        cliente="Cliente Teste Endpoint Classificacao",
        email=email,
        whatsapp=None,
        valor=Decimal("100.00"),
        vencimento=vencimento,
        status=status,
    )
    db.add(account)
    db.flush()
    return account


def _make_paid_event(
    db: Session,
    *,
    account: Account,
    occurred_at: datetime,
    previous_status: str = "atrasado",
) -> AccountEvent:
    event = AccountEvent(
        account_id=account.id,
        event_type="status_changed",
        actor_type="user",
        actor_reference="user:1",
        previous_status=previous_status,
        new_status="pago",
        occurred_at=occurred_at,
    )
    db.add(event)
    db.flush()
    return event


def _make_resolved_cycle(
    db: Session,
    *,
    email: str,
    vencimento: date,
    atraso_dias: int,
) -> Account:
    account = _make_account(
        db,
        email=email,
        vencimento=vencimento,
    )
    _make_paid_event(
        db,
        account=account,
        occurred_at=datetime(
            vencimento.year,
            vencimento.month,
            vencimento.day,
            tzinfo=timezone.utc,
        )
        + timedelta(days=atraso_dias),
    )
    return account


def test_classification_endpoint_returns_404_for_unknown_account(
    client: TestClient,
) -> None:
    response = client.get(
        "/accounts/999999999/classification"
    )

    assert response.status_code == 404


def test_classification_endpoint_not_classified_yet_when_no_memory(
    client: TestClient,
    db_session: Session,
) -> None:
    email = _unique_email()
    account = _make_account(
        db_session,
        email=email,
        vencimento=date(2026, 1, 1),
    )
    db_session.commit()

    response = client.get(
        f"/accounts/{account.id}/classification"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == account.id
    assert body["email"] == email
    assert body["status"] == "not_classified_yet"
    assert body["classification"] is None


def test_classification_endpoint_not_classified_yet_when_no_email(
    client: TestClient,
    db_session: Session,
) -> None:
    # Conta sem e-mail cadastrado -- Fatia 1/2A nunca classificam esse
    # caso, o endpoint deve tratar como not_classified_yet, e nunca
    # tentar agrupar por email=None (o que juntaria contas de clientes
    # totalmente diferentes).
    account = _make_account(
        db_session,
        email=None,
        vencimento=date(2026, 1, 2),
        status="aberto",
    )
    db_session.commit()

    response = client.get(
        f"/accounts/{account.id}/classification"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == account.id
    assert body["email"] is None
    assert body["status"] == "not_classified_yet"
    assert body["classification"] is None


def test_classification_endpoint_returns_insufficient_data(
    client: TestClient,
    db_session: Session,
) -> None:
    email = _unique_email()

    # Duas ocorrencias resolvidas -- abaixo do minimo (3).
    for index, atraso in enumerate([2, 0]):
        _make_resolved_cycle(
            db_session,
            email=email,
            vencimento=date(2026, 2, 1 + index),
            atraso_dias=atraso,
        )

    db_session.commit()

    memory_service = MemoryService(db_session)
    apply_client_classification(
        db_session, memory_service, email
    )
    db_session.commit()

    response = client.get(
        f"/accounts/{_oldest_account_id_for_test(db_session, email)}"
        "/classification"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == email
    assert body["status"] == "classified"

    classification = body["classification"]
    assert classification["label"] == LABEL_INSUFFICIENT_DATA
    assert classification["reason"] == REASON_BELOW_MINIMUM
    assert classification["resolved_occurrences"] == 2
    assert classification["late_occurrences"] is None
    assert classification["late_ratio"] is None
    assert (
        classification["analysis_scope"]
        == "all_available_history"
    )


def test_classification_endpoint_returns_atraso_recorrente_for_any_account_of_client(
    client: TestClient,
    db_session: Session,
) -> None:
    email = _unique_email()

    for index, atraso in enumerate([1, 2, 0, 0]):
        _make_resolved_cycle(
            db_session,
            email=email,
            vencimento=date(2026, 3, 1 + index),
            atraso_dias=atraso,
        )

    db_session.commit()

    memory_service = MemoryService(db_session)
    apply_client_classification(
        db_session, memory_service, email
    )
    db_session.commit()

    # Conta mais nova do mesmo cliente -- nao e a conta interna onde a
    # memoria de fato foi gravada (oldest_account_id). O endpoint deve
    # resolver isso internamente e devolver o account_id pedido (nao o
    # interno), mantendo a resolucao invisivel pra quem chama.
    newest_account = _make_account(
        db_session,
        email=email,
        vencimento=date(2026, 3, 20),
        status="aberto",
    )
    db_session.commit()

    response = client.get(
        f"/accounts/{newest_account.id}/classification"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == newest_account.id
    assert body["email"] == email
    assert body["status"] == "classified"

    classification = body["classification"]
    assert classification["label"] == LABEL_ATRASO_RECORRENTE
    assert classification["reason"] is None
    assert classification["resolved_occurrences"] == 4
    assert classification["late_occurrences"] == 2
    assert classification["late_ratio"] == 0.5
    assert classification["minimum_required_occurrences"] == 3
    assert classification["late_ratio_threshold"] == 0.5
    assert classification["rule_version"] == "recurrence_v1"
    assert classification["classified_at"]
    assert classification["period_start"] is not None
    assert classification["period_end"] is not None


def _oldest_account_id_for_test(
    db: Session,
    email: str,
) -> int:
    from app.core.client_classification import _oldest_account_id

    account_id = _oldest_account_id(db, email)
    assert account_id is not None
    return account_id
