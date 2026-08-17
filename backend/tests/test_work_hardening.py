import inspect

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes import work as work_routes
from app.core.work_errors import WorkValidationError
from app.main import app
from app.services.work_service import WorkManagerService


def _create_work(
    client: TestClient,
    *,
    work_key: str,
) -> dict[str, object]:
    response = client.post(
        "/work-items",
        json={
            "work_type": "task",
            "title": "Trabalho paginado",
            "work_key": work_key,
            "scope": {"type": "global"},
            "context_data": {},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["work_item"]


def test_event_history_uses_bounded_cursor_pages(
    client: TestClient,
) -> None:
    item = _create_work(
        client,
        work_key="hardening.events.cursor",
    )
    version = 1

    for index in range(3):
        response = client.post(
            f"/work-items/{item['id']}/comments",
            json={
                "expected_version": version,
                "comment": f"Comentário {index}",
            },
        )
        assert response.status_code == 200, response.text
        version += 1

    first = client.get(
        f"/work-items/{item['id']}/events",
        params={"limit": 2},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] == (
        first_body["items"][-1]["id"]
    )

    second = client.get(
        f"/work-items/{item['id']}/events",
        params={
            "limit": 2,
            "after_id": first_body["next_cursor"],
        },
    )
    assert second.status_code == 200
    second_body = second.json()
    assert len(second_body["items"]) == 2
    assert second_body["next_cursor"] is None
    assert {
        event["id"]
        for event in first_body["items"]
    }.isdisjoint({
        event["id"]
        for event in second_body["items"]
    })


@pytest.mark.parametrize(
    "suffix",
    [
        "events",
        "dependencies",
        "memory-links",
        "recurrence/occurrences",
    ],
)
def test_historical_collection_limit_is_capped(
    client: TestClient,
    suffix: str,
) -> None:
    item = _create_work(
        client,
        work_key=(
            "hardening.limit."
            + suffix.replace("/", ".")
        ),
    )

    response = client.get(
        f"/work-items/{item['id']}/{suffix}",
        params={"limit": 101},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == (
        "invalid_work_request"
    )


def test_work_page_service_rejects_invalid_values(
    db_session: Session,
) -> None:
    item = WorkManagerService(db_session).create(
        work_type="task",
        title="Validação de página",
        work_key="hardening.service.page",
        scope_type="global",
        origin_type="system",
        origin_reference="test:hardening",
        actor=work_routes.WorkActor(
            actor_type="system",
            actor_reference="system:test",
        ),
    ).work_item
    service = WorkManagerService(db_session)

    with pytest.raises(WorkValidationError):
        service.list_events(item.id, limit=101)

    with pytest.raises(WorkValidationError):
        service.list_events(item.id, after_id=0)


def test_all_historical_routes_publish_cursor_contract() -> None:
    schema = app.openapi()
    paths = (
        "/work-items/{work_item_id}/events",
        "/work-items/{work_item_id}/dependencies",
        "/work-items/{work_item_id}/memory-links",
        "/work-items/{work_item_id}/recurrence/occurrences",
    )

    for path in paths:
        operation = schema["paths"][path]["get"]
        parameters = {
            parameter["name"]: parameter
            for parameter in operation["parameters"]
        }
        assert parameters["limit"]["schema"]["maximum"] == 100
        after_options = parameters[
            "after_id"
        ]["schema"]["anyOf"]
        integer_option = next(
            option
            for option in after_options
            if option.get("type") == "integer"
        )
        assert integer_option["exclusiveMinimum"] == 0


def test_work_observability_source_has_no_payload_fields() -> None:
    source = inspect.getsource(
        work_routes.log_work_change
    )

    assert "title" not in source
    assert "description" not in source
    assert "comment" not in source
    assert "context_data" not in source
    assert "event_data" not in source
    assert "idempotency_key" not in source
    assert "actor_reference" not in source
    assert "origin_reference" not in source
