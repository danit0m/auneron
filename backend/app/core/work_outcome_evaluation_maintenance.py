import asyncio
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.work_outcome_evaluation_observability import (
    log_work_outcome_evaluation_event,
)
from app.database.database import SessionLocal
from app.repositories.work_outcome_evaluation_repository import (
    WorkOutcomeEvaluationRepository,
)
from app.services.work_outcome_evaluation import (
    WorkOutcomeEvaluationService,
)


@dataclass(frozen=True)
class WorkOutcomeEvaluationRecoverySummary:
    candidate_count: int
    completed_count: int
    attention_required_count: int
    failure_count: int


def run_work_outcome_evaluation_recovery(
    *,
    limit: int | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    repository_factory: Callable[
        [Session],
        WorkOutcomeEvaluationRepository,
    ] = WorkOutcomeEvaluationRepository,
    service_factory: Callable[
        [Session],
        WorkOutcomeEvaluationService,
    ] = WorkOutcomeEvaluationService,
) -> WorkOutcomeEvaluationRecoverySummary:
    """Materialize missing terminal learning without executing handlers."""
    effective_limit = (
        settings.work_skill_recovery_batch_size
        if limit is None
        else limit
    )
    if (
        isinstance(effective_limit, bool)
        or not isinstance(effective_limit, int)
        or effective_limit < 1
        or effective_limit > 1000
    ):
        raise ValueError(
            "limit inválido para Outcome Evaluation recovery."
        )

    db = session_factory()
    try:
        repository = repository_factory(db)
        service = service_factory(db)
        execution_ids = (
            repository.list_recovery_candidate_execution_ids(
                limit=effective_limit
            )
        )
        completed_count = 0
        attention_required_count = 0
        failure_count = 0

        for execution_id in execution_ids:
            try:
                result = service.evaluate(
                    execution_id
                )
            except Exception:
                db.rollback()
                failure_count += 1
                attention_required_count += 1
                log_work_outcome_evaluation_event(
                    "work.outcome_evaluation.recovery_failed",
                    work_skill_execution_id=execution_id,
                    failure_count=failure_count,
                    attention_required_count=(
                        attention_required_count
                    ),
                    outcome="retry_required",
                )
                continue

            completed_count += 1
            log_work_outcome_evaluation_event(
                "work.outcome_evaluation.recovered",
                work_skill_execution_id=execution_id,
                memory_item_id=result.memory.id,
                terminal_status=result.evaluation.terminal_status,
                evaluation_code=result.evaluation.evaluation_code,
                learning_signal=result.evaluation.learning_signal,
                status=result.evaluation.status,
                attempts=result.evaluation.attempts,
                duplicate=result.duplicate,
                outcome="completed",
            )

        summary = WorkOutcomeEvaluationRecoverySummary(
            candidate_count=len(execution_ids),
            completed_count=completed_count,
            attention_required_count=attention_required_count,
            failure_count=failure_count,
        )
        log_work_outcome_evaluation_event(
            "work.outcome_evaluation.recovery_completed",
            candidate_count=summary.candidate_count,
            completed_count=summary.completed_count,
            attention_required_count=summary.attention_required_count,
            failure_count=summary.failure_count,
            outcome="completed",
        )
        return summary
    finally:
        db.close()


async def run_work_outcome_evaluation_recovery_async(
) -> WorkOutcomeEvaluationRecoverySummary:
    worker = asyncio.create_task(
        asyncio.to_thread(
            run_work_outcome_evaluation_recovery
        )
    )

    try:
        return await asyncio.shield(
            worker
        )
    except asyncio.CancelledError:
        try:
            await worker
        except Exception:
            log_work_outcome_evaluation_event(
                "work.outcome_evaluation.shutdown_drain_failed",
                outcome="worker_failed",
            )
        raise


async def work_outcome_evaluation_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(
            settings.work_skill_recovery_interval_seconds
        )
        await run_work_outcome_evaluation_recovery_async()
