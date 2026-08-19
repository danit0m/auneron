from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.routes.skills import (
    get_skill_runtime_service,
)
from app.core.authentication import hash_password
from app.main import app
from app.models.skill import SkillInvocation
from app.models.user import User
from app.services.skill_runtime import skill_handler_registry
from app.services.skill_service import CapabilityInput
from app.services.skill_service import SkillService


AUTHENTICATED_EMAIL = "developer.test@example.com"


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


def _another_user(
    db_session: Session,
    *,
    email: str,
) -> User:
    user = User(
        name="Usuário protegido Skill",
        email=email,
        password_hash=hash_password(
            "Senha-Protegida-Skill-123!"
        ),
        role="viewer",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _published_version(
    db_session: Session,
    *,
    skill_key: str,
    execution_mode: str = "read_only",
    capabilities: tuple[
        CapabilityInput,
        ...,
    ] = (),
    include_account: bool = False,
    include_user: bool = False,
):
    properties = {
        "value": {
            "type": "integer",
        }
    }
    required = ["value"]

    if include_account:
        properties["account_id"] = {
            "type": "integer",
            "minimum": 1,
        }
        required.append("account_id")

    if include_user:
        properties["subject_user_id"] = {
            "type": "integer",
            "minimum": 1,
        }
        required.append(
            "subject_user_id"
        )

    service = SkillService(
        db_session
    )
    skill = service.register_skill(
        skill_key=skill_key,
        provider="auneron.core",
        display_name="Skill API Security",
        description=(
            "Skill para segurança HTTP 23D."
        ),
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.security_23d:"
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
            "properties": properties,
            "required": required,
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
        capabilities=capabilities,
    ).version


def _invoke(
    client: TestClient,
    version_id: int,
    input_payload,
    *,
    idempotency_key: str | None = None,
):
    headers = {}
    if idempotency_key is not None:
        headers[
            "Idempotency-Key"
        ] = idempotency_key

    return client.post(
        (
            "/agent-skills/versions/"
            f"{version_id}/invoke"
        ),
        json={
            "input_payload": input_payload,
        },
        headers=headers,
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


def test_api_key_is_required_with_skill_error_contract(
    unauthenticated_client: TestClient,
) -> None:
    response = _invoke(
        unauthenticated_client,
        999999,
        {"value": 1},
    )

    assert response.status_code == 401
    assert response.json()["error"][
        "code"
    ] == "skill_unauthenticated"
    assert response.headers[
        "Cache-Control"
    ] == "no-store"


def test_user_session_is_required_with_skill_error_contract(
    service_client: TestClient,
) -> None:
    response = _invoke(
        service_client,
        999999,
        {"value": 1},
    )

    assert response.status_code == 401
    assert response.json()["error"][
        "code"
    ] == "skill_unauthenticated"
    assert response.headers[
        "Cache-Control"
    ] == "no-store"


def test_missing_base_permission_returns_403_before_version_lookup(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "viewer",
    )

    response = _invoke(
        client,
        999999,
        {"value": 1},
    )

    assert response.status_code == 403
    assert response.json()["error"][
        "code"
    ] == "skill_forbidden"
    assert _invocation_count(
        db_session
    ) == 0


def test_analyst_cannot_execute_mutating_and_no_ledger_is_created(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="security23d.mutating",
        execution_mode="mutating",
    )

    response = _invoke(
        client,
        version.id,
        {"value": 1},
        idempotency_key="security-mutating-1",
    )

    assert response.status_code == 403
    assert response.json()["error"][
        "code"
    ] == "skill_forbidden"
    assert _invocation_count(
        db_session
    ) == 0


def test_cross_user_scope_is_opaque_and_never_reaches_runtime(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "manager",
    )
    subject = _another_user(
        db_session,
        email=(
            "skill.cross.user@example.com"
        ),
    )
    version = _published_version(
        db_session,
        skill_key="security23d.cross-user",
        capabilities=(
            CapabilityInput(
                capability_key="profile.read",
                access_mode="read",
                resource_scope="user",
            ),
        ),
        include_user=True,
    )

    response = _invoke(
        client,
        version.id,
        {
            "value": 1,
            "subject_user_id": subject.id,
        },
    )

    assert response.status_code == 404
    assert response.json()["error"][
        "code"
    ] == "skill_not_found"
    assert _invocation_count(
        db_session
    ) == 0


def test_missing_account_scope_is_opaque_and_never_reaches_runtime(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="security23d.missing-account",
        capabilities=(
            CapabilityInput(
                capability_key="account.read",
                access_mode="read",
                resource_scope="account",
            ),
        ),
        include_account=True,
    )

    response = _invoke(
        client,
        version.id,
        {
            "value": 1,
            "account_id": 999999,
        },
    )

    assert response.status_code == 404
    assert response.json()["error"][
        "code"
    ] == "skill_not_found"
    assert _invocation_count(
        db_session
    ) == 0


def test_scope_identifier_cannot_be_supplied_without_capability(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="security23d.unbound-scope",
    )

    response = _invoke(
        client,
        version.id,
        {
            "value": 1,
            "account_id": 1,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"][
        "code"
    ] == "invalid_skill_request"
    assert _invocation_count(
        db_session
    ) == 0


def test_handler_failure_is_sanitized(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="security23d.handler-failure",
    )

    def failing_handler(_):
        raise RuntimeError(
            "SECRET-HANDLER-DETAIL"
        )

    skill_handler_registry.register(
        runtime_kind=version.runtime_kind,
        handler_reference=(
            version.handler_reference
        ),
        handler=failing_handler,
    )
    try:
        response = _invoke(
            client,
            version.id,
            {"value": 1},
        )
    finally:
        skill_handler_registry.unregister(
            runtime_kind=(
                version.runtime_kind
            ),
            handler_reference=(
                version.handler_reference
            ),
        )

    assert response.status_code == 502
    assert response.json()["error"][
        "code"
    ] == "skill_execution_failed"
    assert (
        "SECRET-HANDLER-DETAIL"
        not in response.text
    )
    assert response.headers[
        "Cache-Control"
    ] == "no-store"


def test_unregistered_handler_is_sanitized_as_unavailable(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="security23d.unregistered",
    )

    response = _invoke(
        client,
        version.id,
        {"value": 1},
    )

    assert response.status_code == 503
    assert response.json()["error"][
        "code"
    ] == "skill_runtime_unavailable"
    assert "handler" not in (
        response.text.lower()
    )


def test_oversized_http_payload_is_rejected_before_runtime(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )

    response = _invoke(
        client,
        999999,
        {
            "value": 1,
            "padding": "x" * (
                130 * 1024
            ),
        },
    )

    assert response.status_code == 413
    assert response.json()["error"][
        "code"
    ] == "skill_payload_too_large"
    assert _invocation_count(
        db_session
    ) == 0


def test_database_outage_is_sanitized_as_503(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="security23d.db-outage",
    )

    class BrokenRuntime:
        def invoke(self, *args, **kwargs):
            raise OperationalError(
                "SELECT secret",
                {},
                Exception(
                    "database-password"
                ),
            )

    app.dependency_overrides[
        get_skill_runtime_service
    ] = lambda: BrokenRuntime()
    try:
        response = _invoke(
            client,
            version.id,
            {"value": 1},
        )
    finally:
        app.dependency_overrides.pop(
            get_skill_runtime_service,
            None,
        )

    assert response.status_code == 503
    assert response.json()["error"][
        "code"
    ] == "skill_runtime_unavailable"
    assert "database-password" not in (
        response.text
    )
    assert "SELECT secret" not in (
        response.text
    )


def test_success_and_failure_responses_are_no_store(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="security23d.no-store",
    )

    skill_handler_registry.register(
        runtime_kind=version.runtime_kind,
        handler_reference=(
            version.handler_reference
        ),
        handler=lambda payload: {
            "result": payload["value"]
        },
    )
    try:
        success = _invoke(
            client,
            version.id,
            {"value": 2},
        )
    finally:
        skill_handler_registry.unregister(
            runtime_kind=(
                version.runtime_kind
            ),
            handler_reference=(
                version.handler_reference
            ),
        )

    failure = _invoke(
        client,
        999999,
        {"value": 2},
    )

    assert success.status_code == 200
    assert success.headers[
        "Cache-Control"
    ] == "no-store"
    assert failure.status_code == 404
    assert failure.headers[
        "Cache-Control"
    ] == "no-store"


def test_non_skill_errors_keep_existing_contract(
    unauthenticated_client: TestClient,
) -> None:
    response = unauthenticated_client.get(
        "/accounts/"
    )

    assert response.status_code == 401
    assert "detail" in response.json()
    assert "error" not in response.json()
