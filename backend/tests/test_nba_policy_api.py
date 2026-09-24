"""
NBA V1 -- camada HTTP: autorizacao (403), anti-IDOR (404), forma da
resposta (200) e I2 (GET nao escreve nada).
"""

from datetime import date
from datetime import timedelta
from decimal import Decimal

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
    valor: Decimal | float = 1000,
) -> Account:
    account = Account(
        cliente="Cliente NBA API Teste",
        email="cliente.nba.api@example.com",
        whatsapp=None,
        valor=valor,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_nba_returns_200_with_frozen_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    response = client.get(
        "/recommendations/next-best-action/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert set(payload.keys()) == {
        "episode",
        "action_space",
        "observed_facts",
        "policy_version",
        "calibration",
        "applied_rules",
        "decision",
        "requires_human_review",
        "human_review_reasons",
        "recommendation_snapshot_id",
    }
    assert payload["episode"] == {
        "account_id": account.id,
        "due_date": due_date.isoformat(),
    }
    assert set(payload["action_space"].keys()) == {
        "episode",
        "actions",
        "recommendable_actions",
        "no_action",
    }
    assert set(payload["observed_facts"].keys()) == {
        "days_overdue",
        "amount",
        "recurrence",
        "active_escalation",
    }
    assert payload["observed_facts"]["days_overdue"] == 10
    assert payload["observed_facts"]["recurrence"] is None
    assert set(payload["calibration"].keys()) == {
        "version",
        "critical_overdue_reference_days",
        "early_high_exposure_reference_day",
        "absolute_high_value_reference",
        "operational_cost_floor_reference",
    }
    assert payload["policy_version"] == "nba_policy_v1"
    assert set(payload["decision"].keys()) == {
        "decision_type",
        "selected_actions",
    }
    assert payload["decision"]["decision_type"] == "single_action"
    assert payload["decision"]["selected_actions"] == [
        "account.mark_overdue"
    ]
    assert payload["applied_rules"] == ["mark_overdue_precedence"]


def test_nba_no_action_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() + timedelta(days=30)
    account = _make_account(db_session, vencimento=due_date)

    response = client.get(
        "/recommendations/next-best-action/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["decision"]["decision_type"] == "no_action"
    assert payload["decision"]["selected_actions"] == []
    assert payload["applied_rules"] == []


def test_nba_requires_approval_read_permission(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    _set_role(db_session, "viewer")

    response = client.get(
        "/recommendations/next-best-action/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )

    assert response.status_code == 403


def test_nba_returns_404_for_nonexistent_account(
    client: TestClient,
) -> None:
    due_date = date.today().isoformat()

    response = client.get(
        f"/recommendations/next-best-action/accounts/999999/episodes/{due_date}"
    )

    assert response.status_code == 404


def test_nba_get_has_no_business_side_effects(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    I2 emendado (DW-6.4A): o GET do NBA nao cria, altera ou resolve
    estado de negocio, memoria, conhecimento, autoridade ou execucao.
    O unico apendice permitido -- nba_recommendation_snapshots -- e
    provado separadamente em
    test_nba_get_persists_exactly_one_recommendation_snapshot, nunca
    nesta lista.
    """
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    tables = (
        "accounts",
        "work_items",
        "knowledge",
        "memory_items",
        "approval_requests",
        "approval_decisions",
        "approval_consumptions",
        "skill_invocations",
        "account_events",
        "business_effect_verifications",
    )
    before = {
        table: db_session.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        ).scalar_one()
        for table in tables
    }

    response = client.get(
        "/recommendations/next-best-action/accounts/"
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


def test_nba_get_persists_exactly_one_recommendation_snapshot(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    I2 emendado (DW-6.4A): o unico efeito colateral permitido no GET
    do NBA e o apendice de proveniencia append-only. Cada GET produz
    uma ocorrencia nova -- nunca deduplicada por digest -- e o
    snapshot persistido corresponde exatamente a recomendacao servida,
    exceto pela identidade HTTP recommendation_snapshot_id (que so
    existe depois do snapshot ja persistido).
    """
    due_date = date.today() - timedelta(days=10)
    account = _make_account(db_session, vencimento=due_date)

    before = db_session.execute(
        text(
            "SELECT COUNT(*) FROM nba_recommendation_snapshots"
        )
    ).scalar_one()

    response = client.get(
        "/recommendations/next-best-action/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )
    assert response.status_code == 200
    payload = response.json()

    after = db_session.execute(
        text(
            "SELECT COUNT(*) FROM nba_recommendation_snapshots"
        )
    ).scalar_one()
    assert after == before + 1

    snapshot_id = payload["recommendation_snapshot_id"]
    row = db_session.execute(
        text(
            "SELECT account_id, due_date, policy_version, "
            "decision_type, requires_human_review, "
            "snapshot_payload, snapshot_digest "
            "FROM nba_recommendation_snapshots WHERE id = :id"
        ),
        {"id": snapshot_id},
    ).mappings().one()

    assert row["account_id"] == account.id
    assert row["due_date"].isoformat() == due_date.isoformat()
    assert row["policy_version"] == payload["policy_version"]
    assert (
        row["decision_type"]
        == payload["decision"]["decision_type"]
    )
    assert (
        row["requires_human_review"]
        == payload["requires_human_review"]
    )
    assert (
        row["snapshot_payload"]["decision"]
        == payload["decision"]
    )
    assert (
        row["snapshot_payload"]["applied_rules"]
        == payload["applied_rules"]
    )
    assert "recommendation_snapshot_id" not in row[
        "snapshot_payload"
    ]

    # Duas requisicoes consecutivas, mesmo conteudo computado, geram
    # DUAS ocorrencias -- nunca deduplicadas por digest.
    second_response = client.get(
        "/recommendations/next-best-action/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )
    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert (
        second_payload["recommendation_snapshot_id"]
        != snapshot_id
    )

    final_count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM nba_recommendation_snapshots"
        )
    ).scalar_one()
    assert final_count == before + 2
