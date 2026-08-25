from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.work_outcome_evaluation import WorkOutcomeEvaluation
from app.models.work_skill_execution import WorkSkillExecution


TERMINAL_EXECUTION_STATUSES = frozenset({
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
})

RECOVERY_EVALUATION_STATUSES = frozenset({
    "pending",
    "memory_recorded",
    "retry_required",
})


class WorkOutcomeEvaluationRepository:
    """Transaction-free persistence for deterministic Work outcomes."""

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def add(
        self,
        evaluation: WorkOutcomeEvaluation,
    ) -> WorkOutcomeEvaluation:
        self.db.add(evaluation)
        self.db.flush()
        return evaluation

    def get_by_execution_id(
        self,
        work_skill_execution_id: int,
    ) -> WorkOutcomeEvaluation | None:
        statement = select(
            WorkOutcomeEvaluation
        ).where(
            WorkOutcomeEvaluation.work_skill_execution_id
            == work_skill_execution_id
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def lock_by_execution_id(
        self,
        work_skill_execution_id: int,
    ) -> WorkOutcomeEvaluation | None:
        statement = (
            select(WorkOutcomeEvaluation)
            .where(
                WorkOutcomeEvaluation.work_skill_execution_id
                == work_skill_execution_id
            )
            .with_for_update()
        )
        return self.db.execute(
            statement
        ).scalar_one_or_none()

    def list_recovery_candidate_execution_ids(
        self,
        *,
        limit: int = 100,
    ) -> list[int]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 1000
        ):
            raise ValueError(
                "limit inválido para Outcome Evaluation recovery."
            )

        statement = (
            select(WorkSkillExecution.id)
            .outerjoin(
                WorkOutcomeEvaluation,
                WorkOutcomeEvaluation.work_skill_execution_id
                == WorkSkillExecution.id,
            )
            .where(
                WorkSkillExecution.status.in_(
                    TERMINAL_EXECUTION_STATUSES
                ),
                or_(
                    WorkOutcomeEvaluation.id.is_(None),
                    WorkOutcomeEvaluation.status.in_(
                        RECOVERY_EVALUATION_STATUSES
                    ),
                ),
            )
            .order_by(
                func.coalesce(
                    WorkOutcomeEvaluation.updated_at,
                    WorkSkillExecution.updated_at,
                ),
                WorkSkillExecution.id,
            )
            .limit(limit)
        )
        return [
            int(execution_id)
            for execution_id in self.db.execute(
                statement
            ).scalars().all()
        ]
