from datetime import datetime
from datetime import timezone

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.work_outcome_evaluation import WorkOutcomeEvaluation
from app.services.skill_service import SkillService
from app.services.work_outcome_evaluation import (
    deterministic_evaluation_digest,
)
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService
from app.services.work_skill_execution import WorkSkillExecutionService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="system:test:25a-db",
)


def _terminal_failure(
    db_session: Session,
    *,
    suffix: str,
):
    user = User(
        name="Outcome DB 25A",
        email=f"outcome.db.{suffix}@example.com",
        password_hash="not-used",
        role="analyst",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    skill_service = SkillService(db_session)
    skill = skill_service.register_skill(
        skill_key=f"outcome.db.{suffix}",
        provider="auneron.core",
        display_name="Outcome DB 25A",
        description="Outcome DB constraint test",
    )
    draft = skill_service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.outcome_db:"
            + suffix.replace("-", "_")
        ),
        execution_mode="read_only",
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
    version = skill_service.publish_version(
        draft.id
    ).version

    work_service = WorkManagerService(db_session)
    item = work_service.create(
        work_type="task",
        title="Outcome DB 25A",
        work_key=f"outcome.db.{suffix}",
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:25a-db",
        actor=SYSTEM_ACTOR,
    ).work_item
    item = work_service.transition_status(
        item.id,
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
        status="ready",
    ).work_item
    execution = WorkSkillExecutionService(
        db_session
    ).configure(
        item.id,
        version_id=version.id,
        authority_user_id=user.id,
        input_payload={"value": 1},
    ).execution
    execution.status = "failed"
    execution.last_error_code = "skill_runtime_failed"
    execution.finished_at = datetime.now(timezone.utc)
    db_session.commit()
    db_session.refresh(execution)
    return execution


def _evaluation(execution, *, digest=None, status="pending", attempts=0):
    code = "execution_failed"
    signal = "negative"
    return WorkOutcomeEvaluation(
        work_skill_execution_id=execution.id,
        terminal_status="failed",
        evaluation_code=code,
        learning_signal=signal,
        evaluator_version="deterministic_v1",
        evaluation_digest=(
            digest
            if digest is not None
            else deterministic_evaluation_digest(
                work_skill_execution_id=execution.id,
                terminal_status="failed",
                evaluation_code=code,
                learning_signal=signal,
            )
        ),
        status=status,
        memory_item_id=None,
        attempts=attempts,
        last_error_code=(
            "retry_required"
            if status == "retry_required"
            else None
        ),
        evaluated_at=datetime.now(timezone.utc),
        completed_at=None,
    )


def test_database_rejects_duplicate_evaluation_per_execution(
    db_session: Session,
) -> None:
    execution = _terminal_failure(
        db_session,
        suffix="unique",
    )
    first = _evaluation(execution)
    db_session.add(first)
    db_session.commit()

    duplicate = _evaluation(
        execution,
        digest="b" * 64,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_database_rejects_invalid_digest_and_attempts(
    db_session: Session,
) -> None:
    execution = _terminal_failure(
        db_session,
        suffix="digest",
    )
    invalid_digest = _evaluation(
        execution,
        digest="A" * 64,
    )
    db_session.add(invalid_digest)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    invalid_non_hex = _evaluation(
        execution,
        digest="g" * 64,
    )
    db_session.add(invalid_non_hex)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    invalid_attempts = _evaluation(
        execution,
        attempts=-1,
    )
    db_session.add(invalid_attempts)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_database_rejects_invalid_state_integrity(
    db_session: Session,
) -> None:
    execution = _terminal_failure(
        db_session,
        suffix="state",
    )
    invalid = _evaluation(
        execution,
        status="memory_recorded",
    )
    db_session.add(invalid)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_database_restricts_deleting_linked_execution(
    db_session: Session,
) -> None:
    execution = _terminal_failure(
        db_session,
        suffix="restrict",
    )
    evaluation = _evaluation(execution)
    db_session.add(evaluation)
    db_session.commit()

    db_session.delete(execution)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_outcome_evaluation_status_index_contract(
    db_session: Session,
) -> None:
    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspect(
            db_session.get_bind()
        ).get_indexes(
            "work_outcome_evaluations"
        )
    }
    assert indexes[
        "ix_work_outcome_eval_status_updated"
    ] == (
        "status",
        "updated_at",
        "id",
    )
