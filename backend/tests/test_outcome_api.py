"""
Outcome Intelligence V1 -- camada HTTP: autorizacao (403), anti-IDOR
(404) e forma da resposta (200), incluindo o caso totalmente ausente.
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
    vencimento: date,
    status: str = "aberto",
) -> Account:
    account = Account(
        cliente="Cliente Outcome API Teste",
        email="cliente.outcome.api@example.com",
        whatsapp=None,
        valor=500,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_outcome_episode_returns_200_with_all_categories_absent(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() + timedelta(days=30)
    account = _make_account(db_session, vencimento=due_date)

    response = client.get(
        f"/outcomes/accounts/{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["episode"] == {
        "account_id": account.id,
        "due_date": due_date.isoformat(),
    }
    assert payload["detection"]["linkage"] == "absent"
    assert payload["recommendation"]["linkage"] == "absent"
    assert payload["approval"]["linkage"] == "absent"
    assert payload["execution"]["linkage"] == "absent"
    assert payload["recommendation_provenance"]["linkage"] == "absent"
    assert payload["payment"]["linkage"] == "absent"
    assert payload["derived"]["days_detection_to_payment"] is None
    assert payload["amount"]["value"] == 500.0
    assert payload["amount"]["provenance_class"] == "fact"


def test_outcome_episode_requires_approval_read_permission(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=5)
    account = _make_account(db_session, vencimento=due_date)

    _set_role(db_session, "viewer")

    response = client.get(
        f"/outcomes/accounts/{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 403


def test_outcome_episode_returns_404_for_nonexistent_account(
    client: TestClient,
) -> None:
    due_date = date.today().isoformat()

    response = client.get(
        f"/outcomes/accounts/999999/episodes/{due_date}"
    )

    assert response.status_code == 404


def test_outcome_episode_response_matches_frozen_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=1)
    account = _make_account(db_session, vencimento=due_date)

    response = client.get(
        f"/outcomes/accounts/{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert set(payload.keys()) == {
        "episode",
        "detection",
        "recommendation",
        "approval",
        "execution",
        "recommendation_provenance",
        "human_approval",
        "human_execution",
        "effect_verification",
        "payment",
        "derived",
        "amount",
    }
    assert set(payload["episode"].keys()) == {
        "account_id",
        "due_date",
    }
    assert set(payload["recommendation_provenance"].keys()) == {
        "linkage",
        "recommendation_snapshot_id",
        "policy_version",
        "decision_type",
        "selected_actions",
        "requires_human_review",
        "created_at",
        "evidence",
    }
    assert payload["recommendation_provenance"]["linkage"] == "absent"
    assert set(payload["payment"].keys()) == {
        "linkage",
        "rule_version",
        "occurred_at",
        "evidence",
        "candidate_evidence",
    }
    assert payload["payment"]["rule_version"] == "outcome_correlation_v1"
    assert set(payload["approval"].keys()) == {
        "linkage",
        "status",
        "decided_at",
        "decided_by_reference",
        "approval_source_attempt",
        "attempt_mismatch",
        "evidence",
    }
