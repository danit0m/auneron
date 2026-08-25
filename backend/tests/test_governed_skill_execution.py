from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalConsumptionConflictError
from app.core.approval_errors import ApprovalRequiredError
from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.core.skill_errors import SkillAuthorizationError
from app.core.skill_errors import SkillConflictError
from app.core.skill_errors import SkillExecutionError
from app.core.skill_errors import SkillValidationError
from app.models.approval import ApprovalConsumption
from app.models.skill import SkillInvocation
from app.models.user import User
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import ApprovalService
from app.services.governed_skill_execution import GovernedSkillExecutionService
from app.services.isolated_skill_executor import IsolatedSkillExecutor
from app.services.skill_runtime import SkillHandlerRegistry
from app.services.skill_runtime import SkillInvocationActor
from app.services.skill_runtime import SkillRuntimeService
from app.services.skill_service import CapabilityInput
from app.services.skill_service import SkillService


def _user(
    db_session: Session,
    *,
    email: str,
    role: str,
) -> User:
    user = User(
        name="Governed execution test",
        email=email,
        password_hash="not-used",
        role=role,
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
    runtime_kind: str = "internal_python",
    execution_mode: str = "read_only",
    capabilities: tuple[
        CapabilityInput,
        ...,
    ] = (),
):
    service = SkillService(
        db_session
    )
    skill = service.register_skill(
        skill_key=skill_key,
        provider="auneron.core",
        display_name="Governed 24D",
        description="Skill para execução governada 24D.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind=runtime_kind,
        handler_reference=(
            "app.skills.governed:"
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
                },
            },
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "result": {
                    "type": "integer",
                },
            },
            "required": ["result"],
            "additionalProperties": False,
        },
    )
    return service.publish_version(
        draft.id,
        capabilities=capabilities,
    ).version


def _runtime(
    db_session: Session,
    *,
    version,
    handler,
    trusted_for_autonomy: bool = True,
    autonomy_entrypoint: str | None = None,
):
    registry = SkillHandlerRegistry()
    effective_entrypoint = autonomy_entrypoint
    if (
        effective_entrypoint is None
        and trusted_for_autonomy
        and version.runtime_kind == "internal_python"
    ):
        effective_entrypoint = (
            "isolated_skill_handlers:identity"
        )

    registry.register(
        runtime_kind=version.runtime_kind,
        handler_reference=version.handler_reference,
        handler=handler,
        trusted_for_autonomy=trusted_for_autonomy,
        autonomy_entrypoint=effective_entrypoint,
    )
    isolated_executor = IsolatedSkillExecutor(
        max_workers=1,
        python_path_entries=(
            str(Path(__file__).resolve().parent),
        ),
    )
    runtime = SkillRuntimeService(
        db_session,
        handler_registry=registry,
        isolated_executor=isolated_executor,
    )
    governed = GovernedSkillExecutionService(
        db_session,
        runtime=runtime,
    )
    return governed, isolated_executor


def _actor(
    value: str = "agent:finance",
) -> SkillInvocationActor:
    actor_type = value.split(
        ":",
        1,
    )[0]
    return SkillInvocationActor(
        actor_type=actor_type,
        actor_reference=value,
    )


def _approve(
    db_session: Session,
    *,
    version,
    actor: SkillInvocationActor,
    input_payload,
    key: str,
    decider: User,
    sensitive: bool = False,
):
    service = ApprovalService(
        db_session
    )
    created = service.create_skill_execution_request(
        version_id=version.id,
        requester=ApprovalRequester(
            actor_type=actor.actor_type,
            actor_reference=actor.actor_reference,
        ),
        input_payload=input_payload,
        idempotency_key=key,
    )
    decided = service.decide(
        created.request.id,
        decider_user_id=decider.id,
        decision="approved",
        sensitive_elevation_verified=sensitive,
    )
    return decided


