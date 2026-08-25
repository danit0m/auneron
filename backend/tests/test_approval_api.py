import logging

from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.routes.approvals import get_approval_service
from app.core.approval_observability import log_approval_event
from app.main import app
from app.models.approval import ApprovalRequest
from app.models.skill import SkillInvocation
from app.models.user import User
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import ApprovalService
from app.services.skill_service import SkillService


AUTHENTICATED_EMAIL = "developer.test@example.com"
AUTHENTICATED_PASSWORD = "Senha-Teste-Auneron-123!"


def _current_user(
    db_session: Session,
) -> User:
    return (
        db_session.query(User)
        .filter(
            User.email
            == AUTHENTICATED_EMAIL
        )
        .one()
    )


def _set_role(
    db_session: Session,
    role: str,
) -> User:
    user = _current_user(
        db_session
    )
    user.role = role
    db_session.commit()
    db_session.refresh(user)
    return user


def _published_version(
    db_session: Session,
    *,
    skill_key: str,
    execution_mode: str = "read_only",
):
    service = SkillService(
        db_session
    )
    skill = service.register_skill(
        skill_key=skill_key,
        provider="auneron.core",
        display_name="Approval API 24B",
        description="Skill para validar Approval API 24B.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.approval_24b:"
            + skill_key.replace(
                ".",
                "_",
            ).replace(
                "-",
                "_",
            )
        ),
        execution_mode=execution_mode,
        input_schema={
            "type": "object",
            "properties": {
                "value": {
                    "type": "integer",
                }
            },
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "result": {
                    "type": "integer",
                }
            },
            "required": ["result"],
            "additionalProperties": False,
        },
    )
    return service.publish_version(
        draft.id,
        capabilities=(),
    ).version


def _request_approval(
    client: TestClient,
    version_id: int,
    *,
    value: int = 1,
    key: str = "approval-24b-key",
):
    return client.post(
        (
            "/approvals/skill-executions/"
            f"{version_id}"
        ),
        json={
            "input_payload": {
                "value": value,
            }
        },
        headers={
            "Idempotency-Key": key,
        },
    )


def _invocation_count(
    db_session: Session,
) -> int:
    return db_session.execute(
        select(
            func.count(
                SkillInvocation.id
            )
        )
    ).scalar_one()


