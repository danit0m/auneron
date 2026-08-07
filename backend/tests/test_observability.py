import json
import logging
from uuid import UUID

from fastapi.testclient import TestClient

from app.core import observability
from app.core.observability import (
    JsonLogFormatter,
)
from app.core.observability import (
    REQUEST_ID_HEADER_NAME,
)
from app.core.security import (
    API_KEY_HEADER_NAME,
)
from app.core.security import security_logger


def test_response_receives_generated_request_id(
    unauthenticated_client: TestClient,
) -> None:
    response = unauthenticated_client.get(
        "/health"
    )

    assert response.status_code == 200

    request_id = response.headers[
        REQUEST_ID_HEADER_NAME
    ]

    assert str(
        UUID(request_id)
    ) == request_id


def test_valid_request_id_is_preserved(
    unauthenticated_client: TestClient,
) -> None:
    request_id = (
        "frontend-request-2026-08-07"
    )

    response = unauthenticated_client.get(
        "/health",
        headers={
            REQUEST_ID_HEADER_NAME: request_id,
        },
    )

    assert response.status_code == 200
    assert response.headers[
        REQUEST_ID_HEADER_NAME
    ] == request_id


def test_invalid_request_id_is_replaced(
    unauthenticated_client: TestClient,
) -> None:
    invalid_request_id = "x" * 129

    response = unauthenticated_client.get(
        "/health",
        headers={
            REQUEST_ID_HEADER_NAME: (
                invalid_request_id
            ),
        },
    )

    returned_request_id = response.headers[
        REQUEST_ID_HEADER_NAME
    ]

    assert returned_request_id != (
        invalid_request_id
    )
    assert str(
        UUID(returned_request_id)
    ) == returned_request_id


def test_json_formatter_redacts_sensitive_fields() -> None:
    formatter = JsonLogFormatter()

    record = logging.LogRecord(
        name="auneron.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="test_log",
        args=(),
        exc_info=None,
    )

    record.api_key = "super-secret-api-key"
    record.database_url = (
        "postgresql://user:password@host/db"
    )
    record.safe_field = "visible"

    serialized = formatter.format(
        record
    )
    payload = json.loads(
        serialized
    )

    assert payload["api_key"] == (
        "[REDACTED]"
    )
    assert payload["database_url"] == (
        "[REDACTED]"
    )
    assert payload["safe_field"] == "visible"
    assert "super-secret-api-key" not in (
        serialized
    )
    assert "password@host" not in serialized


def test_http_log_has_metadata_without_api_key(
    client: TestClient,
    monkeypatch,
) -> None:
    calls = []

    def capture_log(
        level,
        message,
        *,
        extra=None,
    ):
        calls.append({
            "level": level,
            "message": message,
            "extra": extra,
        })

    monkeypatch.setattr(
        observability.http_logger,
        "log",
        capture_log,
    )

    response = client.get(
        "/accounts/",
        headers={
            REQUEST_ID_HEADER_NAME: (
                "observability-test-request"
            ),
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1

    call = calls[0]

    assert call["message"] == (
        "http_request_completed"
    )
    assert call["extra"]["request_id"] == (
        "observability-test-request"
    )
    assert call["extra"]["method"] == "GET"
    assert call["extra"]["path"] == (
        "/accounts/"
    )
    assert call["extra"]["status_code"] == 200
    assert call["extra"]["duration_ms"] >= 0

    api_key = client.headers[
        API_KEY_HEADER_NAME
    ]

    assert api_key not in repr(call)


def test_security_log_never_contains_invalid_key(
    unauthenticated_client: TestClient,
    monkeypatch,
) -> None:
    calls = []

    def capture_warning(
        message,
        *,
        extra=None,
    ):
        calls.append({
            "message": message,
            "extra": extra,
        })

    monkeypatch.setattr(
        security_logger,
        "warning",
        capture_warning,
    )

    invalid_key = (
        "invalid-key-that-must-never-be-logged-123"
    )

    response = unauthenticated_client.get(
        "/accounts/",
        headers={
            API_KEY_HEADER_NAME: invalid_key,
            REQUEST_ID_HEADER_NAME: (
                "security-log-test"
            ),
        },
    )

    assert response.status_code == 401
    assert len(calls) == 1

    call = calls[0]

    assert call["message"] == (
        "api_auth_rejected"
    )
    assert call["extra"] == {
        "event": "api_auth",
        "request_id": "security-log-test",
        "reason": "invalid",
    }
    assert invalid_key not in repr(call)