def test_read_only_nonhuman_executes_without_approval_consumption(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="governed.analyst@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="governed.read",
    )
    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": -999,
        },
        autonomy_entrypoint=(
            "isolated_skill_handlers:double"
        ),
    )

    try:
        result = governed.execute(
            version.id,
            actor=_actor(),
            authority_user_id=authority.id,
            input_payload={"value": 4},
            idempotency_key="autonomous-read-1",
        )
    finally:
        executor.shutdown()

    assert result.policy.disposition == "autonomous_allowed"
    assert result.approval_request_id is None
    assert result.approval_consumption_id is None
    assert result.invocation.output == {"result": 8}
    assert (
        result.invocation.invocation.actor_type
        == "agent"
    )
    assert (
        db_session.query(
            ApprovalConsumption
        ).count()
        == 0
    )


def test_user_actor_is_rejected_before_runtime(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="governed.user-path@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="governed.user-path",
    )
    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"],
        },
    )

    try:
        with pytest.raises(
            ApprovalValidationError
        ):
            governed.execute(
                version.id,
                actor=SkillInvocationActor(
                    actor_type="user",
                    actor_reference="user:1",
                    actor_user_id=1,
                ),
                authority_user_id=authority.id,
                input_payload={"value": 1},
                idempotency_key="user-path-1",
            )
    finally:
        executor.shutdown()

    assert (
        db_session.query(
            SkillInvocation
        ).count()
        == 0
    )


def test_mutating_requires_approval_before_runtime(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="governed.manager@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="governed.mutating-required",
        execution_mode="mutating",
        capabilities=(
            CapabilityInput(
                capability_key="records.update",
                access_mode="write",
                resource_scope="internal",
            ),
        ),
    )
    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"],
        },
    )

    try:
        with pytest.raises(
            ApprovalRequiredError
        ):
            governed.execute(
                version.id,
                actor=_actor(),
                authority_user_id=authority.id,
                input_payload={"value": 1},
            )
    finally:
        executor.shutdown()

    assert (
        db_session.query(
            SkillInvocation
        ).count()
        == 0
    )


def test_approved_mutating_executes_once_and_replays(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="governed.authority.manager@example.com",
        role="manager",
    )
    decider = _user(
        db_session,
        email="governed.decider.manager@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="governed.mutating-replay",
        execution_mode="mutating",
        capabilities=(
            CapabilityInput(
                capability_key="records.update",
                access_mode="write",
                resource_scope="internal",
            ),
        ),
    )
    actor = _actor()
    approved = _approve(
        db_session,
        version=version,
        actor=actor,
        input_payload={"value": 3},
        key="governed-approval-1",
        decider=decider,
    )
    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": -999,
        },
        autonomy_entrypoint=(
            "isolated_skill_handlers:increment"
        ),
    )

    try:
        first = governed.execute(
            version.id,
            actor=actor,
            authority_user_id=authority.id,
            input_payload={"value": 3},
            approval_request_id=approved.request.id,
        )
        second = governed.execute(
            version.id,
            actor=actor,
            authority_user_id=authority.id,
            input_payload={"value": 3},
            approval_request_id=approved.request.id,
        )
    finally:
        executor.shutdown()

    assert first.invocation.output == {"result": 4}
    assert (
        first.invocation.invocation.id
        == second.invocation.invocation.id
    )
    assert second.invocation.duplicate is True

    consumption = db_session.query(
        ApprovalConsumption
    ).one()
    assert consumption.status == "consumed"
    assert (
        consumption.skill_invocation_id
        == first.invocation.invocation.id
    )
    assert (
        consumption.runtime_idempotency_key
        == f"approval:{approved.request.id}"
    )


