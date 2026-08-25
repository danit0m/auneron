from datetime import datetime
from datetime import timezone

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.work_learning_runtime_context_snapshot import (
    WorkLearningRuntimeContextSnapshot,
)
from app.repositories.work_learning_runtime_context_snapshot_repository import (
    WorkLearningRuntimeContextSnapshotRepository,
)
from app.services.skill_runtime_context import (
    WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
)
from app.services.skill_runtime_context import (
    normalize_work_learning_runtime_context,
)
from app.services.skill_service import SkillService
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService
from app.services.work_skill_execution import WorkSkillExecutionService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="system:test:25d-snapshot-repository",
)


def _configured_execution(db_session: Session):
    authority = User(
        name="Snapshot Repository Developer",
        email="snapshot.repository@example.com",
        password_hash="not-used",
        role="developer",
        active=True,
    )
    db_session.add(authority)
    db_session.commit()
    db_session.refresh(authority)

    skills = SkillService(db_session)
    skill = skills.register_skill(
        skill_key="snapshot.repository.read",
        provider="auneron.core",
        display_name="Snapshot Repository",
        description="Snapshot repository test skill.",
    )
    draft = skills.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference="app.skills.snapshot_repository:read",
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
        output_schema={"type": "object"},
    )
    version = skills.publish_version(draft.id).version

    work = WorkManagerService(db_session).create(
        work_type="task",
        title="Snapshot Repository",
        work_key="snapshot.repository.target",
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:25d-snapshot-repository",
        actor=SYSTEM_ACTOR,
    ).work_item
    work = WorkManagerService(db_session).transition_status(
        work.id,
        expected_version=work.version,
        actor=SYSTEM_ACTOR,
        status="ready",
    ).work_item

    execution = WorkSkillExecutionService(db_session).configure(
        work.id,
        version_id=version.id,
        authority_user_id=authority.id,
        input_payload={"value": 1},
    ).execution
    return authority, version, work, execution


def test_repository_adds_and_reads_snapshot_without_owning_transaction(
    db_session: Session,
) -> None:
    _, version, work, execution = _configured_execution(db_session)
    normalized = normalize_work_learning_runtime_context(
        {
            "protocol": WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
            "items": [],
        },
        expected_skill_version_id=version.id,
    )
    snapshot = WorkLearningRuntimeContextSnapshot(
        work_skill_execution_id=execution.id,
        work_item_id=work.id,
        skill_version_id=version.id,
        protocol=normalized.protocol,
        context_payload=normalized.payload,
        context_digest=normalized.digest,
        item_count=0,
        context_bytes=len(normalized.canonical_bytes),
        resolved_as_of=datetime.now(timezone.utc),
    )
    repository = WorkLearningRuntimeContextSnapshotRepository(
        db_session
    )

    repository.add(snapshot)

    assert snapshot.id is not None
    assert repository.get_by_execution_id(execution.id) is snapshot

    db_session.rollback()

    assert repository.get_by_execution_id(execution.id) is None
