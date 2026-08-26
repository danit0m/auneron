from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.knowledge_service import KnowledgeService


def create_premium_account(
    client: TestClient,
) -> dict:
    marker = uuid4().hex[:12]

    response = client.post(
        "/accounts/",
        json={
            "cliente": f"Cliente Brain Extended {marker}",
            "email": f"brain.extended.{marker}@outlook.com",
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


def test_brain_filters_resolution_and_pagination(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_premium_account(client)

    seed_account_knowledge(
        db_session,
        account_id=account["id"],
        items=(
            ("FinanceAgent", "medium"),
            ("RiskAgent", "high"),
            ("AnalyticsAgent", "info"),
        ),
    )

    account_response = client.get(
        "/brain/",
        params={
            "account_id": account["id"],
            "limit": 100,
        },
    )

    assert account_response.status_code == 200

    account_items = account_response.json()

    assert len(account_items) == 3

    finance_response = client.get(
        "/brain/",
        params={
            "agent_name": "FinanceAgent",
        },
    )

    assert finance_response.status_code == 200
    assert len(finance_response.json()) == 1
    assert (
        finance_response.json()[0]["agent_name"]
        == "FinanceAgent"
    )

    severity_response = client.get(
        "/brain/",
        params={
            "severity": "high",
        },
    )

    assert severity_response.status_code == 200
    assert len(severity_response.json()) == 1
    assert severity_response.json()[0]["severity"] == "high"

    knowledge_id = account_items[0]["id"]

    resolve_response = client.patch(
        f"/brain/{knowledge_id}/resolve",
    )

    assert resolve_response.status_code == 200

    resolved_response = client.get(
        "/brain/",
        params={
            "resolved": "true",
        },
    )

    unresolved_response = client.get(
        "/brain/",
        params={
            "resolved": "false",
        },
    )

    assert len(resolved_response.json()) == 1
    assert len(unresolved_response.json()) == 2

    all_response = client.get(
        "/brain/",
        params={"limit": 100},
    )

    page_response = client.get(
        "/brain/",
        params={
            "skip": 1,
            "limit": 1,
        },
    )

    assert all_response.status_code == 200
    assert page_response.status_code == 200
    assert len(page_response.json()) == 1
    assert (
        page_response.json()[0]["id"]
        == all_response.json()[1]["id"]
    )


def test_delete_knowledge(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_premium_account(client)

    knowledge_ids = seed_account_knowledge(
        db_session,
        account_id=account["id"],
        items=(("FinanceAgent", "medium"),),
    )
    knowledge_id = knowledge_ids[0]

    delete_response = client.delete(
        f"/brain/{knowledge_id}",
    )

    assert delete_response.status_code == 204

    get_response = client.get(
        f"/brain/{knowledge_id}",
    )

    second_delete_response = client.delete(
        f"/brain/{knowledge_id}",
    )

    assert get_response.status_code == 404
    assert second_delete_response.status_code == 404


def test_missing_knowledge_operations_return_404(
    client: TestClient,
) -> None:
    knowledge_id = 999999

    responses = [
        client.get(
            f"/brain/{knowledge_id}",
        ),
        client.patch(
            f"/brain/{knowledge_id}/resolve",
        ),
        client.patch(
            f"/brain/{knowledge_id}/reopen",
        ),
        client.delete(
            f"/brain/{knowledge_id}",
        ),
    ]

    assert all(
        response.status_code == 404
        for response in responses
    )


def test_invalid_brain_parameters_return_422(
    client: TestClient,
) -> None:
    invalid_parameters = [
        {"skip": -1},
        {"limit": 0},
        {"limit": 501},
        {"account_id": 0},
    ]

    for parameters in invalid_parameters:
        response = client.get(
            "/brain/",
            params=parameters,
        )

        assert response.status_code == 422
