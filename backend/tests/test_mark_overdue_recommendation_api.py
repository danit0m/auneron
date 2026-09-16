"""
Pilot Action Space V1.C -- camada HTTP de account.mark_overdue:
autorizacao (403), anti-IDOR (404), forma da resposta (200) e I2 (GET
nao escreve nada).
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
        cliente="Cliente Mark Overdue API Teste",
        email="cliente.mark.overdue.api@example.com",
        whatsapp=None,
        valor=500,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_mark_overdue_returns_200_with_frozen_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    response = client.get(
        "/recommendations/mark-overdue/accounts/"
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
    }
    assert payload["eligibility_policy"] == (
        "account_mark_overdue_eligibility_v1"
    )
    assert payload["episode"] == {
        "account_id": account.id,
        "due_date": due_date.isoformat(),
    }
    assert payload["structurally_available"] is True
    assert payload["system_recommendable"] is True
    assert payload["requires_external_fact"] is None
    assert payload["reason"] is None


def test_mark_overdue_due_date_mismatch_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    real_due_date = date.today() - timedelta(days=10)
    requested_due_date = real_due_date - timedelta(days=5)
    account = _make_account(db_session, vencimento=real_due_date)

    response = client.get(
        "/recommendations/mark-overdue/accounts/"
        f"{account.id}/episodes/{requested_due_date.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["structurally_available"] is False
    assert payload["reason"] == "due_date_mismatch"


def test_mark_overdue_requires_approval_read_permission(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    _set_role(db_session, "viewer")

    response = client.get(
        "/recommendations/mark-overdue/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 403


def test_mark_overdue_returns_404_for_nonexistent_account(
    client: TestClient,
) -> None:
    due_date = date.today().isoformat()

    response = client.get(
        f"/recommendations/mark-overdue/accounts/999999/episodes/{due_date}"
    )

    assert response.status_code == 404


def test_mark_overdue_get_performs_zero_writes(
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
        "/recommendations/mark-overdue/accounts/"
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
