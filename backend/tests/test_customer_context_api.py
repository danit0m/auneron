"""
Customer Intelligence 360 V1 -- camada HTTP: autorizacao (403),
anti-IDOR (404) e forma da resposta (200).
"""

from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient
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
    email: str | None = None,
    vencimento: date,
) -> Account:
    account = Account(
        cliente="Cliente 360 API Teste",
        email=email,
        whatsapp=None,
        valor=750,
        vencimento=vencimento,
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_customer_context_returns_200_with_frozen_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() + timedelta(days=15)
    account = _make_account(db_session, vencimento=due_date)

    response = client.get(
        f"/customer-context/accounts/{account.id}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert set(payload.keys()) == {
        "requested_account_id",
        "aggregation_identity",
        "accounts",
        "financial_summary",
        "behavioral_intelligence",
        "episodes",
    }
    assert payload["requested_account_id"] == account.id
    assert payload["aggregation_identity"] == {
        "method": "single_account_no_email",
        "value": None,
        "linkage": "none_single_account",
        "anchor_account_id": account.id,
    }
    assert set(payload["financial_summary"].keys()) == {
        "total_exposure",
        "total_exposure_account_ids",
        "overdue_count",
        "overdue_account_ids",
        "due_soon_count",
        "due_soon_account_ids",
        "next_due_date",
        "next_due_account_ids",
        "provenance_class",
    }
    assert payload["financial_summary"][
        "provenance_class"
    ] == "derived"
    assert payload["behavioral_intelligence"] == {
        "classification_status": "not_classified_yet",
        "pattern": None,
        "classification": None,
    }
    assert len(payload["episodes"]) == 1


def test_customer_context_requires_approval_read_permission(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() + timedelta(days=15)
    account = _make_account(db_session, vencimento=due_date)

    _set_role(db_session, "viewer")

    response = client.get(
        f"/customer-context/accounts/{account.id}"
    )

    assert response.status_code == 403


def test_customer_context_returns_404_for_nonexistent_account(
    client: TestClient,
) -> None:
    response = client.get(
        "/customer-context/accounts/999999"
    )

    assert response.status_code == 404
