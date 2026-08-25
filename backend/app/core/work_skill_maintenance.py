import asyncio
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.work_skill_observability import (
    log_work_skill_execution_event,
)
from app.database.database import SessionLocal
from app.repositories.work_skill_execution_repository import (
    WorkSkillExecutionRepository,
)
from app.services.work_skill_execution import WorkSkillExecutionService


_ATTENTION_OUTCOMES = frozenset({
    "configuration_retry_required",
    "retry_required",
})


@dataclass(frozen=True)
class WorkSkillRecoverySummary:
    candidate_count: int
    reconciled_count: int
    attention_required_count: int
    failure_count: int


def run_work_skill_execution_recovery(
    *,
    limit: int | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    repository_factory: Callable[
        [Session],
        WorkSkillExecutionRepository,
    ] = WorkSkillExecutionRepository,
    service_factory: Callable[
        [Session],
        WorkSkillExecutionService,
    ] = WorkSkillExecutionService,
) -> WorkSkillRecoverySummary:
    """Reconcile durable Work -> Skill ledgers without executing handlers."""
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
            "limit inválido para Work Skill recovery."
        )

    db = session_factory()
    try:
        repository = repository_factory(db)
        service = service_factory(db)
        work_item_ids = repository.list_recovery_candidate_work_ids(
            limit=effective_limit
        )
        reconciled_count = 0
        attention_required_count = 0
        failure_count = 0

        for work_item_id in work_item_ids:
            try:
                result = service.reconcile(work_item_id)
            except Exception:
                db.rollback()
                failure_count += 1
                log_work_skill_execution_event(
                    "work.skill_execution.recovery_failed",
                    work_item_id=work_item_id,
                    failure_count=failure_count,
                )
                continue

            if result.outcome in _ATTENTION_OUTCOMES:
                attention_required_count += 1
            else:
                reconciled_count += 1

            log_work_skill_execution_event(
                "work.skill_execution.reconciled",
                work_item_id=result.work_item.id,
                work_skill_execution_id=result.execution.id,
                skill_version_id=result.execution.skill_version_id,
                approval_request_id=result.execution.approval_request_id,
                skill_invocation_id=result.execution.skill_invocation_id,
                status=result.execution.status,
                outcome=result.outcome,
                duplicate=result.duplicate,
                dispatch_attempts=result.execution.dispatch_attempts,
            )

        summary = WorkSkillRecoverySummary(
            candidate_count=len(work_item_ids),
            reconciled_count=reconciled_count,
            attention_required_count=attention_required_count,
            failure_count=failure_count,
        )
        log_work_skill_execution_event(
            "work.skill_execution.recovery_completed",
            candidate_count=summary.candidate_count,
            reconciled_count=summary.reconciled_count,
            attention_required_count=summary.attention_required_count,
            failure_count=summary.failure_count,
        )
        return summary
    finally:
        db.close()


async def run_work_skill_execution_recovery_async(
) -> WorkSkillRecoverySummary:
    """
    Run synchronous recovery without abandoning an in-flight DB worker.

    Cancellation is delayed until the underlying thread finishes so shutdown
    cannot proceed while that worker may still own a Session/transaction.
    """
    worker = asyncio.create_task(
        asyncio.to_thread(
            run_work_skill_execution_recovery
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
            log_work_skill_execution_event(
                "work.skill_execution.shutdown_drain_failed",
                outcome="worker_failed",
            )
        raise


async def work_skill_execution_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(
            settings.work_skill_recovery_interval_seconds
        )
        await run_work_skill_execution_recovery_async()
