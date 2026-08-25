from datetime import datetime
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.work_errors import WorkStateError
from app.models.memory import MemoryEvidence
from app.models.memory import MemoryItem
from app.models.user import User
from app.models.work import WorkMemoryLink
from app.models.work_outcome_evaluation import WorkOutcomeEvaluation
from app.services.skill_service import SkillService
from app.services.work_outcome_evaluation import EVALUATOR_VERSION
from app.services.work_outcome_evaluation import (
    WorkOutcomeEvaluationService,
)
from app.services.work_outcome_evaluation import deterministic_outcome
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService
from app.services.work_skill_execution import WorkSkillExecutionService


SYSTEM_ACTOR = WorkActor(
    actor_type="system",
    actor_reference="system:test:25a",
)


def _configured_execution(
    db_session: Session,
    *,
    suffix: str,
):
    user = User(
        name="Outcome Evaluation 25A",
        email=f"outcome.{suffix}@example.com",
        password_hash="not-used",
        role="analyst",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    skill_service = SkillService(
        db_session
    )
    skill = skill_service.register_skill(
        skill_key=f"outcome.{suffix}",
        provider="auneron.core",
        display_name="Outcome Evaluation 25A",
        description="Outcome Evaluation test",
    )
    draft = skill_service.create_draft_version(
        skill_id=skill.id,
        version="1.0.0",
        runtime_kind="internal_python",
        handler_reference=(
            "app.skills.outcome:"
            + suffix.replace("-", "_")
        ),
        execution_mode="read_only",
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
        },
    )
    version = skill_service.publish_version(
        draft.id
    ).version

    work_service = WorkManagerService(
        db_session
    )
    item = work_service.create(
        work_type="task",
        title="Outcome Evaluation 25A",
        work_key=f"outcome.{suffix}",
        scope_type="global",
        origin_type="system",
        origin_reference="system:test:25a",
        actor=SYSTEM_ACTOR,
        context_data={
            "secret_model_output": "must-not-learn",
            "dispatch_key": "must-not-learn",
        },
    ).work_item
    item = work_service.transition_status(
        item.id,
        expected_version=item.version,
        actor=SYSTEM_ACTOR,
        status="ready",
    ).work_item

    configured = WorkSkillExecutionService(
        db_session
    ).configure(
        item.id,
        version_id=version.id,
        authority_user_id=user.id,
        input_payload={"value": 1},
    )
    return configured.execution, item


def _terminal_failure(
    db_session: Session,
    *,
    suffix: str,
):
    execution, item = _configured_execution(
        db_session,
        suffix=suffix,
    )
    execution.status = "failed"
    execution.last_error_code = "skill_runtime_failed"
    execution.finished_at = datetime.now(
        timezone.utc
    )
    db_session.commit()
    db_session.refresh(execution)
    return execution, item


def test_deterministic_mapping_covers_all_terminal_statuses() -> None:
    assert deterministic_outcome("succeeded") == (
        "execution_succeeded",
        "positive",
    )
    assert deterministic_outcome("failed") == (
        "execution_failed",
        "negative",
    )
    assert deterministic_outcome("timed_out") == (
        "execution_timed_out",
        "negative",
    )
    assert deterministic_outcome("cancelled") == (
        "execution_cancelled",
        "neutral",
    )


def test_nonterminal_evaluation_is_rejected(
    db_session: Session,
) -> None:
    execution, _ = _configured_execution(
        db_session,
        suffix="nonterminal",
    )
    with pytest.raises(
        WorkStateError,
        match="status terminal",
    ):
        WorkOutcomeEvaluationService(
            db_session
        ).evaluate(
            execution.id
        )


