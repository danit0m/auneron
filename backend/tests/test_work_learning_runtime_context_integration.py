from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.skill import SkillInvocation
from app.models.user import User
from app.models.work_learning_runtime_context_snapshot import (
    WorkLearningRuntimeContextSnapshot,
)
from app.services.governed_skill_execution import (
    GovernedSkillExecutionService,
)
from app.services.isolated_skill_executor import IsolatedSkillExecutor
from app.services.skill_runtime import SkillHandlerRegistry
from app.services.skill_runtime import SkillRuntimeService
from app.services.skill_service import SkillService
from app.services.work_learning_context import WorkLearningContextItem
from app.services.work_learning_runtime_context_snapshot import (
    WorkLearningRuntimeContextSnapshotService,
)
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService
from app.services.work_skill_execution import WorkSkillExecutionService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="system:test:25d-runtime-context-integration",
)


class FixedLearningService:
    def __init__(self):
        self.calls = []

    def resolve(
        self,
        work_item_id,
        *,
        skill_version_id,
        authority_user_id,
        limit,
        as_of,
    ):
        self.calls.append({
            "work_item_id": work_item_id,
            "skill_version_id": skill_version_id,
            "authority_user_id": authority_user_id,
            "limit": limit,
            "as_of": as_of,
        })
        return (
            WorkLearningContextItem(
                memory_id=701,
                source_work_item_id=702,
                work_skill_execution_id=703,
                skill_version_id=skill_version_id,
                terminal_status="succeeded",
                evaluation_code="execution_succeeded",
                learning_signal="positive",
                observed_at=(
                    datetime.now(timezone.utc)
                    - timedelta(minutes=10)
                ),
            ),
        )


def _authority(db_session: Session) -> User:
    user = User(
        name="Runtime Context Integration Developer",
        email="runtime.context.integration@example.com",
        password_hash="not-used",
        role="developer",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _version(db_session: Session):
    skills = SkillService(db_session)
    skill = skills.register_skill(
        skill_key="runtime.context.work.integration",
        provider="auneron.core",
        display_name="Runtime Context Work Integration",
        description="25D production Work runtime-context integration test.",
    )
    draft = skills.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference="app.skills.work_runtime_context:run",
        execution_mode="read_only",
        manifest={
            "runtime_context_protocol": "work_learning_v1",
        },
        input_schema={
            "type": "object",
            "properties": {
                "value": {"type": "integer"},
            },
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "result": {"type": "integer"},
                "context_protocol": {"type": "string"},
                "learning_signal": {"type": "string"},
                "context_items": {"type": "integer"},
            },
            "required": [
                "result",
                "context_protocol",
                "learning_signal",
                "context_items",
            ],
            "additionalProperties": False,
        },
    )
    return skills.publish_version(draft.id).version


def _work(db_session: Session):
    manager = WorkManagerService(db_session)
    item = manager.create(
        work_type="task",
        title="Runtime Context Work Integration",
        work_key="runtime.context.work.integration",
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:25d-runtime-context-integration",
        actor=SYSTEM_ACTOR,
    ).work_item
    return manager.transition_status(
        item.id,
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
        status="ready",
    ).work_item


def test_work_dispatch_uses_durable_side_band_learning_context_without_input_mutation(
    db_session: Session,
) -> None:
    authority = _authority(db_session)
    version = _version(db_session)
    work = _work(db_session)

    registry = SkillHandlerRegistry()
    registry.register(
        runtime_kind=version.runtime_kind,
        handler_reference=version.handler_reference,
        handler=lambda payload: {
            "result": payload["value"],
        },
        trusted_for_autonomy=True,
        autonomy_entrypoint="isolated_skill_handlers:context_probe",
        runtime_context_protocol="work_learning_v1",
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
    learning = FixedLearningService()
    snapshots = WorkLearningRuntimeContextSnapshotService(
        db_session,
        learning_context_service=learning,
    )
    service = WorkSkillExecutionService(
        db_session,
        governed_service=governed,
        learning_context_snapshot_service=snapshots,
    )

    try:
        configured = service.configure(
            work.id,
            version_id=version.id,
            authority_user_id=authority.id,
            input_payload={"value": 4},
        )
        result = service.dispatch(
            work.id,
            input_payload={"value": 4},
        )
    finally:
        isolated.shutdown()

    assert result.outcome == "succeeded"
    assert result.work_item.status == "completed"
    assert len(learning.calls) == 1
    assert learning.calls[0]["limit"] == 5

    snapshot = db_session.query(
        WorkLearningRuntimeContextSnapshot
    ).one()
    assert (
        snapshot.work_skill_execution_id
        == configured.execution.id
    )
    assert snapshot.item_count == 1
    assert snapshot.protocol == "work_learning_v1"

    invocation = db_session.query(
        SkillInvocation
    ).one()
    assert (
        invocation.input_digest
        == configured.execution.input_digest
    )
    assert invocation.output_payload == {
        "value": {
            "result": 8,
            "context_protocol": "work_learning_v1",
            "learning_signal": "positive",
            "context_items": 1,
        }
    }
