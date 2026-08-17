import json
import logging

from fastapi.testclient import TestClient

from app.core import work_observability
from app.core.observability import JsonLogFormatter
from app.core.observability import REQUEST_ID_HEADER_NAME


def _payload(
    *,
    work_key: str,
    title: str = "Trabalho observável",
    context_data: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "work_type": "task",
        "title": title,
        "work_key": work_key,
        "scope": {"type": "global"},
        "context_data": context_data or {},
    }


def test_work_creation_log_contains_only_safe_metadata(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def capture_info(
        message: str,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        calls.append({
            "message": message,
            "extra": extra,
        })

    monkeypatch.setattr(
        work_observability.work_observability_logger,
        "info",
        capture_info,
    )

    title_secret = "internal-customer-secret-title"
    context_secret = "private-context-payload"
    idempotency_secret = "private-idempotency-key"
    response = client.post(
        "/work-items",
        json=_payload(
            work_key="observability.safe.creation",
            title=title_secret,
            context_data={"secret": context_secret},
        ),
        headers={
            REQUEST_ID_HEADER_NAME: "work-observability-create",
            "Idempotency-Key": idempotency_secret,
        },
    )

    assert response.status_code == 201
    assert len(calls) == 1
    call = calls[0]
    assert call["message"] == "work_change_completed"
    assert call["extra"] == {
        "event": "work.change",
        "request_id": "work-observability-create",
        "outcome": "applied",
        "work_item_id": response.json()["work_item"]["id"],
        "scope_type": "global",
        "work_event_type": "created",
        "actor_type": "user",
        "actor_user_id": response.json()["event"][
            "actor_user_id"
        ],
        "version": 1,
    }
    serialized_call = repr(call)
    assert title_secret not in serialized_call
    assert context_secret not in serialized_call
    assert idempotency_secret not in serialized_call


def test_work_replay_log_is_distinguishable_without_key(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def capture_info(
        message: str,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        calls.append({
            "message": message,
            "extra": extra,
        })

    monkeypatch.setattr(
        work_observability.work_observability_logger,
        "info",
        capture_info,
    )

    key = "observability-replay-sensitive-key"
    payload = _payload(
        work_key="observability.safe.replay"
    )
    headers = {"Idempotency-Key": key}

    first = client.post(
        "/work-items",
        json=payload,
        headers=headers,
    )
    replay = client.post(
        "/work-items",
        json=payload,
        headers=headers,
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert len(calls) == 2
    assert calls[0]["extra"]["outcome"] == "applied"
    assert calls[1]["extra"]["outcome"] == "replayed"
    assert key not in repr(calls)


def test_json_formatter_redacts_work_idempotency_fields() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="auneron.work.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="work_test",
        args=(),
        exc_info=None,
    )
    record.idempotency_key = "sensitive-retry-key"
    record.credential_hint = "sensitive-credential"
    record.work_item_id = 42

    serialized = formatter.format(record)
    payload = json.loads(serialized)

    assert payload["idempotency_key"] == "[REDACTED]"
    assert payload["credential_hint"] == "[REDACTED]"
    assert payload["work_item_id"] == 42
    assert "sensitive-retry-key" not in serialized
    assert "sensitive-credential" not in serialized
