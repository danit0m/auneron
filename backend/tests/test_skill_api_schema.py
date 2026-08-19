import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas.skill import SkillInvocationResponse
from app.schemas.skill import SkillInvokeRequest


def test_skill_invoke_request_accepts_only_input_payload() -> None:
    request = SkillInvokeRequest.model_validate({
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
        "agent_name",
        "scope",
    ],
)
def test_skill_invoke_request_rejects_authority_spoof_fields(
    spoofed_field: str,
) -> None:
    with pytest.raises(
        ValidationError
    ):
        SkillInvokeRequest.model_validate({
            "input_payload": {
                "value": 1,
            },
            spoofed_field: "spoofed",
        })


def test_skill_response_exposes_no_internal_authority_or_digest_fields() -> None:
    fields = set(
        SkillInvocationResponse.model_fields
    )

    assert fields == {
        "invocation_id",
        "skill_version_id",
        "status",
        "duplicate",
        "output",
        "started_at",
        "finished_at",
        "duration_ms",
    }
    assert "actor_user_id" not in fields
    assert "request_fingerprint" not in fields
    assert "input_digest" not in fields
    assert "error_code" not in fields


HTTP_METHODS = frozenset({
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "options",
    "head",
    "trace",
})


def _skill_openapi_operations() -> list[tuple[str, str]]:
    schema = app.openapi()
    paths = schema.get("paths", {})
    operations: list[tuple[str, str]] = []

    for path, path_item in paths.items():
        if not path.startswith(
            "/agent-skills"
        ):
            continue

        for method in path_item:
            normalized_method = method.lower()
            if normalized_method not in HTTP_METHODS:
                continue

            operations.append(
                (
                    normalized_method.upper(),
                    path,
                )
            )

    return sorted(operations)


def test_skill_api_exposes_exactly_one_public_operation() -> None:
    assert _skill_openapi_operations() == [
        (
            "POST",
            (
                "/agent-skills/versions/"
                "{version_id}/invoke"
            ),
        )
    ]


def test_skill_api_has_no_public_invocation_history_route() -> None:
    paths = app.openapi().get(
        "paths",
        {},
    )

    forbidden = [
        path
        for path in paths
        if path.startswith(
            "/agent-skills/invocations"
        )
    ]

    assert forbidden == []
