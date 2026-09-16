"""
Action Space Evaluator V1 -- camada HTTP: autorizacao (403), anti-IDOR
(404), forma da resposta (200) e I2 (GET nao escreve nada).
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
        cliente="Cliente Action Space API Teste",
        email="cliente.action.space.api@example.com",
        whatsapp=None,
        valor=750,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_action_space_returns_200_with_frozen_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=42)
    account = _make_account(db_session, vencimento=due_date)

    response = client.get(
        "/recommendations/action-space/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert set(payload.keys()) == {
        "episode",
        "actions",
        "recommendable_actions",
        "no_action",
    }
    assert payload["episode"] == {
        "account_id": account.id,
        "due_date": due_date.isoformat(),
    }
    assert len(payload["actions"]) == 3
    assert [a["action_key"] for a in payload["actions"]] == [
        "account.mark_overdue",
        "account.mark_paid",
        "escalate_to_human",
    ]
    assert payload["recommendable_actions"] == [
        "account.mark_overdue",
        "escalate_to_human",
    ]
    assert payload["no_action"] is False

    for action in payload["actions"]:
        assert set(action.keys()) == {
            "action_key",
            "capability_kind",
            "eligibility_policy",
            "structurally_available",
            "system_recommendable",
            "requires_external_fact",
            "reason",
            "initiation_actor",
            "authority_model",
            "required_authority",
            "execution_corridor",
        }

    mark_paid = next(
        a
        for a in payload["actions"]
        if a["action_key"] == "account.mark_paid"
    )
    assert mark_paid["structurally_available"] is True
    assert mark_paid["system_recommendable"] is False
    assert mark_paid["requires_external_fact"] == "payment_observed"


def test_action_space_no_action_true_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() + timedelta(days=30)
    account = _make_account(db_session, vencimento=due_date)

    response = client.get(
        "/recommendations/action-space/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["recommendable_actions"] == []
    assert payload["no_action"] is True


def test_action_space_requires_approval_read_permission(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    _set_role(db_session, "viewer")

    response = client.get(
        "/recommendations/action-space/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 403


def test_action_space_returns_404_for_nonexistent_account(
    client: TestClient,
) -> None:
    due_date = date.today().isoformat()

    response = client.get(
        f"/recommendations/action-space/accounts/999999/episodes/{due_date}"
    )

    assert response.status_code == 404


def test_action_space_get_performs_zero_writes(
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
        "/recommendations/action-space/accounts/"
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
