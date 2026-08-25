from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String
from sqlalchemy import and_
from sqlalchemy import cast
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.memory import MemoryItem
from app.models.work import WorkItem
from app.models.work import WorkMemoryLink
from app.models.work_outcome_evaluation import WorkOutcomeEvaluation
from app.models.work_skill_execution import WorkSkillExecution


EVALUATOR_VERSION = "deterministic_v1"
TERMINAL_STATUSES = (
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
)


@dataclass(frozen=True)
class WorkLearningContextCandidate:
    memory_id: int
    source_work_item_id: int
    work_skill_execution_id: int
    skill_version_id: int
    terminal_status: str
    evaluation_code: str
    learning_signal: str
    observed_at: datetime


class WorkLearningContextRepository:
    """Read-only query boundary for deterministic prior Work outcomes."""

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def list_outcome_candidates(
        self,
        *,
        target_work_item_id: int,
        skill_version_id: int,
        scope_type: str,
        account_id: int | None,
        subject_user_id: int | None,
        as_of: datetime,
        limit: int,
    ) -> list[WorkLearningContextCandidate]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 10
        ):
            raise ValueError(
                "limit inválido para Work Learning Context."
            )
        if (
            not isinstance(as_of, datetime)
            or as_of.tzinfo is None
            or as_of.utcoffset() is None
        ):
            raise ValueError(
                "as_of deve possuir timezone."
            )

        scope_predicates = self._scope_predicates(
            scope_type=scope_type,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
        expected_memory_key = func.concat(
            "work-skill-outcome:",
            cast(WorkSkillExecution.id, String),
            ":v1",
        )
        expected_source_reference = func.concat(
            "work-skill-execution:",
            cast(WorkSkillExecution.id, String),
        )
        deterministic_mapping = or_(
            and_(
                WorkSkillExecution.status == "succeeded",
                WorkOutcomeEvaluation.terminal_status == "succeeded",
                WorkOutcomeEvaluation.evaluation_code
                == "execution_succeeded",
                WorkOutcomeEvaluation.learning_signal == "positive",
            ),
            and_(
                WorkSkillExecution.status == "failed",
                WorkOutcomeEvaluation.terminal_status == "failed",
                WorkOutcomeEvaluation.evaluation_code
                == "execution_failed",
                WorkOutcomeEvaluation.learning_signal == "negative",
            ),
            and_(
                WorkSkillExecution.status == "timed_out",
                WorkOutcomeEvaluation.terminal_status == "timed_out",
                WorkOutcomeEvaluation.evaluation_code
                == "execution_timed_out",
                WorkOutcomeEvaluation.learning_signal == "negative",
            ),
            and_(
                WorkSkillExecution.status == "cancelled",
                WorkOutcomeEvaluation.terminal_status == "cancelled",
                WorkOutcomeEvaluation.evaluation_code
                == "execution_cancelled",
                WorkOutcomeEvaluation.learning_signal == "neutral",
            ),
        )

        statement = (
            select(
                WorkOutcomeEvaluation.memory_item_id,
                WorkSkillExecution.work_item_id,
                WorkSkillExecution.id,
                WorkSkillExecution.skill_version_id,
                WorkOutcomeEvaluation.terminal_status,
                WorkOutcomeEvaluation.evaluation_code,
                WorkOutcomeEvaluation.learning_signal,
                WorkSkillExecution.finished_at,
            )
            .select_from(WorkOutcomeEvaluation)
            .join(
                WorkSkillExecution,
                WorkSkillExecution.id
                == WorkOutcomeEvaluation.work_skill_execution_id,
            )
            .join(
                WorkItem,
                WorkItem.id == WorkSkillExecution.work_item_id,
            )
            .join(
                MemoryItem,
                MemoryItem.id
                == WorkOutcomeEvaluation.memory_item_id,
            )
            .join(
                WorkMemoryLink,
                and_(
                    WorkMemoryLink.work_item_id == WorkItem.id,
                    WorkMemoryLink.memory_id == MemoryItem.id,
                    WorkMemoryLink.relation == "outcome",
                ),
            )
            .where(
                WorkOutcomeEvaluation.status == "completed",
                WorkOutcomeEvaluation.evaluator_version
                == EVALUATOR_VERSION,
                WorkSkillExecution.status.in_(TERMINAL_STATUSES),
                deterministic_mapping,
                WorkSkillExecution.skill_version_id
                == skill_version_id,
                WorkSkillExecution.work_item_id
                != target_work_item_id,
                WorkSkillExecution.finished_at.is_not(None),
                WorkSkillExecution.finished_at <= as_of,
                MemoryItem.status == "active",
                MemoryItem.memory_type == "observation",
                MemoryItem.source_type == "derived",
                MemoryItem.confidence == Decimal("1.000"),
                MemoryItem.importance == Decimal("0.500"),
                MemoryItem.memory_key == expected_memory_key,
                MemoryItem.source_reference
                == expected_source_reference,
                MemoryItem.valid_from <= as_of,
                or_(
                    MemoryItem.valid_until.is_(None),
                    MemoryItem.valid_until > as_of,
                ),
                *scope_predicates,
            )
            .order_by(
                WorkSkillExecution.finished_at.desc(),
                WorkSkillExecution.id.desc(),
            )
            .limit(limit + 1)
        )

        candidates: list[WorkLearningContextCandidate] = []
        for row in self.db.execute(statement).all():
            memory_id = row[0]
            observed_at = row[7]
            if memory_id is None or observed_at is None:
                continue
            candidates.append(
                WorkLearningContextCandidate(
                    memory_id=int(memory_id),
                    source_work_item_id=int(row[1]),
                    work_skill_execution_id=int(row[2]),
                    skill_version_id=int(row[3]),
                    terminal_status=str(row[4]),
                    evaluation_code=str(row[5]),
                    learning_signal=str(row[6]),
                    observed_at=observed_at,
                )
            )
        return candidates

    @staticmethod
    def _scope_predicates(
        *,
        scope_type: str,
        account_id: int | None,
        subject_user_id: int | None,
    ) -> tuple[object, ...]:
        if scope_type == "global":
            if account_id is not None or subject_user_id is not None:
                raise ValueError("Escopo global inválido.")
            return (
                WorkItem.scope_type == "global",
                WorkItem.account_id.is_(None),
                WorkItem.subject_user_id.is_(None),
                MemoryItem.scope_type == "global",
                MemoryItem.account_id.is_(None),
                MemoryItem.subject_user_id.is_(None),
            )

        if scope_type == "account":
            if account_id is None or subject_user_id is not None:
                raise ValueError("Escopo account inválido.")
            return (
                WorkItem.scope_type == "account",
                WorkItem.account_id == account_id,
                WorkItem.subject_user_id.is_(None),
                MemoryItem.scope_type == "account",
                MemoryItem.account_id == account_id,
                MemoryItem.subject_user_id.is_(None),
            )

        if scope_type == "user":
            if account_id is not None or subject_user_id is None:
                raise ValueError("Escopo user inválido.")
            return (
                WorkItem.scope_type == "user",
                WorkItem.account_id.is_(None),
                WorkItem.subject_user_id == subject_user_id,
                MemoryItem.scope_type == "user",
                MemoryItem.account_id.is_(None),
                MemoryItem.subject_user_id == subject_user_id,
            )

        raise ValueError("Escopo de Work Learning Context inválido.")
