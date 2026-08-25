from datetime import datetime
from datetime import timezone

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.work_learning_runtime_context_snapshot import (
    WorkLearningRuntimeContextSnapshot,
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
    actor_reference="system:test:25d-snapshot-db",
)


def _execution(
    db_session: Session,
    *,
    suffix: str,
):
    authority = User(
        name="Snapshot DB Developer",
        email=f"snapshot.db.{suffix}@example.com",
        password_hash="not-used",
        role="developer",
        active=True,
    )
    db_session.add(authority)
    db_session.commit()
    db_session.refresh(authority)

    skills = SkillService(db_session)
    skill = skills.register_skill(
        skill_key=f"snapshot.db.{suffix}",
        provider="auneron.core",
        display_name="Snapshot DB",
        description="Snapshot database constraint test.",
    )
    draft = skills.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.snapshot_db:"
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
        title="Snapshot DB",
        work_key=f"snapshot.db.work.{suffix}",
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:25d-snapshot-db",
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
    return version, work, execution


def _snapshot(version, work, execution, **overrides):
    normalized = normalize_work_learning_runtime_context(
        {
            "protocol": "work_learning_v1",
            "items": [],
        },
        expected_skill_version_id=version.id,
    )
    values = {
        "work_skill_execution_id": execution.id,
        "work_item_id": work.id,
        "skill_version_id": version.id,
        "protocol": normalized.protocol,
        "context_payload": normalized.payload,
        "context_digest": normalized.digest,
        "item_count": 0,
        "context_bytes": len(normalized.canonical_bytes),
        "resolved_as_of": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return WorkLearningRuntimeContextSnapshot(**values)


def test_database_enforces_one_snapshot_per_work_skill_execution(
    db_session: Session,
) -> None:
    version, work, execution = _execution(
        db_session,
        suffix="unique",
    )
    db_session.add(_snapshot(version, work, execution))
    db_session.commit()

    db_session.add(_snapshot(version, work, execution))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        {"protocol": "unknown_v1"},
        {"context_digest": "A" * 64},
        {"context_digest": "g" * 64},
        {"item_count": -1},
        {"item_count": 11},
        {"context_bytes": 0},
        {"context_bytes": 16385},
    ],
)
def test_database_rejects_invalid_snapshot_contract(
    db_session: Session,
    overrides,
) -> None:
    version, work, execution = _execution(
        db_session,
        suffix=(
            "invalid-"
            + str(abs(hash(repr(overrides))))[:8]
        ),
    )
    db_session.add(
        _snapshot(
            version,
            work,
            execution,
            **overrides,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_database_restricts_deleting_linked_execution(
    db_session: Session,
) -> None:
    version, work, execution = _execution(
        db_session,
        suffix="restrict",
    )
    db_session.add(_snapshot(version, work, execution))
    db_session.commit()

    db_session.delete(execution)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_snapshot_table_constraint_names_are_stable(
    db_session: Session,
) -> None:
    inspector = inspect(db_session.get_bind())
    checks = {
        item["name"]
        for item in inspector.get_check_constraints(
            "work_learning_runtime_context_snapshots"
        )
    }
    assert {
        "ck_work_learning_runtime_context_protocol",
        "ck_work_learning_runtime_context_digest",
        "ck_work_learning_runtime_context_item_count",
        "ck_work_learning_runtime_context_bytes",
    }.issubset(checks)

    unique = {
        item["name"]
        for item in inspector.get_unique_constraints(
            "work_learning_runtime_context_snapshots"
        )
    }
    assert "uq_work_learning_runtime_context_execution" in unique
