from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session


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


def test_agents_create_account_knowledge(
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

    knowledge_items = response.json()

    assert len(knowledge_items) == 3
    assert all(
        item["account_id"] == account_id
        for item in knowledge_items
    )

    agent_names = {
        item["agent_name"]
        for item in knowledge_items
    }

    assert agent_names == {
        "FinanceAgent",
        "RiskAgent",
        "AnalyticsAgent",
    }


def test_resolve_and_reopen_knowledge(
    client: TestClient,
) -> None:
    account = create_test_account(client)

    knowledge_response = client.get(
        "/brain/",
        params={
            "account_id": account["id"],
            "limit": 100,
        },
    )

    knowledge_id = knowledge_response.json()[0]["id"]

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

    knowledge_response = client.get(
        "/brain/",
        params={
            "account_id": account_id,
            "limit": 100,
        },
    )

    knowledge_ids = [
        item["id"]
        for item in knowledge_response.json()
    ]

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