from datetime import datetime
from datetime import timezone
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.skill_errors import SkillExecutionTimeoutError
from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkStateError
from app.models.approval import ApprovalConsumption
from app.models.skill import SkillInvocation
from app.models.user import User
from app.models.work_skill_execution import WorkSkillExecution
from app.services.approval_service import ApprovalService
from app.services.governed_skill_execution import (
    GovernedSkillExecutionService,
)
from app.services.isolated_skill_executor import IsolatedSkillExecutor
from app.services.skill_runtime import SkillHandlerRegistry
from app.services.skill_runtime import SkillRuntimeService
from app.services.skill_service import CapabilityInput
from app.services.skill_service import SkillService
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService
from app.services.work_skill_execution import WorkSkillExecutionService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="system:test:24e2",
)


def _user(
    db_session: Session,
    *,
    email: str,
    role: str,
) -> User:
    user = User(
        name="Work Skill 24E.2",
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
    execution_mode: str = "read_only",
    manifest: dict | None = None,
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
        display_name="Work Skill 24E.2",
        description="Skill para integração governada com Work.",
    )
    draft = service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.work:"
            + skill_key.replace(
                ".",
                "_",
            ).replace(
                "-",
                "_",
            )
        ),
        execution_mode=execution_mode,
        manifest=manifest,
        input_schema={
            "type": "object",
            "properties": {
                "value": {
                    "type": "integer",
                },
                "account_id": {
                    "type": "integer",
                },
                "subject_user_id": {
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


def _work(
    db_session: Session,
    *,
    key: str,
):
    service = WorkManagerService(
        db_session
    )
    item = service.create(
        work_type="task",
        title="Work governado 24E.2",
        work_key=key,
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:24e2",
        actor=SYSTEM_ACTOR,
    ).work_item
    item = service.transition_status(
        item.id,
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
        status="ready",
    ).work_item
    return item


def _governed(
    db_session: Session,
    *,
    version,
):
    registry = SkillHandlerRegistry()
    registry.register(
        runtime_kind=version.runtime_kind,
        handler_reference=version.handler_reference,
        handler=lambda payload: {
            "result": -999,
        },
        trusted_for_autonomy=True,
        autonomy_entrypoint=(
            "isolated_skill_handlers:double"
        ),
    )
    isolated = IsolatedSkillExecutor(
        max_workers=1,
        python_path_entries=(
            str(Path(__file__).resolve().parent),
        ),
    )
    runtime = SkillRuntimeService(
        db_session,
        handler_registry=registry,
        isolated_executor=isolated,
    )
    governed = GovernedSkillExecutionService(
        db_session,
        runtime=runtime,
    )
    return governed, isolated


def test_read_only_work_dispatch_completes_once(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.read@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="work.read.dispatch",
    )
    item = _work(
        db_session,
        key="work.read.dispatch",
    )
    governed, isolated = _governed(
        db_session,
        version=version,
    )
    service = WorkSkillExecutionService(
        db_session,
        governed_service=governed,
    )

    try:
        configured = service.configure(
            item.id,
            version_id=version.id,
            authority_user_id=authority.id,
            input_payload={"value": 4},
        )
        result = service.dispatch(
            item.id,
            input_payload={"value": 4},
        )
        replay = service.dispatch(
            item.id,
            input_payload={"value": 4},
        )
    finally:
        isolated.shutdown()

    assert configured.outcome == "ready"
    assert result.outcome == "succeeded"
    assert result.work_item.status == "completed"
    assert result.execution.skill_invocation_id is not None
    assert result.execution.approval_request_id is None
    assert replay.outcome == "succeeded"
    assert replay.duplicate is True
    assert (
        db_session.query(
            SkillInvocation
        ).count()
        == 1
    )


def test_configuration_is_idempotent_and_input_bound(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.config@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="work.config.idempotent",
    )
    item = _work(
        db_session,
        key="work.config.idempotent",
    )
    service = WorkSkillExecutionService(
        db_session
    )

    first = service.configure(
        item.id,
        version_id=version.id,
        authority_user_id=authority.id,
        input_payload={"value": 1},
    )
    duplicate = service.configure(
        item.id,
        version_id=version.id,
        authority_user_id=authority.id,
        input_payload={"value": 1},
    )

    assert duplicate.duplicate is True
    assert duplicate.execution.id == first.execution.id

    with pytest.raises(
        WorkConflictError,
        match="outra ação Skill",
    ):
        service.configure(
            item.id,
            version_id=version.id,
            authority_user_id=authority.id,
            input_payload={"value": 2},
        )


def test_work_scope_must_match_skill_scope(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.scope@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="work.scope.user",
        capabilities=(
            CapabilityInput(
                capability_key="users.read",
                access_mode="read",
                resource_scope="user",
            ),
        ),
    )
    item = _work(
        db_session,
        key="work.scope.global",
    )
    service = WorkSkillExecutionService(
        db_session
    )

    with pytest.raises(
        WorkStateError,
        match="Escopo",
    ):
        service.configure(
            item.id,
            version_id=version.id,
            authority_user_id=authority.id,
            input_payload={
                "value": 1,
                "subject_user_id": authority.id,
            },
        )

    assert (
        db_session.query(
            WorkSkillExecution
        ).count()
        == 0
    )


def test_external_work_execution_is_blocked_before_ledger(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.external@example.com",
        role="administrator",
    )
    version = _published_version(
        db_session,
        skill_key="work.external.blocked",
        execution_mode="external",
    )
    item = _work(
        db_session,
        key="work.external.blocked",
    )
    service = WorkSkillExecutionService(
        db_session
    )

    with pytest.raises(
        WorkStateError,
        match="external",
    ):
        service.configure(
            item.id,
            version_id=version.id,
            authority_user_id=authority.id,
            input_payload={"value": 1},
        )

    assert (
        db_session.query(
            WorkSkillExecution
        ).count()
        == 0
    )


def test_mutating_work_creates_exact_approval_and_waits(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.mutating.pending@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="work.mutating.pending",
        execution_mode="mutating",
        capabilities=(
            CapabilityInput(
                capability_key="internal.update",
                access_mode="write",
                resource_scope="internal",
            ),
        ),
    )
    item = _work(
        db_session,
        key="work.mutating.pending",
    )
    service = WorkSkillExecutionService(
        db_session
    )

    configured = service.configure(
        item.id,
        version_id=version.id,
        authority_user_id=authority.id,
        input_payload={"value": 3},
    )
    pending = service.dispatch(
        item.id,
        input_payload={"value": 3},
    )

    assert configured.outcome == "approval_pending"
    assert configured.execution.approval_request_id is not None
    assert pending.outcome == "approval_pending"
    assert pending.work_item.status == "ready"
    assert (
        db_session.query(
            SkillInvocation
        ).count()
        == 0
    )


def test_approved_mutating_work_consumes_approval_and_completes(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.mutating.authority@example.com",
        role="manager",
    )
    decider = _user(
        db_session,
        email="work.mutating.decider@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="work.mutating.approved",
        execution_mode="mutating",
        capabilities=(
            CapabilityInput(
                capability_key="internal.update",
                access_mode="write",
                resource_scope="internal",
            ),
        ),
    )
    item = _work(
        db_session,
        key="work.mutating.approved",
    )
    governed, isolated = _governed(
        db_session,
        version=version,
    )
    service = WorkSkillExecutionService(
        db_session,
        governed_service=governed,
    )

    configured = service.configure(
        item.id,
        version_id=version.id,
        authority_user_id=authority.id,
        input_payload={"value": 5},
    )
    assert configured.execution.approval_request_id is not None

    ApprovalService(
        db_session
    ).decide(
        configured.execution.approval_request_id,
        decider_user_id=decider.id,
        decision="approved",
    )

    try:
        result = service.dispatch(
            item.id,
            input_payload={"value": 5},
        )
    finally:
        isolated.shutdown()

    assert result.outcome == "succeeded"
    assert result.work_item.status == "completed"
    assert result.execution.approval_consumption_id is not None
    assert result.execution.skill_invocation_id is not None
    consumption = db_session.get(
        ApprovalConsumption,
        result.execution.approval_consumption_id,
    )
    assert consumption is not None
    assert consumption.status == "consumed"


def test_current_authority_is_rechecked_before_dispatch(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.authority.revoked@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="work.authority.revoked",
    )
    item = _work(
        db_session,
        key="work.authority.revoked",
    )
    service = WorkSkillExecutionService(
        db_session
    )
    service.configure(
        item.id,
        version_id=version.id,
        authority_user_id=authority.id,
        input_payload={"value": 1},
    )

    authority.active = False
    db_session.commit()

    with pytest.raises(
        WorkStateError,
        match="inativo",
    ):
        service.dispatch(
            item.id,
            input_payload={"value": 1},
        )

    db_session.refresh(item)
    assert item.status == "ready"


def test_unsatisfied_dependency_blocks_before_runtime(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.dependency@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="work.dependency.block",
    )
    work_service = WorkManagerService(
        db_session
    )
    predecessor = work_service.create(
        work_type="task",
        title="Predecessor",
        work_key="work.dependency.pred",
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:24e2",
        actor=SYSTEM_ACTOR,
    ).work_item
    item = work_service.create(
        work_type="task",
        title="Dependente",
        work_key="work.dependency.item",
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:24e2",
        actor=SYSTEM_ACTOR,
    ).work_item
    item = work_service.add_dependency(
        item.id,
        depends_on_work_item_id=predecessor.id,
        dependency_type="finish_to_start",
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
    ).work_item
    item = work_service.transition_status(
        item.id,
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
        status="ready",
    ).work_item

    service = WorkSkillExecutionService(
        db_session
    )
    service.configure(
        item.id,
        version_id=version.id,
        authority_user_id=authority.id,
        input_payload={"value": 1},
    )

    with pytest.raises(
        WorkStateError,
        match="não satisfeitas",
    ):
        service.dispatch(
            item.id,
            input_payload={"value": 1},
        )

    assert (
        db_session.query(
            SkillInvocation
        ).count()
        == 0
    )


class _TimeoutGoverned:
    def __init__(
        self,
        db_session: Session,
    ) -> None:
        self.db = db_session

    def execute(
        self,
        version_id: int,
        *,
        actor,
        authority_user_id: int,
        input_payload,
        idempotency_key: str | None = None,
        approval_request_id: int | None = None,
        now=None,
    ):
        started = datetime.now(
            timezone.utc
        )
        runtime_key = (
            idempotency_key
            if idempotency_key is not None
            else f"approval:{approval_request_id}"
        )
        invocation = SkillInvocation(
            skill_version_id=version_id,
            actor_type=actor.actor_type,
            actor_reference=actor.actor_reference,
            actor_user_id=None,
            idempotency_key=runtime_key,
            request_fingerprint=sha256(
                b"work-timeout"
            ).hexdigest(),
            input_digest=sha256(
                b"work-timeout-input"
            ).hexdigest(),
            status="timed_out",
            output_payload=None,
            output_digest=None,
            output_bytes=None,
            error_code="execution_timeout",
            duration_ms=1,
            started_at=started,
            finished_at=started,
        )
        self.db.add(invocation)
        self.db.commit()
        raise SkillExecutionTimeoutError(
            "timeout"
        )


def test_timeout_links_invocation_and_blocks_work(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.timeout@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="work.timeout",
    )
    item = _work(
        db_session,
        key="work.timeout",
    )
    service = WorkSkillExecutionService(
        db_session,
        governed_service=_TimeoutGoverned(
            db_session
        ),
    )
    service.configure(
        item.id,
        version_id=version.id,
        authority_user_id=authority.id,
        input_payload={"value": 1},
    )

    result = service.dispatch(
        item.id,
        input_payload={"value": 1},
    )

    assert result.outcome == "timed_out"
    assert result.work_item.status == "blocked"
    assert result.execution.skill_invocation_id is not None
    assert result.execution.last_error_code == "skill_timed_out"


def test_cancelled_work_cannot_dispatch(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.cancelled@example.com",
        role="analyst",
    )
    version = _published_version(
        db_session,
        skill_key="work.cancelled",
    )
    item = _work(
        db_session,
        key="work.cancelled",
    )
    service = WorkSkillExecutionService(
        db_session
    )
    service.configure(
        item.id,
        version_id=version.id,
        authority_user_id=authority.id,
        input_payload={"value": 1},
    )
    item = WorkManagerService(
        db_session
    ).transition_status(
        item.id,
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
        status="cancelled",
        reason="Cancelado antes do dispatch",
    ).work_item

    with pytest.raises(
        WorkStateError,
        match="estado executável",
    ):
        service.dispatch(
            item.id,
            input_payload={"value": 1},
        )

    assert item.status == "cancelled"


class _FailingSnapshotService:
    def __init__(self):
        self.calls = []

    def get_or_create(self, **kwargs):
        self.calls.append(kwargs)
        raise WorkStateError(
            "snapshot resolution failed"
        )


def test_context_manifest_is_rejected_for_mutating_work_before_ledger(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.context.mutating@example.com",
        role="manager",
    )
    version = _published_version(
        db_session,
        skill_key="work.context.mutating",
        execution_mode="mutating",
        manifest={
            "runtime_context_protocol": "work_learning_v1",
        },
    )
    item = _work(
        db_session,
        key="work.context.mutating",
    )

    with pytest.raises(
        WorkStateError,
        match="internal_python read_only",
    ):
        WorkSkillExecutionService(
            db_session
        ).configure(
            item.id,
            version_id=version.id,
            authority_user_id=authority.id,
            input_payload={"value": 1},
        )

    assert (
        db_session.query(
            WorkSkillExecution
        ).count()
        == 0
    )


def test_unknown_context_protocol_is_rejected_before_ledger(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.context.unknown@example.com",
        role="developer",
    )
    version = _published_version(
        db_session,
        skill_key="work.context.unknown",
        manifest={
            "runtime_context_protocol": "unknown_v1",
        },
    )
    item = _work(
        db_session,
        key="work.context.unknown",
    )

    with pytest.raises(
        WorkStateError,
        match="não suportado",
    ):
        WorkSkillExecutionService(
            db_session
        ).configure(
            item.id,
            version_id=version.id,
            authority_user_id=authority.id,
            input_payload={"value": 1},
        )

    assert (
        db_session.query(
            WorkSkillExecution
        ).count()
        == 0
    )


def test_context_snapshot_failure_does_not_start_work_or_increment_attempt(
    db_session: Session,
) -> None:
    authority = _user(
        db_session,
        email="work.context.snapshot-failure@example.com",
        role="developer",
    )
    version = _published_version(
        db_session,
        skill_key="work.context.snapshot-failure",
        manifest={
            "runtime_context_protocol": "work_learning_v1",
        },
    )
    item = _work(
        db_session,
        key="work.context.snapshot-failure",
    )
    snapshots = _FailingSnapshotService()
    service = WorkSkillExecutionService(
        db_session,
        learning_context_snapshot_service=snapshots,
    )
    configured = service.configure(
        item.id,
        version_id=version.id,
        authority_user_id=authority.id,
        input_payload={"value": 1},
    )

    with pytest.raises(
        WorkStateError,
        match="snapshot resolution failed",
    ):
        service.dispatch(
            item.id,
            input_payload={"value": 1},
        )

    db_session.refresh(item)
    db_session.refresh(configured.execution)
    assert item.status == "ready"
    assert configured.execution.status == "ready"
    assert configured.execution.dispatch_attempts == 0
    assert configured.execution.started_at is None
    assert len(snapshots.calls) == 1
    assert (
        db_session.query(
            SkillInvocation
        ).count()
        == 0
    )
