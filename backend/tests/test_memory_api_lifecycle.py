from datetime import datetime
from datetime import timezone

from fastapi.testclient import TestClient


NOW = datetime(
    2026,
    8,
    12,
    12,
    tzinfo=timezone.utc,
)


def _memory_payload(
    *,
    memory_key: str,
    title: str = "Status financeiro",
    content: str = "Pagamento pendente.",
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
        "context_data": {},
        "evidence": [],
    }


def _supersede_payload(
    *,
    title: str = "Status financeiro atualizado",
    content: str = "Pagamento confirmado.",
) -> dict[str, object]:
    return {
        "reason": "Informacao atualizada.",
        "memory_type": "fact",
        "title": title,
        "content": content,
        "importance": 0.9,
        "confidence": 1.0,
        "valid_from": NOW.isoformat(),
        "source": {
            "type": "database",
            "reference": "payments:991",
        },
        "context_data": {
            "currency": "BRL",
        },
        "evidence": [
            {
                "relation": "supports",
                "source_type": "database",
                "source_reference": "payments:991",
                "evidence_text": (
                    "Pagamento confirmado."
                ),
                "weight": 1.0,
            }
        ],
    }


def _create_memory(
    client: TestClient,
    *,
    memory_key: str,
) -> dict[str, object]:
    response = client.post(
        "/memories",
        json=_memory_payload(
            memory_key=memory_key,
        ),
    )

    assert response.status_code == 201

    return response.json()["memory"]


def test_supersede_returns_replacement_and_history_link(
    client: TestClient,
) -> None:
    original = _create_memory(
        client,
        memory_key="api.supersede.success",
    )
    response = client.post(
        f"/memories/{original['id']}/supersede",
        json=_supersede_payload(),
    )

    assert response.status_code == 201

    body = response.json()
    assert body["previous"]["id"] == original["id"]
    assert body["previous"]["status"] == "superseded"
    assert body["replacement"]["status"] == "active"
    assert body["replacement"]["memory_key"] == (
        original["memory_key"]
    )
    assert body["replacement"]["scope"] == (
        original["scope"]
    )
    assert body["replacement"][
        "supersedes_memory_id"
    ] == original["id"]
    assert body["replacement"][
        "created_by_user_id"
    ] is not None
    assert len(body["evidence"]) == 1


def test_supersede_rejects_server_controlled_scope(
    client: TestClient,
) -> None:
    original = _create_memory(
        client,
        memory_key="api.supersede.scope",
    )
    response = client.post(
        f"/memories/{original['id']}/supersede",
        json={
            **_supersede_payload(),
            "scope": {
                "type": "global",
            },
        },
    )

    assert response.status_code == 422


def test_supersede_rejects_final_state(
    client: TestClient,
) -> None:
    original = _create_memory(
        client,
        memory_key="api.supersede.final",
    )
    archived = client.post(
        f"/memories/{original['id']}/archive",
        json={},
    )
    response = client.post(
        f"/memories/{original['id']}/supersede",
        json=_supersede_payload(),
    )

    assert archived.status_code == 200
    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "invalid_memory_state"
    )


def test_invalidate_requires_non_blank_reason(
    client: TestClient,
) -> None:
    memory = _create_memory(
        client,
        memory_key="api.invalidate.reason",
    )
    response = client.post(
        f"/memories/{memory['id']}/invalidate",
        json={
            "reason": " ",
        },
    )

    assert response.status_code == 422


