from datetime import datetime
from datetime import timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.memory import MemoryItem


NOW = datetime(
    2026,
    8,
    12,
    12,
    tzinfo=timezone.utc,
)


def _global_payload(
    *,
    title: str = "Status financeiro",
    content: str = "Pagamento pendente.",
    memory_key: str | None = "payment_status",
) -> dict[str, object]:
    return {
        "memory_type": "fact",
        "title": title,
        "content": content,
        "memory_key": memory_key,
        "scope": {
            "type": "global",
        },
        "importance": 0.8,
        "confidence": 1.0,
        "valid_from": NOW.isoformat(),
        "source": {
            "type": "database",
            "reference": "accounts:42",
        },
        "context_data": {
            "currency": "BRL",
        },
        "evidence": [
            {
                "relation": "supports",
                "source_type": "database",
                "source_reference": "accounts:42",
                "evidence_text": (
                    "Registro financeiro confirmado."
                ),
                "weight": 1.0,
            }
        ],
    }


def test_create_memory_returns_201_and_server_fields(
    client: TestClient,
    db_session: Session,
) -> None:
    response = client.post(
        "/memories",
        json=_global_payload(),
    )

    assert response.status_code == 201

    body = response.json()
    assert body["created"] is True
    assert body["duplicate"] is False
    assert body["memory"]["status"] == "active"
    assert body["memory"]["scope"] == {
        "type": "global",
        "account_id": None,
        "subject_user_id": None,
    }
    assert body["memory"]["source"] == {
        "type": "database",
        "reference": "accounts:42",
    }
    assert body["memory"]["created_by_user_id"] is not None
    assert len(body["evidence"]) == 1

    stored = db_session.get(
        MemoryItem,
        body["memory"]["id"],
    )

    assert stored is not None
    assert stored.created_by_user_id == body["memory"][
        "created_by_user_id"
    ]


def test_equivalent_create_is_idempotent(
    client: TestClient,
) -> None:
    payload = _global_payload()
    first = client.post(
        "/memories",
        json=payload,
    )
    second = client.post(
        "/memories",
        json=payload,
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["duplicate"] is True
    assert (
        second.json()["memory"]["id"]
        == first.json()["memory"]["id"]
    )


def test_conflicting_active_key_returns_409(
    client: TestClient,
) -> None:
    first = client.post(
        "/memories",
        json=_global_payload(),
    )
    conflict = client.post(
        "/memories",
        json=_global_payload(
            content="Conteudo divergente.",
        ),
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == (
        "memory_active_key_conflict"
    )


def test_recall_and_get_memory(
    client: TestClient,
) -> None:
    created = client.post(
        "/memories",
        json=_global_payload(),
    )
    memory_id = created.json()["memory"]["id"]

    recalled = client.get(
        "/memories",
        params={
            "scope_type": "global",
        },
    )

    assert recalled.status_code == 200
    assert [
        item["id"]
        for item in recalled.json()["items"]
    ] == [memory_id]
    assert recalled.json()["page"] == {
        "limit": 20,
        "has_more": False,
        "next_cursor": None,
    }

    fetched = client.get(
        f"/memories/{memory_id}"
    )

    assert fetched.status_code == 200
    assert fetched.json()["id"] == memory_id


def test_recall_supports_opaque_cursor(
    client: TestClient,
) -> None:
    for index in range(3):
        response = client.post(
            "/memories",
            json=_global_payload(
                title=f"Memoria {index}",
                memory_key=f"memory_{index}",
            ),
        )
        assert response.status_code == 201

    first_page = client.get(
        "/memories",
        params={
            "scope_type": "global",
            "limit": 2,
        },
    )

    assert first_page.status_code == 200
    assert first_page.json()["page"]["has_more"] is True
    cursor = first_page.json()["page"]["next_cursor"]
    assert cursor

    second_page = client.get(
        "/memories",
        params={
            "scope_type": "global",
            "limit": 2,
            "cursor": cursor,
        },
    )

    assert second_page.status_code == 200

    first_ids = {
        item["id"]
        for item in first_page.json()["items"]
    }
    second_ids = {
        item["id"]
        for item in second_page.json()["items"]
    }

    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)


def test_invalid_cursor_returns_frozen_error(
    client: TestClient,
) -> None:
    response = client.get(
        "/memories",
        params={
            "scope_type": "global",
            "cursor": "invalid.cursor",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == (
        "invalid_cursor"
    )


def test_invalid_scope_combination_returns_422(
    client: TestClient,
) -> None:
    response = client.post(
        "/memories",
        json={
            **_global_payload(),
            "scope": {
                "type": "global",
                "account_id": 1,
            },
        },
    )

    assert response.status_code == 422


def test_server_controlled_fields_are_rejected(
    client: TestClient,
) -> None:
    response = client.post(
        "/memories",
        json={
            **_global_payload(),
            "status": "archived",
            "created_by_user_id": 999,
        },
    )

    assert response.status_code == 422


def test_memory_api_requires_api_key(
    unauthenticated_client: TestClient,
) -> None:
    response = unauthenticated_client.get(
        "/memories",
        params={
            "scope_type": "global",
        },
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == (
        "ApiKey"
    )


def test_memory_api_requires_user_session(
    service_client: TestClient,
) -> None:
    response = service_client.get(
        "/memories",
        params={
            "scope_type": "global",
        },
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == (
        "Session"
    )


def test_missing_memory_returns_404(
    client: TestClient,
) -> None:
    response = client.get(
        "/memories/999999"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == (
        "memory_not_found"
    )