def test_approved_action_rejects_input_or_actor_mismatch(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="governed.mismatch.authority@example.com",
        role="manager",
    )
    decider = _user(
        db_session,
        email="governed.mismatch.decider@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="governed.mismatch",
        execution_mode="mutating",
        capabilities=(
            CapabilityInput(
                capability_key="records.update",
                access_mode="write",
                resource_scope="internal",
            ),
        ),
    )
    actor = _actor()
    approved = _approve(
        db_session,
        version=version,
        actor=actor,
        input_payload={"value": 1},
        key="governed-mismatch-1",
        decider=decider,
    )
    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"],
        },
    )

    try:
        with pytest.raises(
            ApprovalConsumptionConflictError
        ):
            governed.execute(
                version.id,
                actor=actor,
                authority_user_id=authority.id,
                input_payload={"value": 2},
                approval_request_id=approved.request.id,
            )

        with pytest.raises(
            ApprovalConsumptionConflictError
        ):
            governed.execute(
                version.id,
                actor=_actor("system:planner"),
                authority_user_id=authority.id,
                input_payload={"value": 1},
                approval_request_id=approved.request.id,
            )
    finally:
        executor.shutdown()

    assert (
        db_session.query(
            SkillInvocation
        ).count()
        == 0
    )


def test_current_authority_is_rechecked_after_human_approval(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="governed.revoked.authority@example.com",
        role="manager",
    )
    decider = _user(
        db_session,
        email="governed.revoked.decider@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="governed.revoked",
        execution_mode="mutating",
        capabilities=(
            CapabilityInput(
                capability_key="records.update",
                access_mode="write",
                resource_scope="internal",
            ),
        ),
    )
    actor = _actor()
    approved = _approve(
        db_session,
        version=version,
        actor=actor,
        input_payload={"value": 1},
        key="governed-revoked-1",
        decider=decider,
    )

    authority.active = False
    db_session.commit()

    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"],
        },
    )

    try:
        with pytest.raises(
            ApprovalAuthorizationError
        ):
            governed.execute(
                version.id,
                actor=actor,
                authority_user_id=authority.id,
                input_payload={"value": 1},
                approval_request_id=approved.request.id,
            )
    finally:
        executor.shutdown()

    assert (
        db_session.query(
            SkillInvocation
        ).count()
        == 0
    )


def test_approved_request_expires_before_execution(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="governed.expiry.authority@example.com",
        role="manager",
    )
    decider = _user(
        db_session,
        email="governed.expiry.decider@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="governed.expiry",
        execution_mode="mutating",
        capabilities=(
            CapabilityInput(
                capability_key="records.update",
                access_mode="write",
                resource_scope="internal",
            ),
        ),
    )
    actor = _actor()
    approved = _approve(
        db_session,
        version=version,
        actor=actor,
        input_payload={"value": 1},
        key="governed-expiry-1",
        decider=decider,
    )
    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"],
        },
    )

    try:
        with pytest.raises(
            ApprovalStateError
        ):
            governed.execute(
                version.id,
                actor=actor,
                authority_user_id=authority.id,
                input_payload={"value": 1},
                approval_request_id=approved.request.id,
                now=(
                    approved.request.expires_at
                    + timedelta(seconds=1)
                ),
            )
    finally:
        executor.shutdown()

    assert (
        db_session.query(
            SkillInvocation
        ).count()
        == 0
    )


def test_critical_requires_sensitive_evidence_and_external_authority(
    db_session: Session,
) -> None:
    executive_authority = _user(
        db_session,
        email="governed.external.executive@example.com",
        role="executive",
    )
    admin_authority = _user(
        db_session,
        email="governed.external.admin@example.com",
        role="administrator",
    )
    decider = _user(
        db_session,
        email="governed.external.decider@example.com",
        role="executive",
    )
    version = _published_version(
        db_session,
        skill_key="governed.external",
        execution_mode="external",
        capabilities=(
            CapabilityInput(
                capability_key="crm.sync",
                access_mode="execute",
                resource_scope="external",
            ),
        ),
    )
    actor = _actor("integration:crm")
    approved = _approve(
        db_session,
        version=version,
        actor=actor,
        input_payload={"value": 1},
        key="governed-external-1",
        decider=decider,
        sensitive=True,
    )

    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"],
        },
    )

    try:
        with pytest.raises(
            SkillAuthorizationError
        ):
            governed.execute(
                version.id,
                actor=actor,
                authority_user_id=executive_authority.id,
                input_payload={"value": 1},
                approval_request_id=approved.request.id,
            )

        result = governed.execute(
            version.id,
            actor=actor,
            authority_user_id=admin_authority.id,
            input_payload={"value": 1},
            approval_request_id=approved.request.id,
        )
    finally:
        executor.shutdown()

    assert result.invocation.invocation.status == "succeeded"
    consumption = db_session.query(
        ApprovalConsumption
    ).one()
    assert consumption.authority_user_id == admin_authority.id