def test_invalidate_changes_status(
    client: TestClient,
) -> None:
    memory = _create_memory(
        client,
        memory_key="api.invalidate.success",
    )
    response = client.post(
        f"/memories/{memory['id']}/invalidate",
        json={
            "reason": "Origem incorreta.",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "invalidated"
    assert response.json()["status_reason"] == (
        "Origem incorreta."
    )


def test_archive_accepts_optional_reason(
    client: TestClient,
) -> None:
    memory = _create_memory(
        client,
        memory_key="api.archive.success",
    )
    response = client.post(
        f"/memories/{memory['id']}/archive",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    assert response.json()["status_reason"] is None


def test_missing_lifecycle_target_returns_404(
    client: TestClient,
) -> None:
    response = client.post(
        "/memories/999999/invalidate",
        json={
            "reason": "Registro inexistente.",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == (
        "memory_not_found"
    )


def test_add_evidence_returns_201_and_server_author(
    client: TestClient,
) -> None:
    memory = _create_memory(
        client,
        memory_key="api.evidence.create",
    )
    response = client.post(
        f"/memories/{memory['id']}/evidence",
        json={
            "relation": "supports",
            "source_type": "database",
            "source_reference": "payments:991",
            "evidence_text": "Pagamento confirmado.",
            "weight": 1.0,
            "context_data": {},
        },
    )

    assert response.status_code == 201

    body = response.json()
    assert body["created"] is True
    assert body["duplicate"] is False
    assert body["evidence"]["memory_id"] == memory["id"]
    assert body["evidence"][
        "created_by_user_id"
    ] is not None


def test_add_evidence_is_idempotent(
    client: TestClient,
) -> None:
    memory = _create_memory(
        client,
        memory_key="api.evidence.duplicate",
    )
    payload = {
        "relation": "supports",
        "source_type": "database",
        "source_reference": "payments:duplicate",
        "evidence_text": "Pagamento confirmado.",
        "weight": 1.0,
        "context_data": {},
    }
    first = client.post(
        f"/memories/{memory['id']}/evidence",
        json=payload,
    )
    second = client.post(
        f"/memories/{memory['id']}/evidence",
        json=payload,
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["duplicate"] is True
    assert second.json()["evidence"]["id"] == (
        first.json()["evidence"]["id"]
    )


def test_list_evidence_is_deterministic(
    client: TestClient,
) -> None:
    memory = _create_memory(
        client,
        memory_key="api.evidence.list",
    )

    for index in range(2):
        response = client.post(
            f"/memories/{memory['id']}/evidence",
            json={
                "relation": "context",
                "source_type": "system",
                "source_reference": f"test:{index}",
                "evidence_text": f"Evidence {index}.",
            },
        )
        assert response.status_code == 201

    response = client.get(
        f"/memories/{memory['id']}/evidence"
    )

    assert response.status_code == 200
    assert [
        item["source_reference"]
        for item in response.json()["items"]
    ] == ["test:0", "test:1"]


def test_evidence_source_memory_is_returned_when_accessible(
    client: TestClient,
) -> None:
    source = _create_memory(
        client,
        memory_key="api.evidence.source",
    )
    target = _create_memory(
        client,
        memory_key="api.evidence.target",
    )
    created = client.post(
        f"/memories/{target['id']}/evidence",
        json={
            "relation": "context",
            "source_type": "derived",
            "source_reference": "memory:source",
            "source_memory_id": source["id"],
            "evidence_text": "Memoria correlacionada.",
        },
    )
    listed = client.get(
        f"/memories/{target['id']}/evidence"
    )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()["items"][0][
        "source_memory_id"
    ] == source["id"]


def test_missing_evidence_parent_returns_404(
    client: TestClient,
) -> None:
    response = client.get(
        "/memories/999999/evidence"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == (
        "memory_not_found"
    )


def test_history_returns_complete_oldest_first_chain(
    client: TestClient,
) -> None:
    original = _create_memory(
        client,
        memory_key="api.history.chain",
    )
    first = client.post(
        f"/memories/{original['id']}/supersede",
        json=_supersede_payload(
            title="Versao 2",
            content="Segundo valor.",
        ),
    )
    first_id = first.json()["replacement"]["id"]
    second = client.post(
        f"/memories/{first_id}/supersede",
        json=_supersede_payload(
            title="Versao 3",
            content="Terceiro valor.",
        ),
    )
    second_id = second.json()["replacement"]["id"]
    history = client.get(
        f"/memories/{first_id}/history"
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert history.status_code == 200

    items = history.json()["items"]
    assert [item["id"] for item in items] == [
        original["id"],
        first_id,
        second_id,
    ]
    assert [item["status"] for item in items] == [
        "superseded",
        "superseded",
        "active",
    ]
    assert items[1]["supersedes_memory_id"] == (
        original["id"]
    )
    assert items[2]["supersedes_memory_id"] == first_id


def test_missing_history_target_returns_404(
    client: TestClient,
) -> None:
    response = client.get(
        "/memories/999999/history"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == (
        "memory_not_found"
    )
