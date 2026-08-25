from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy.orm import Session

from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkNotFoundError
from app.models.user import User
from app.models.work_learning_runtime_context_snapshot import (
    WorkLearningRuntimeContextSnapshot,
)
from app.services.skill_service import SkillService
from app.services.work_learning_context import WorkLearningContextItem
from app.services.work_learning_runtime_context_snapshot import (
    PRODUCTION_WORK_LEARNING_CONTEXT_LIMIT,
)
from app.services.work_learning_runtime_context_snapshot import (
    WorkLearningRuntimeContextSnapshotService,
)
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService
from app.services.work_skill_execution import WorkSkillExecutionService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="system:test:25d-snapshot-service",
)


class RecordingLearningService:
    def __init__(self, items):
        self.items = tuple(items)
        self.calls = []

    def resolve(self, work_item_id, **kwargs):
        self.calls.append(
            {
                "work_item_id": work_item_id,
                **kwargs,
            }
        )
        return self.items


def _configured_execution(
    db_session: Session,
    *,
    suffix: str,
):
    authority = User(
        name="Snapshot Service Developer",
        email=f"snapshot.service.{suffix}@example.com",
        password_hash="not-used",
        role="developer",
        active=True,
    )
    db_session.add(authority)
    db_session.commit()
    db_session.refresh(authority)

    skills = SkillService(db_session)
    skill = skills.register_skill(
        skill_key=f"snapshot.service.{suffix}",
        provider="auneron.core",
        display_name="Snapshot Service",
        description="Snapshot service test skill.",
    )
    draft = skills.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.snapshot_service:"
            + suffix.replace("-", "_")
        ),
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

    manager = WorkManagerService(db_session)
    work = manager.create(
        work_type="task",
        title="Snapshot Service",
        work_key=f"snapshot.service.work.{suffix}",
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:25d-snapshot-service",
        actor=SYSTEM_ACTOR,
    ).work_item
    work = manager.transition_status(
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


def _item(version_id: int) -> WorkLearningContextItem:
    return WorkLearningContextItem(
        memory_id=101,
        source_work_item_id=102,
        work_skill_execution_id=103,
        skill_version_id=version_id,
        terminal_status="succeeded",
        evaluation_code="execution_succeeded",
        learning_signal="positive",
        observed_at=(
            datetime.now(timezone.utc)
            - timedelta(minutes=5)
        ),
    )


def test_service_persists_once_and_reuses_exact_snapshot_without_reresolving(
    db_session: Session,
) -> None:
    authority, version, work, execution = _configured_execution(
        db_session,
        suffix="reuse",
    )
    learning = RecordingLearningService([
        _item(version.id),
    ])
    service = WorkLearningRuntimeContextSnapshotService(
        db_session,
        learning_context_service=learning,
    )

    first = service.get_or_create(
        work_skill_execution_id=execution.id,
        work_item_id=work.id,
        skill_version_id=version.id,
        authority_user_id=authority.id,
    )
    second = service.get_or_create(
        work_skill_execution_id=execution.id,
        work_item_id=work.id,
        skill_version_id=version.id,
        authority_user_id=authority.id,
    )

    assert first == second
    assert first.protocol == "work_learning_v1"
    assert len(first.payload["items"]) == 1
    assert len(learning.calls) == 1
    assert learning.calls[0]["limit"] == (
        PRODUCTION_WORK_LEARNING_CONTEXT_LIMIT
    )
    assert learning.calls[0]["skill_version_id"] == version.id
    assert learning.calls[0]["authority_user_id"] == authority.id
    assert learning.calls[0]["as_of"].tzinfo is not None

    snapshot = db_session.query(
        WorkLearningRuntimeContextSnapshot
    ).one()
    assert snapshot.work_skill_execution_id == execution.id
    assert snapshot.work_item_id == work.id
    assert snapshot.skill_version_id == version.id
    assert snapshot.context_digest == first.digest
    assert snapshot.item_count == 1
    assert snapshot.context_bytes == len(first.canonical_bytes)
    assert snapshot.resolved_as_of.tzinfo is not None


def test_empty_authorized_context_is_persisted_as_contextful_snapshot(
    db_session: Session,
) -> None:
    authority, version, work, execution = _configured_execution(
        db_session,
        suffix="empty",
    )
    learning = RecordingLearningService([])
    context = WorkLearningRuntimeContextSnapshotService(
        db_session,
        learning_context_service=learning,
    ).get_or_create(
        work_skill_execution_id=execution.id,
        work_item_id=work.id,
        skill_version_id=version.id,
        authority_user_id=authority.id,
    )

    assert context.payload == {
        "protocol": "work_learning_v1",
        "items": [],
    }
    snapshot = db_session.query(
        WorkLearningRuntimeContextSnapshot
    ).one()
    assert snapshot.item_count == 0


def test_existing_snapshot_reauthorizes_current_work_and_memory_reads(
    db_session: Session,
) -> None:
    authority, version, work, execution = _configured_execution(
        db_session,
        suffix="reauthorize",
    )
    learning = RecordingLearningService([])
    service = WorkLearningRuntimeContextSnapshotService(
        db_session,
        learning_context_service=learning,
    )
    service.get_or_create(
        work_skill_execution_id=execution.id,
        work_item_id=work.id,
        skill_version_id=version.id,
        authority_user_id=authority.id,
    )

    with Session(bind=db_session.get_bind()) as external:
        persisted = external.get(User, authority.id)
        assert persisted is not None
        persisted.role = "manager"
        external.commit()

    with pytest.raises(WorkNotFoundError):
        service.get_or_create(
            work_skill_execution_id=execution.id,
            work_item_id=work.id,
            skill_version_id=version.id,
            authority_user_id=authority.id,
        )

    assert len(learning.calls) == 1


def test_snapshot_digest_tamper_fails_closed_without_reresolving(
    db_session: Session,
) -> None:
    authority, version, work, execution = _configured_execution(
        db_session,
        suffix="tamper",
    )
    learning = RecordingLearningService([])
    service = WorkLearningRuntimeContextSnapshotService(
        db_session,
        learning_context_service=learning,
    )
    service.get_or_create(
        work_skill_execution_id=execution.id,
        work_item_id=work.id,
        skill_version_id=version.id,
        authority_user_id=authority.id,
    )

    snapshot = db_session.query(
        WorkLearningRuntimeContextSnapshot
    ).one()
    snapshot.context_digest = "a" * 64
    db_session.commit()

    with pytest.raises(WorkConflictError):
        service.get_or_create(
            work_skill_execution_id=execution.id,
            work_item_id=work.id,
            skill_version_id=version.id,
            authority_user_id=authority.id,
        )

    assert len(learning.calls) == 1
