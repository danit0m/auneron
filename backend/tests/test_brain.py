from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.knowledge_service import KnowledgeService


def create_test_account(
    client: TestClient,
) -> dict:
    marker = uuid4().hex[:12]

    response = client.post(
        "/accounts/",
        json={
            "cliente": f"Cliente Brain {marker}",
            "email": f"brain.{marker}@outlook.com",
            "whatsapp": "11999999999",
            "valor": 12345.67,
            "vencimento": "2026-12-31",
            "status": "aberto",
        },
    )

    assert response.status_code == 201

    return response.json()


def seed_account_knowledge(
    db_session: Session,
    *,
    account_id: int,
    items: tuple[tuple[str, str], ...],
) -> list[int]:
    knowledge_ids: list[int] = []

    for index, (agent_name, severity) in enumerate(
        items,
        start=1,
    ):
        knowledge = KnowledgeService.create(
            db=db_session,
            agent_name=agent_name,
            event_name="test_seed",
            knowledge_type="test",
            severity=severity,
            title=f"Seed knowledge {index}",
            message=f"Seeded by test for {agent_name}",
            account_id=account_id,
        )
        knowledge_ids.append(knowledge.id)

    return knowledge_ids


def test_account_creation_does_not_create_legacy_agent_knowledge(
    client: TestClient,
) -> None:
    account = create_test_account(client)
    account_id = account["id"]

    response = client.get(
        "/brain/",
        params={
            "account_id": account_id,
            "limit": 100,
        },
    )

    assert response.status_code == 200
    assert response.json() == []


def test_resolve_and_reopen_knowledge(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_test_account(client)

    knowledge_ids = seed_account_knowledge(
        db_session,
        account_id=account["id"],
        items=(("FinanceAgent", "medium"),),
    )
    knowledge_id = knowledge_ids[0]

    get_response = client.get(
        f"/brain/{knowledge_id}",
    )

    assert get_response.status_code == 200
    assert get_response.json()["resolved"] is False

    resolve_response = client.patch(
        f"/brain/{knowledge_id}/resolve",
    )

    assert resolve_response.status_code == 200
    assert resolve_response.json()["resolved"] is True

    reopen_response = client.patch(
        f"/brain/{knowledge_id}/reopen",
    )

    assert reopen_response.status_code == 200
    assert reopen_response.json()["resolved"] is False


def test_account_delete_sets_knowledge_account_id_to_null(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_test_account(client)
    account_id = account["id"]

    knowledge_ids = seed_account_knowledge(
        db_session,
        account_id=account_id,
        items=(
            ("FinanceAgent", "medium"),
            ("RiskAgent", "high"),
            ("AnalyticsAgent", "info"),
        ),
    )

    assert len(knowledge_ids) == 3

    delete_response = client.delete(
        f"/accounts/{account_id}",
    )

    assert delete_response.status_code == 204

    missing_account_response = client.get(
        f"/accounts/{account_id}",
    )

    assert missing_account_response.status_code == 404

    rows = (
        db_session.execute(
            text(
                """
                SELECT
                    id,
                    account_id
                FROM knowledge
                WHERE id = ANY(:knowledge_ids)
                ORDER BY id
                """
            ),
            {
                "knowledge_ids": knowledge_ids,
            },
        )
        .mappings()
        .all()
    )

    assert len(rows) == 3
    assert all(
        row["account_id"] is None
        for row in rows
    )