def test_create_is_idempotent_and_never_executes_skill(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="approval24b.create",
    )

    first = _request_approval(
        client,
        version.id,
        value=7,
        key="approval-create-1",
    )
    second = _request_approval(
        client,
        version.id,
        value=7,
        key="approval-create-1",
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["duplicate"] is True
    assert (
        first.json()["request"]["request_id"]
        == second.json()["request"]["request_id"]
    )
    assert "input_digest" not in first.text
    assert "request_fingerprint" not in first.text
    assert "idempotency_key" not in first.text
    assert _invocation_count(
        db_session
    ) == 0


def test_manager_can_list_and_read_non_sensitive_request(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="approval24b.read",
    )
    created = _request_approval(
        client,
        version.id,
        key="approval-read-1",
    )
    request_id = created.json()[
        "request"
    ]["request_id"]

    _set_role(
        db_session,
        "manager",
    )

    listing = client.get(
        "/approvals"
    )
    details = client.get(
        f"/approvals/{request_id}"
    )

    assert listing.status_code == 200
    assert [
        item["request_id"]
        for item in listing.json()["items"]
    ] == [request_id]
    assert details.status_code == 200
    assert details.json()["decision"] is None


def test_low_risk_request_can_be_decided_without_elevation(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "manager",
    )
    version = _published_version(
        db_session,
        skill_key="approval24b.low-decision",
    )
    created = _request_approval(
        client,
        version.id,
        key="approval-low-decision-1",
    )
    request_id = created.json()[
        "request"
    ]["request_id"]

    response = client.post(
        f"/approvals/{request_id}/decision",
        json={
            "decision": "approved",
            "decision_note": "Aprovado para validação.",
        },
    )

    assert response.status_code == 200
    assert response.json()[
        "request"
    ]["status"] == "approved"
    assert response.json()[
        "decision"
    ]["decision"] == "approved"
    assert _invocation_count(
        db_session
    ) == 0


def test_high_risk_request_enforces_separation_of_duties(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "manager",
    )
    version = _published_version(
        db_session,
        skill_key="approval24b.high-self",
        execution_mode="mutating",
    )
    created = _request_approval(
        client,
        version.id,
        key="approval-high-self-1",
    )
    request_id = created.json()[
        "request"
    ]["request_id"]

    response = client.post(
        f"/approvals/{request_id}/decision",
        json={
            "decision": "approved",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"][
        "code"
    ] == "approval_forbidden"
    assert _invocation_count(
        db_session
    ) == 0


def test_sensitive_request_is_hidden_from_manager_and_requires_elevation(
    client: TestClient,
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="approval24b.critical",
        execution_mode="external",
    )
    service = ApprovalService(
        db_session
    )
    created = (
        service.create_skill_execution_request(
            version_id=version.id,
            requester=ApprovalRequester(
                actor_type="system",
                actor_reference="system:approval-test",
            ),
            input_payload={
                "value": 1,
            },
            idempotency_key="approval-critical-system-1",
        )
    )
    request_id = created.request.id

    _set_role(
        db_session,
        "manager",
    )
    hidden = client.get(
        f"/approvals/{request_id}"
    )
    listing = client.get(
        "/approvals"
    )

    assert hidden.status_code == 404
    assert listing.status_code == 200
    assert listing.json()["items"] == []

    _set_role(
        db_session,
        "executive",
    )
    revoke = client.post(
        "/auth/elevation/revoke"
    )
    assert revoke.status_code == 204

    not_elevated = client.post(
        f"/approvals/{request_id}/decision",
        json={
            "decision": "approved",
        },
    )
    assert not_elevated.status_code == 403
    assert not_elevated.json()[
        "error"
    ]["code"] == "approval_elevation_required"

    elevate = client.post(
        "/auth/elevate",
        json={
            "password": AUTHENTICATED_PASSWORD,
        },
    )
    assert elevate.status_code == 200

    approved = client.post(
        f"/approvals/{request_id}/decision",
        json={
            "decision": "approved",
        },
    )
    assert approved.status_code == 200
    assert approved.json()[
        "request"
    ]["status"] == "approved"
    assert _invocation_count(
        db_session
    ) == 0


def test_api_key_and_session_are_required_with_approval_contract(
    unauthenticated_client: TestClient,
    service_client: TestClient,
) -> None:
    no_api_key = unauthenticated_client.get(
        "/approvals"
    )
    no_session = service_client.get(
        "/approvals"
    )

    assert no_api_key.status_code == 401
    assert no_api_key.json()["error"][
        "code"
    ] == "approval_unauthenticated"
    assert no_session.status_code == 401
    assert no_session.json()["error"][
        "code"
    ] == "approval_unauthenticated"
    assert no_api_key.headers[
        "Cache-Control"
    ] == "no-store"
    assert no_session.headers[
        "Cache-Control"
    ] == "no-store"


def test_viewer_cannot_create_approval_request(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "viewer",
    )

    response = _request_approval(
        client,
        999999,
        key="approval-viewer-1",
    )

    assert response.status_code == 403
    assert response.json()["error"][
        "code"
    ] == "approval_forbidden"


def test_oversized_approval_payload_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )

    response = client.post(
        "/approvals/skill-executions/999999",
        json={
            "input_payload": {
                "padding": "x" * (
                    130 * 1024
                ),
            }
        },
        headers={
            "Idempotency-Key": "oversized-24b",
        },
    )

    assert response.status_code == 413
    assert response.json()["error"][
        "code"
    ] == "approval_payload_too_large"


def test_database_outage_is_sanitized(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="approval24b.db-outage",
    )

    class BrokenApprovalService:
        def create_skill_execution_request(
            self,
            *args,
            **kwargs,
        ):
            raise OperationalError(
                "SELECT secret",
                {},
                Exception(
                    "database-password"
                ),
            )

    app.dependency_overrides[
        get_approval_service
    ] = lambda: BrokenApprovalService()
    try:
        response = _request_approval(
            client,
            version.id,
            key="approval-db-outage-1",
        )
    finally:
        app.dependency_overrides.pop(
            get_approval_service,
            None,
        )

    assert response.status_code == 503
    assert response.json()["error"][
        "code"
    ] == "approval_unavailable"
    assert "database-password" not in response.text
    assert "SELECT secret" not in response.text


def test_approval_observability_drops_sensitive_fields(
    monkeypatch,
) -> None:
    captured = {}

    def fake_log(
        level,
        message,
        *,
        extra,
    ):
        captured["level"] = level
        captured["message"] = message
        captured["extra"] = extra

    from app.core import approval_observability

    monkeypatch.setattr(
        approval_observability.approval_observability_logger,
        "log",
        fake_log,
    )

    log_approval_event(
        "approval.test",
        level=logging.INFO,
        operation="read",
        user_id=7,
        approval_request_id=9,
        idempotency_key="SECRET-IDEMPOTENCY",
        input_payload={
            "password": "SECRET-PAYLOAD"
        },
        request_fingerprint="SECRET-FINGERPRINT",
    )

    assert captured["message"] == "approval.test"
    assert captured["extra"]["user_id"] == 7
    assert "idempotency_key" not in captured["extra"]
    assert "input_payload" not in captured["extra"]
    assert "request_fingerprint" not in captured["extra"]
