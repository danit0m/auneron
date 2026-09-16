"""
Pilot Action Space V1.B -- camada HTTP: autorizacao (403), anti-IDOR
(404), forma da resposta (200, com status como unica fonte de
verdade) e I2 (GET nao escreve nada).
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
        cliente="Cliente Escalation API Teste",
        email="cliente.escalation.api@example.com",
        whatsapp=None,
        valor=750,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_human_escalation_returns_200_with_frozen_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    response = client.get(
        "/recommendations/human-escalation/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert set(payload.keys()) == {
        "recommendation_key",
        "episode",
        "status",
        "reason",
        "suppressing_work_item_id",
        "support_evidence",
    }
    assert payload["recommendation_key"] == (
        f"human_escalation_recommendation:v1:{account.id}:"
        f"{due_date.isoformat()}"
    )
    assert payload["episode"] == {
        "account_id": account.id,
        "due_date": due_date.isoformat(),
    }
    assert payload["status"] == "eligible"
    assert payload["reason"] is None
    assert payload["suppressing_work_item_id"] is None
    assert set(payload["support_evidence"].keys()) == {
        "days_overdue",
        "amount",
        "classification",
        "average_late_days",
        "prior_episodes",
    }
    assert payload["support_evidence"]["days_overdue"] == 10


def test_human_escalation_ineligible_shape_has_no_support_evidence(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=5)
    account = _make_account(
        db_session, vencimento=due_date, status="pago"
    )

    response = client.get(
        "/recommendations/human-escalation/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "ineligible"
    assert payload["reason"] == "account_paid"
    assert payload["support_evidence"] is None


def test_human_escalation_requires_approval_read_permission(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    _set_role(db_session, "viewer")

    response = client.get(
        "/recommendations/human-escalation/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 403


def test_human_escalation_returns_404_for_nonexistent_account(
    client: TestClient,
) -> None:
    due_date = date.today().isoformat()

    response = client.get(
        f"/recommendations/human-escalation/accounts/999999/episodes/{due_date}"
    )

    assert response.status_code == 404


def test_human_escalation_get_performs_zero_writes(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    I2 -- prova read-only via snapshot de contagem de linhas antes/
    depois, mesmo padrao ja usado em Outcome V1.
    """

    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    tables = ("work_items", "accounts", "knowledge")
    before = {
        table: db_session.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        ).scalar_one()
        for table in tables
    }

    response = client.get(
        "/recommendations/human-escalation/accounts/"
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