def test_runtime_failure_spends_approval_and_replay_does_not_run_twice(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="governed.fail.authority@example.com",
        role="manager",
    )
    decider = _user(
        db_session,
        email="governed.fail.decider@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="governed.failure",
        execution_mode="mutating",
        capabilities=(
            CapabilityInput(
                capability_key="records.update",
                access_mode="write",
                resource_scope="internal",
            ),
        ),
    )
    actor = _actor()
    approved = _approve(
        db_session,
        version=version,
        actor=actor,
        input_payload={"value": 1},
        key="governed-failure-1",
        decider=decider,
    )
    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": -999,
        },
        autonomy_entrypoint=(
            "isolated_skill_handlers:fail"
        ),
    )

    try:
        for _ in range(2):
            with pytest.raises(
                SkillExecutionError
            ):
                governed.execute(
                    version.id,
                    actor=actor,
                    authority_user_id=authority.id,
                    input_payload={"value": 1},
                    approval_request_id=approved.request.id,
                )
    finally:
        executor.shutdown()

    consumption = db_session.query(
        ApprovalConsumption
    ).one()
    invocation = db_session.query(
        SkillInvocation
    ).one()
    assert consumption.status == "consumed"
    assert consumption.skill_invocation_id == invocation.id
    assert invocation.status == "failed"

def test_untrusted_allowlisted_handler_is_blocked_before_runtime(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="governed.untrusted.authority@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="governed.untrusted",
    )
    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"],
        },
        trusted_for_autonomy=False,
    )

    try:
        with pytest.raises(
            ApprovalAuthorizationError
        ):
            governed.execute(
                version.id,
                actor=_actor(),
                authority_user_id=authority.id,
                input_payload={"value": 1},
                idempotency_key="untrusted-1",
            )
    finally:
        executor.shutdown()

    assert db_session.query(
        SkillInvocation
    ).count() == 0


def test_plugin_runtime_is_blocked_from_autonomy_even_when_trusted(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="governed.plugin.authority@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="governed.plugin",
        runtime_kind="plugin",
    )
    governed, executor = _runtime(
        db_session,
        version=version,
        handler=lambda payload: {
            "result": payload["value"],
        },
        trusted_for_autonomy=True,
    )

    try:
        with pytest.raises(
            ApprovalAuthorizationError
        ):
            governed.execute(
                version.id,
                actor=_actor(),
                authority_user_id=authority.id,
                input_payload={"value": 1},
                idempotency_key="plugin-1",
            )
    finally:
        executor.shutdown()

    assert db_session.query(
        SkillInvocation
    ).count() == 0


def test_handler_registry_autonomy_trust_is_explicit_and_fail_closed(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="governed.registry-trust",
    )
    registry = SkillHandlerRegistry()
    handler = lambda payload: {
        "result": payload["value"],
    }

    default_registration = registry.register(
        runtime_kind=version.runtime_kind,
        handler_reference=version.handler_reference,
        handler=handler,
    )
    assert default_registration.trusted_for_autonomy is False

    with pytest.raises(SkillConflictError):
        registry.register(
            runtime_kind=version.runtime_kind,
            handler_reference=version.handler_reference,
            handler=handler,
            trusted_for_autonomy=True,
            autonomy_entrypoint=(
                "isolated_skill_handlers:identity"
            ),
        )


def test_internal_autonomy_trust_requires_isolated_entrypoint(
    db_session: Session,
) -> None:
    version = _published_version(
        db_session,
        skill_key="governed.registry-isolation",
    )
    registry = SkillHandlerRegistry()

    with pytest.raises(
        SkillValidationError,
        match="autonomy_entrypoint",
    ):
        registry.register(
            runtime_kind=version.runtime_kind,
            handler_reference=version.handler_reference,
            handler=lambda payload: payload,
            trusted_for_autonomy=True,
        )
