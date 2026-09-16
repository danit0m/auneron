"""
Pilot Action Space V1.C -- camada HTTP de account.mark_paid:
autorizacao (403), anti-IDOR (404), forma da resposta (200, com a
garantia explicita de system_recommendable=false/requires_external_fact
="payment_observed" mesmo quando structurally_available=true) e I2
(GET nao escreve nada).
"""

from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.user import User


AUTHENTICATED_EMAIL = "developer.test@example.com"


def _current_user(db_session: Session) -> User:
    return (
        db_session.query(User)
        .filter(User.email == AUTHENTICATED_EMAIL)
        .one()
    )


def _set_role(db_session: Session, role: str) -> User:
    user = _current_user(db_session)
    user.role = role
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_account(
    db_session: Session,
    *,
    vencimento: date,
    status: str = "aberto",
) -> Account:
    account = Account(
        cliente="Cliente Mark Paid API Teste",
        email="cliente.mark.paid.api@example.com",
        whatsapp=None,
        valor=500,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_mark_paid_available_never_recommendable_frozen_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    O caso central do V1.C exposto via HTTP: disponibilidade estrutural
    nunca vira recomendacao sem payment_observed.
    """

    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    response = client.get(
        "/recommendations/mark-paid/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert set(payload.keys()) == {
        "eligibility_policy",
        "episode",
        "structurally_available",
        "system_recommendable",
        "requires_external_fact",
        "reason",
        "due_date_matches_current_vencimento",
    }
    assert payload["eligibility_policy"] == (
        "account_mark_paid_eligibility_v1"
    )
    assert payload["structurally_available"] is True
    assert payload["system_recommendable"] is False
    assert payload["requires_external_fact"] == "payment_observed"
    assert payload["reason"] is None
    assert payload["due_date_matches_current_vencimento"] is True


def test_mark_paid_already_paid_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(
        db_session, vencimento=due_date, status="pago"
    )

    response = client.get(
        "/recommendations/mark-paid/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["structurally_available"] is False
    assert payload["system_recommendable"] is False
    assert payload["requires_external_fact"] is None
    assert payload["reason"] == "already_paid"


def test_mark_paid_requires_approval_read_permission(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    _set_role(db_session, "viewer")

    response = client.get(
        "/recommendations/mark-paid/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 403


def test_mark_paid_returns_404_for_nonexistent_account(
    client: TestClient,
) -> None:
    due_date = date.today().isoformat()

    response = client.get(
        f"/recommendations/mark-paid/accounts/999999/episodes/{due_date}"
    )

    assert response.status_code == 404


def test_mark_paid_get_performs_zero_writes(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    tables = ("accounts", "work_items", "knowledge")
    before = {
        table: db_session.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        ).scalar_one()
        for table in tables
    }

    response = client.get(
        "/recommendations/mark-paid/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )
    assert response.status_code == 200

    after = {
        table: db_session.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        ).scalar_one()
        for table in tables
    }

    assert before == after
