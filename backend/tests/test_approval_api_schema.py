import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas.approval import ApprovalCreateRequest
from app.schemas.approval import ApprovalDecisionRequest
from app.schemas.approval import ApprovalRequestResponse


def test_create_schema_accepts_only_input_payload() -> None:
    request = ApprovalCreateRequest.model_validate({
        "input_payload": {
            "value": 1,
        }
    })

    assert request.input_payload == {
        "value": 1
    }


@pytest.mark.parametrize(
    "spoofed_field",
    [
        "actor_type",
        "actor_reference",
        "actor_user_id",
        "requester_user_id",
        "risk_level",
        "required_permission",
        "status",
        "request_fingerprint",
        "input_digest",
    ],
)
def test_create_schema_rejects_authority_spoof_fields(
    spoofed_field: str,
) -> None:
    with pytest.raises(
        ValidationError
    ):
        ApprovalCreateRequest.model_validate({
            "input_payload": {
                "value": 1,
            },
            spoofed_field: "spoofed",
        })


def test_decision_schema_is_bounded_and_forbids_extra_fields() -> None:
    request = ApprovalDecisionRequest.model_validate({
        "decision": "approved",
        "decision_note": "Aprovado.",
    })
    assert request.decision == "approved"

    with pytest.raises(
        ValidationError
    ):
        ApprovalDecisionRequest.model_validate({
            "decision": "approved",
            "decided_by_user_id": 123,
        })

    with pytest.raises(
        ValidationError
    ):
        ApprovalDecisionRequest.model_validate({
            "decision": "approved",
            "decision_note": "x" * 501,
        })


def test_public_request_response_exposes_no_digest_or_idempotency() -> None:
    fields = set(
        ApprovalRequestResponse.model_fields
    )

    assert fields == {
        "request_id",
        "action_type",
        "skill_version_id",
        "requester_actor_type",
        "requester_user_id",
        "risk_level",
        "status",
        "target_account_id",
        "target_user_id",
        "expires_at",
        "resolved_at",
        "created_at",
    }
    assert "input_digest" not in fields
    assert "request_fingerprint" not in fields
    assert "idempotency_key" not in fields
    assert "required_permission" not in fields
    assert "requester_reference" not in fields


def test_approval_api_exposes_exact_four_operations() -> None:
    schema = app.openapi()
    paths = schema.get(
        "paths",
        {},
    )
    methods = {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "options",
        "head",
        "trace",
    }

    operations = sorted(
        (
            method.upper(),
            path,
        )
        for path, path_item in paths.items()
        if path.startswith(
            "/approvals"
        )
        for method in path_item
        if method.lower() in methods
    )

    assert operations == [
        (
            "GET",
            "/approvals",
        ),
        (
            "GET",
            "/approvals/{request_id}",
        ),
        (
            "POST",
            (
                "/approvals/skill-executions/"
                "{version_id}"
            ),
        ),
        (
            "POST",
            "/approvals/{request_id}/decision",
        ),
    ]


def test_approval_api_has_no_execution_or_actor_selection_route() -> None:
    paths = app.openapi().get(
        "paths",
        {},
    )

    assert all(
        "/invoke" not in path
        and "/execute" not in path
        and "/actors" not in path
        for path in paths
        if path.startswith(
            "/approvals"
        )
    )