def test_evaluate_materializes_safe_memory_and_link_idempotently(
    db_session: Session,
) -> None:
    execution, item = _terminal_failure(
        db_session,
        suffix="materialize",
    )
    service = WorkOutcomeEvaluationService(
        db_session
    )

    first = service.evaluate(
        execution.id
    )
    second = service.evaluate(
        execution.id
    )

    assert first.evaluation.status == "completed"
    assert first.evaluation.evaluator_version == EVALUATOR_VERSION
    assert first.evaluation.terminal_status == "failed"
    assert first.evaluation.evaluation_code == "execution_failed"
    assert first.evaluation.learning_signal == "negative"
    assert second.duplicate is True
    assert second.evaluation.id == first.evaluation.id
    assert second.memory.id == first.memory.id

    memory = db_session.get(
        MemoryItem,
        first.memory.id,
    )
    assert memory is not None
    assert memory.memory_type == "observation"
    assert memory.source_type == "derived"
    assert memory.confidence == Decimal("1.000")
    assert memory.context_data == {
        "work_item_id": item.id,
        "work_skill_execution_id": execution.id,
        "skill_version_id": execution.skill_version_id,
        "skill_invocation_id": None,
        "terminal_status": "failed",
        "evaluation_code": "execution_failed",
        "learning_signal": "negative",
        "evaluator_version": "deterministic_v1",
    }
    serialized = str(memory.context_data)
    assert "secret_model_output" not in serialized
    assert "must-not-learn" not in serialized
    assert "dispatch_key" not in serialized

    evidence = db_session.query(
        MemoryEvidence
    ).filter(
        MemoryEvidence.memory_id == memory.id
    ).one()
    assert evidence.relation == "supports"
    assert evidence.source_type == "system"
    assert "skill_runtime_failed" not in evidence.evidence_text
    assert "must-not-learn" not in evidence.evidence_text

    link = db_session.query(
        WorkMemoryLink
    ).filter(
        WorkMemoryLink.work_item_id == item.id,
        WorkMemoryLink.memory_id == memory.id,
        WorkMemoryLink.relation == "outcome",
    ).one()
    assert link.id is not None
    assert (
        db_session.query(
            WorkOutcomeEvaluation
        ).count()
        == 1
    )


def test_failure_after_memory_commit_recovers_without_duplicate(
    db_session: Session,
    monkeypatch,
) -> None:
    execution, _ = _terminal_failure(
        db_session,
        suffix="memory-crash",
    )
    service = WorkOutcomeEvaluationService(
        db_session
    )

    def crash_after_memory(*args, **kwargs):
        raise RuntimeError("simulated after memory commit")

    monkeypatch.setattr(
        service,
        "_persist_memory_recorded",
        crash_after_memory,
    )
    with pytest.raises(RuntimeError):
        service.evaluate(
            execution.id
        )

    assert (
        db_session.query(
            MemoryItem
        ).filter(
            MemoryItem.memory_key
            == f"work-skill-outcome:{execution.id}:v1"
        ).count()
        == 1
    )

    recovered = WorkOutcomeEvaluationService(
        db_session
    ).evaluate(
        execution.id
    )
    assert recovered.evaluation.status == "completed"
    assert (
        db_session.query(
            MemoryItem
        ).filter(
            MemoryItem.memory_key
            == f"work-skill-outcome:{execution.id}:v1"
        ).count()
        == 1
    )


def test_failure_after_work_link_commit_finishes_without_replay(
    db_session: Session,
    monkeypatch,
) -> None:
    execution, item = _terminal_failure(
        db_session,
        suffix="link-crash",
    )
    service = WorkOutcomeEvaluationService(
        db_session
    )

    def crash_after_link(*args, **kwargs):
        raise RuntimeError("simulated after Work link commit")

    monkeypatch.setattr(
        service,
        "_mark_completed",
        crash_after_link,
    )
    with pytest.raises(RuntimeError):
        service.evaluate(
            execution.id
        )

    evaluation = db_session.query(
        WorkOutcomeEvaluation
    ).filter(
        WorkOutcomeEvaluation.work_skill_execution_id
        == execution.id
    ).one()
    assert evaluation.memory_item_id is not None
    assert (
        db_session.query(
            WorkMemoryLink
        ).filter(
            WorkMemoryLink.work_item_id == item.id,
            WorkMemoryLink.memory_id == evaluation.memory_item_id,
            WorkMemoryLink.relation == "outcome",
        ).count()
        == 1
    )

    recovery = WorkOutcomeEvaluationService(
        db_session
    )

    def forbidden_replay(*args, **kwargs):
        raise AssertionError("existing Work link must be prechecked")

    monkeypatch.setattr(
        recovery.work_service,
        "link_memory",
        forbidden_replay,
    )
    result = recovery.evaluate(
        execution.id
    )
    assert result.evaluation.status == "completed"


def test_best_effort_hook_never_rewrites_terminal_execution(
    db_session: Session,
) -> None:
    execution, _ = _terminal_failure(
        db_session,
        suffix="best-effort",
    )

    class FailingOutcomeService:
        def evaluate(self, execution_id):
            raise RuntimeError("simulated learning failure")

    service = WorkSkillExecutionService(
        db_session,
        outcome_evaluation_service=FailingOutcomeService(),
    )
    service._evaluate_terminal_outcome_best_effort(
        execution
    )
    db_session.expire_all()
    persisted = db_session.get(
        type(execution),
        execution.id,
    )
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.last_error_code == "skill_runtime_failed"
