from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any
from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account import Account
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


def _published_version(
    db_session: Session,
    *,
    skill_key: str,
    execution_mode: str = "read_only",
    capabilities: tuple[
        CapabilityInput,
        ...,
    ] = (),
    account_scope: bool = False,
    user_scope: bool = False,
):
    properties: dict[str, Any] = {
        "value": {
            "type": "integer",
        }
    }
    required = ["value"]

    if account_scope:
        properties["account_id"] = {
            "type": "integer",
            "minimum": 1,
        }
        required.append(
            "account_id"
        )

    if user_scope:
        properties[
            "subject_user_id"
        ] = {
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
        display_name="Skill API 23D",
        description=(
            "Skill para validar a API explícita 23D."
        ),
    )
    handler_name = (
        skill_key
        .replace(".", "_")
        .replace("-", "_")
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.api_23d:"
            + handler_name
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
        timeout_seconds=20,
        max_output_bytes=32768,
    )
    return service.publish_version(
        draft.id,
        capabilities=capabilities,
    ).version


@contextmanager
def _registered(
    version,
    handler,
) -> Generator[None, None, None]:
    skill_handler_registry.register(
        runtime_kind=version.runtime_kind,
        handler_reference=(
            version.handler_reference
        ),
        handler=handler,
    )
    try:
        yield
    finally:
        skill_handler_registry.unregister(
            runtime_kind=(
                version.runtime_kind
            ),
            handler_reference=(
                version.handler_reference
            ),
        )


def _invoke(
    client: TestClient,
    version_id: int,
    payload: dict[str, Any],
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
            "input_payload": payload,
        },
        headers=headers,
    )


def test_read_only_api_executes_as_authenticated_user(
    client: TestClient,
    db_session: Session,
) -> None:
    user = _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="api23d.read",
    )

    with _registered(
        version,
        lambda payload: {
            "result": payload["value"] * 2
        },
    ):
        response = _invoke(
            client,
            version.id,
            {"value": 6},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["duplicate"] is False
    assert body["output"] == {
        "result": 12
    }
    assert "actor_type" not in body
    assert "input_digest" not in body

    invocation = db_session.get(
        SkillInvocation,
        body["invocation_id"],
    )
    assert invocation is not None
    assert invocation.actor_type == "user"
    assert invocation.actor_user_id == user.id
    assert (
        invocation.actor_reference
        == f"user:{user.id}"
    )


def test_mutating_api_is_idempotent_and_executes_once(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "manager",
    )
    version = _published_version(
        db_session,
        skill_key="api23d.mutating",
        execution_mode="mutating",
    )
    calls = {"count": 0}

    def handler(payload):
        calls["count"] += 1
        return {
            "result": payload["value"]
        }

    with _registered(
        version,
        handler,
    ):
        first = _invoke(
            client,
            version.id,
            {"value": 3},
            idempotency_key="api23d-mutating-1",
        )
        second = _invoke(
            client,
            version.id,
            {"value": 3},
            idempotency_key="api23d-mutating-1",
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert (
        first.json()["invocation_id"]
        == second.json()["invocation_id"]
    )
    assert calls["count"] == 1


def test_account_scope_authorizes_exact_input_identifier(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "analyst",
    )
    account = Account(
        cliente="Conta Skill 23D",
        valor=Decimal("1000.00"),
        vencimento=date(
            2026,
            12,
            31,
        ),
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    version = _published_version(
        db_session,
        skill_key="api23d.account",
        capabilities=(
            CapabilityInput(
                capability_key="account.read",
                access_mode="read",
                resource_scope="account",
            ),
        ),
        account_scope=True,
    )
    seen = {}

    def handler(payload):
        seen["account_id"] = (
            payload["account_id"]
        )
        return {
            "result": payload["value"]
        }

    with _registered(
        version,
        handler,
    ):
        response = _invoke(
            client,
            version.id,
            {
                "value": 4,
                "account_id": account.id,
            },
        )

    assert response.status_code == 200
    assert seen["account_id"] == account.id


def test_user_scope_self_uses_exact_subject_identifier(
    client: TestClient,
    db_session: Session,
) -> None:
    user = _set_role(
        db_session,
        "analyst",
    )
    version = _published_version(
        db_session,
        skill_key="api23d.user-self",
        capabilities=(
            CapabilityInput(
                capability_key="profile.read",
                access_mode="read",
                resource_scope="user",
            ),
        ),
        user_scope=True,
    )
    seen = {}

    def handler(payload):
        seen["subject_user_id"] = (
            payload["subject_user_id"]
        )
        return {
            "result": payload["value"]
        }

    with _registered(
        version,
        handler,
    ):
        response = _invoke(
            client,
            version.id,
            {
                "value": 5,
                "subject_user_id": user.id,
            },
        )

    assert response.status_code == 200
    assert (
        seen["subject_user_id"]
        == user.id
    )


def test_developer_can_execute_external_only_while_elevated(
    client: TestClient,
    db_session: Session,
) -> None:
    _set_role(
        db_session,
        "developer",
    )
    version = _published_version(
        db_session,
        skill_key="api23d.external",
        execution_mode="external",
    )

    with _registered(
        version,
        lambda payload: {
            "result": payload["value"]
        },
    ):
        elevated = _invoke(
            client,
            version.id,
            {"value": 8},
            idempotency_key="api23d-external-1",
        )
        revoke = client.post(
            "/auth/elevation/revoke"
        )
        not_elevated = _invoke(
            client,
            version.id,
            {"value": 9},
            idempotency_key="api23d-external-2",
        )

    assert elevated.status_code == 200
    assert revoke.status_code == 204
    assert not_elevated.status_code == 403
    assert not_elevated.json()[
        "error"
    ]["code"] == "skill_forbidden"
