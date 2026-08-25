import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.memory_errors import MemoryConflictError
from app.core.memory_errors import MemoryValidationError
from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkStateError
from app.core.work_errors import WorkValidationError
from app.core.work_outcome_evaluation_observability import (
    log_work_outcome_evaluation_event,
)
from app.models.memory import MemoryItem
from app.models.work_outcome_evaluation import WorkOutcomeEvaluation
from app.models.work_skill_execution import WorkSkillExecution
from app.repositories.memory_repository import MemoryRepository
from app.repositories.work_outcome_evaluation_repository import (
    WorkOutcomeEvaluationRepository,
)
from app.repositories.work_repository import WorkRepository
from app.services.memory_service import EvidenceInput
from app.services.memory_service import MemoryService
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


EVALUATOR_VERSION = "deterministic_v1"

TERMINAL_MAPPING = {
    "succeeded": (
        "execution_succeeded",
        "positive",
    ),
    "failed": (
        "execution_failed",
        "negative",
    ),
    "timed_out": (
        "execution_timed_out",
        "negative",
    ),
    "cancelled": (
        "execution_cancelled",
        "neutral",
    ),
}


@dataclass(frozen=True)
class WorkOutcomeEvaluationResult:
    evaluation: WorkOutcomeEvaluation
    memory: MemoryItem
    duplicate: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_id(
    value: int,
    *,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise WorkValidationError(
            f"{field_name} inválido."
        )
    return value


def deterministic_outcome(
    terminal_status: str,
) -> tuple[str, str]:
    normalized = terminal_status.strip().lower()
    mapped = TERMINAL_MAPPING.get(
        normalized
    )
    if mapped is None:
        raise WorkStateError(
            "Outcome Evaluation exige status terminal."
        )
    return mapped


def deterministic_evaluation_digest(
    *,
    work_skill_execution_id: int,
    terminal_status: str,
    evaluation_code: str,
    learning_signal: str,
) -> str:
    canonical = json.dumps(
        [
            EVALUATOR_VERSION,
            work_skill_execution_id,
            terminal_status,
            evaluation_code,
            learning_signal,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _safe_error_code(
    error: Exception,
) -> str:
    if isinstance(error, MemoryConflictError):
        return "memory_conflict"
    if isinstance(error, MemoryValidationError):
        return "memory_validation_failed"
    if isinstance(error, WorkConflictError):
        return "work_link_conflict"
    if isinstance(error, WorkNotFoundError):
        return "work_reference_missing"
    if isinstance(error, WorkStateError):
        return "outcome_state_invalid"
    if isinstance(error, IntegrityError):
        return "database_integrity_failed"
    return "outcome_materialization_failed"


class WorkOutcomeEvaluationService:
    """
    Deterministic post-terminal learning boundary.

    It can materialize evaluation/memory/link state only. It never dispatches,
    retries, invokes a Skill handler, changes approval/RBAC/autonomy, or reads
    raw Skill input/output/error as learning content.
    """

    def __init__(
        self,
        db: Session,
        *,
        repository: WorkOutcomeEvaluationRepository | None = None,
        work_repository: WorkRepository | None = None,
        memory_repository: MemoryRepository | None = None,
        memory_service: MemoryService | None = None,
        work_service: WorkManagerService | None = None,
    ) -> None:
        self.db = db
        self.repository = (
            repository
            if repository is not None
            else WorkOutcomeEvaluationRepository(db)
        )
        self.work_repository = (
            work_repository
            if work_repository is not None
            else WorkRepository(db)
        )
        self.memory_repository = (
            memory_repository
            if memory_repository is not None
            else MemoryRepository(db)
        )
        self.memory_service = (
            memory_service
            if memory_service is not None
            else MemoryService(db)
        )
        self.work_service = (
            work_service
            if work_service is not None
            else WorkManagerService(db)
        )

    def evaluate(
        self,
        work_skill_execution_id: int,
    ) -> WorkOutcomeEvaluationResult:
        normalized_execution_id = _positive_id(
            work_skill_execution_id,
            field_name="work_skill_execution_id",
        )
        execution = self.db.get(
            WorkSkillExecution,
            normalized_execution_id,
        )
        if execution is None:
            raise WorkNotFoundError(
                "WorkSkillExecution não encontrada."
            )

        evaluation_code, learning_signal = (
            deterministic_outcome(
                execution.status
            )
        )
        if execution.finished_at is None:
            raise WorkStateError(
                "Execução terminal sem finished_at."
            )

        work_item = self.work_repository.get_by_id(
            execution.work_item_id
        )
        if work_item is None:
            raise WorkNotFoundError(
                "Work vinculado à execução não foi encontrado."
            )

        digest = deterministic_evaluation_digest(
            work_skill_execution_id=execution.id,
            terminal_status=execution.status,
            evaluation_code=evaluation_code,
            learning_signal=learning_signal,
        )
        evaluation, created = self._load_or_create(
            execution=execution,
            evaluation_code=evaluation_code,
            learning_signal=learning_signal,
            evaluation_digest=digest,
        )

        if evaluation.status == "completed":
            memory = self._load_and_validate_memory(
                evaluation=evaluation,
                execution=execution,
                work_item=work_item,
                evaluation_code=evaluation_code,
                learning_signal=learning_signal,
            )
            self._require_existing_work_link(
                work_item_id=execution.work_item_id,
                memory_item_id=memory.id,
            )
            return WorkOutcomeEvaluationResult(
                evaluation=evaluation,
                memory=memory,
                duplicate=True,
            )

        attempt_number = max(
            evaluation.attempts + 1,
            1,
        )
        memory_item_id = evaluation.memory_item_id

        try:
            evaluation.attempts = attempt_number
            evaluation.last_error_code = None
            evaluation.completed_at = None
            evaluation.status = (
                "memory_recorded"
                if memory_item_id is not None
                else "pending"
            )

            if memory_item_id is None:
                memory = self._remember_outcome(
                    execution=execution,
                    work_item=work_item,
                    evaluation_code=evaluation_code,
                    learning_signal=learning_signal,
                )
                memory_item_id = memory.id
                self._persist_memory_recorded(
                    evaluation=evaluation,
                    memory_item_id=memory.id,
                )
            else:
                memory = self._load_and_validate_memory(
                    evaluation=evaluation,
                    execution=execution,
                    work_item=work_item,
                    evaluation_code=evaluation_code,
                    learning_signal=learning_signal,
                )

            self._ensure_work_link(
                work_item_id=execution.work_item_id,
                work_skill_execution_id=execution.id,
                memory_item_id=memory.id,
            )
            self._mark_completed(
                evaluation
            )
        except Exception as error:
            self.db.rollback()
            self._persist_retry_required(
                execution=execution,
                evaluation_code=evaluation_code,
                learning_signal=learning_signal,
                evaluation_digest=digest,
                attempts=attempt_number,
                memory_item_id=memory_item_id,
                error_code=_safe_error_code(error),
            )
            log_work_outcome_evaluation_event(
                "work.outcome_evaluation.retry_required",
                work_item_id=execution.work_item_id,
                work_skill_execution_id=execution.id,
                memory_item_id=memory_item_id,
                terminal_status=execution.status,
                evaluation_code=evaluation_code,
                learning_signal=learning_signal,
                status="retry_required",
                attempts=attempt_number,
                error_code=_safe_error_code(error),
                outcome="retry_required",
            )
            raise

        self.db.refresh(evaluation)
        log_work_outcome_evaluation_event(
            "work.outcome_evaluation.completed",
            work_item_id=execution.work_item_id,
            work_skill_execution_id=execution.id,
            memory_item_id=evaluation.memory_item_id,
            terminal_status=execution.status,
            evaluation_code=evaluation_code,
            learning_signal=learning_signal,
            status=evaluation.status,
            attempts=evaluation.attempts,
            duplicate=(not created),
            outcome="completed",
        )
        return WorkOutcomeEvaluationResult(
            evaluation=evaluation,
            memory=memory,
            duplicate=(not created),
        )

    def _load_or_create(
        self,
        *,
        execution: WorkSkillExecution,
        evaluation_code: str,
        learning_signal: str,
        evaluation_digest: str,
    ) -> tuple[WorkOutcomeEvaluation, bool]:
        existing = self.repository.lock_by_execution_id(
            execution.id
        )
        if existing is not None:
            self._validate_evaluation(
                existing,
                execution=execution,
                evaluation_code=evaluation_code,
                learning_signal=learning_signal,
                evaluation_digest=evaluation_digest,
            )
            return existing, False

        evaluation = WorkOutcomeEvaluation(
            work_skill_execution_id=execution.id,
            terminal_status=execution.status,
            evaluation_code=evaluation_code,
            learning_signal=learning_signal,
            evaluator_version=EVALUATOR_VERSION,
            evaluation_digest=evaluation_digest,
            status="pending",
            memory_item_id=None,
            attempts=0,
            last_error_code=None,
            evaluated_at=_utc_now(),
            completed_at=None,
        )
        try:
            self.repository.add(
                evaluation
            )
        except IntegrityError:
            self.db.rollback()
            concurrent = self.repository.lock_by_execution_id(
                execution.id
            )
            if concurrent is None:
                raise WorkConflictError(
                    "Conflito concorrente no ledger de outcome."
                )
            self._validate_evaluation(
                concurrent,
                execution=execution,
                evaluation_code=evaluation_code,
                learning_signal=learning_signal,
                evaluation_digest=evaluation_digest,
            )
            return concurrent, False

        return evaluation, True

    @staticmethod
    def _validate_evaluation(
        evaluation: WorkOutcomeEvaluation,
        *,
        execution: WorkSkillExecution,
        evaluation_code: str,
        learning_signal: str,
        evaluation_digest: str,
    ) -> None:
        if (
            evaluation.terminal_status != execution.status
            or evaluation.evaluation_code != evaluation_code
            or evaluation.learning_signal != learning_signal
            or evaluation.evaluator_version != EVALUATOR_VERSION
            or evaluation.evaluation_digest != evaluation_digest
        ):
            raise WorkConflictError(
                "Outcome Evaluation persistida diverge do fato terminal."
            )

    def _remember_outcome(
        self,
        *,
        execution: WorkSkillExecution,
        work_item,
        evaluation_code: str,
        learning_signal: str,
    ) -> MemoryItem:
        memory_key = self._memory_key(
            execution.id
        )
        context_data = self._memory_context(
            execution=execution,
            work_item=work_item,
            evaluation_code=evaluation_code,
            learning_signal=learning_signal,
        )
        evidence = self._evidence_input(
            execution=execution,
            evaluation_code=evaluation_code,
            learning_signal=learning_signal,
        )
        result = self.memory_service.remember(
            memory_type="observation",
            title="Work Skill terminal outcome",
            content=self._memory_content(
                execution=execution,
                evaluation_code=evaluation_code,
                learning_signal=learning_signal,
            ),
            scope_type=work_item.scope_type,
            account_id=work_item.account_id,
            subject_user_id=work_item.subject_user_id,
            created_by_user_id=None,
            source_type="derived",
            source_reference=(
                f"work-skill-execution:{execution.id}"
            ),
            confidence=Decimal("1.000"),
            importance=Decimal("0.500"),
            memory_key=memory_key,
            valid_from=execution.finished_at,
            valid_until=None,
            context_data=context_data,
            evidence=(evidence,),
        )
        self._validate_memory(
            result.memory,
            execution=execution,
            work_item=work_item,
            evaluation_code=evaluation_code,
            learning_signal=learning_signal,
        )
        return result.memory

    def _load_and_validate_memory(
        self,
        *,
        evaluation: WorkOutcomeEvaluation,
        execution: WorkSkillExecution,
        work_item,
        evaluation_code: str,
        learning_signal: str,
    ) -> MemoryItem:
        if evaluation.memory_item_id is None:
            raise WorkStateError(
                "Evaluation sem MemoryItem durável."
            )
        memory = self.db.get(
            MemoryItem,
            evaluation.memory_item_id,
        )
        if memory is None:
            raise WorkStateError(
                "MemoryItem da Evaluation não existe."
            )
        self._validate_memory(
            memory,
            execution=execution,
            work_item=work_item,
            evaluation_code=evaluation_code,
            learning_signal=learning_signal,
        )
        return memory

    def _validate_memory(
        self,
        memory: MemoryItem,
        *,
        execution: WorkSkillExecution,
        work_item,
        evaluation_code: str,
        learning_signal: str,
    ) -> None:
        expected_context = self._memory_context(
            execution=execution,
            work_item=work_item,
            evaluation_code=evaluation_code,
            learning_signal=learning_signal,
        )
        if (
            memory.memory_type != "observation"
            or memory.title != "Work Skill terminal outcome"
            or memory.content != self._memory_content(
                execution=execution,
                evaluation_code=evaluation_code,
                learning_signal=learning_signal,
            )
            or memory.memory_key != self._memory_key(
                execution.id
            )
            or memory.scope_type != work_item.scope_type
            or memory.account_id != work_item.account_id
            or memory.subject_user_id != work_item.subject_user_id
            or memory.source_type != "derived"
            or memory.source_reference
            != f"work-skill-execution:{execution.id}"
            or memory.confidence != Decimal("1.000")
            or memory.importance != Decimal("0.500")
            or (memory.context_data or {}) != expected_context
        ):
            raise WorkConflictError(
                "Memory de outcome diverge do contrato determinístico."
            )

        expected_evidence = self._evidence_input(
            execution=execution,
            evaluation_code=evaluation_code,
            learning_signal=learning_signal,
        )
        evidence_rows = self.memory_repository.list_evidence(
            memory.id
        )
        found = any(
            row.relation == expected_evidence.relation
            and row.source_type == expected_evidence.source_type
            and row.source_reference
            == expected_evidence.source_reference
            and row.evidence_text
            == expected_evidence.evidence_text
            and row.weight == Decimal("1.000")
            and (row.context_data or {}) == {}
            for row in evidence_rows
        )
        if not found:
            raise WorkConflictError(
                "Memory de outcome não possui a evidência determinística."
            )

    def _persist_memory_recorded(
        self,
        *,
        evaluation: WorkOutcomeEvaluation,
        memory_item_id: int,
    ) -> None:
        evaluation.memory_item_id = memory_item_id
        evaluation.status = "memory_recorded"
        evaluation.last_error_code = None
        evaluation.completed_at = None
        try:
            self.db.commit()
            self.db.refresh(
                evaluation
            )
        except Exception:
            self.db.rollback()
            raise

    def _ensure_work_link(
        self,
        *,
        work_item_id: int,
        work_skill_execution_id: int,
        memory_item_id: int,
    ) -> None:
        work_item = self.work_repository.lock_by_id(
            work_item_id
        )
        if work_item is None:
            raise WorkNotFoundError(
                "Work do outcome não foi encontrado."
            )
        existing = self.work_repository.find_memory_link(
            work_item_id=work_item.id,
            memory_id=memory_item_id,
            relation="outcome",
        )
        if existing is not None:
            return

        self.work_service.link_memory(
            work_item.id,
            memory_id=memory_item_id,
            relation="outcome",
            expected_version=work_item.version,
            actor=WorkActor(
                actor_type="system",
                actor_reference=(
                    f"system:work:{work_item.id}"
                ),
                actor_user_id=None,
            ),
            idempotency_key=(
                f"work:{work_item.id}:outcome-memory:"
                f"{work_skill_execution_id}:v1"
            ),
        )

    def _require_existing_work_link(
        self,
        *,
        work_item_id: int,
        memory_item_id: int,
    ) -> None:
        existing = self.work_repository.find_memory_link(
            work_item_id=work_item_id,
            memory_id=memory_item_id,
            relation="outcome",
        )
        if existing is None:
            raise WorkStateError(
                "Evaluation completed sem WorkMemoryLink outcome."
            )

    def _mark_completed(
        self,
        evaluation: WorkOutcomeEvaluation,
    ) -> None:
        evaluation.status = "completed"
        evaluation.last_error_code = None
        evaluation.completed_at = _utc_now()
        try:
            self.db.commit()
            self.db.refresh(
                evaluation
            )
        except Exception:
            self.db.rollback()
            raise

    def _persist_retry_required(
        self,
        *,
        execution: WorkSkillExecution,
        evaluation_code: str,
        learning_signal: str,
        evaluation_digest: str,
        attempts: int,
        memory_item_id: int | None,
        error_code: str,
    ) -> None:
        try:
            evaluation = self.repository.lock_by_execution_id(
                execution.id
            )
            if evaluation is None:
                evaluation = WorkOutcomeEvaluation(
                    work_skill_execution_id=execution.id,
                    terminal_status=execution.status,
                    evaluation_code=evaluation_code,
                    learning_signal=learning_signal,
                    evaluator_version=EVALUATOR_VERSION,
                    evaluation_digest=evaluation_digest,
                    status="retry_required",
                    memory_item_id=memory_item_id,
                    attempts=max(attempts, 1),
                    last_error_code=error_code,
                    evaluated_at=_utc_now(),
                    completed_at=None,
                )
                try:
                    self.repository.add(
                        evaluation
                    )
                except IntegrityError:
                    self.db.rollback()
                    evaluation = self.repository.lock_by_execution_id(
                        execution.id
                    )
                    if evaluation is None:
                        return

            self._validate_evaluation(
                evaluation,
                execution=execution,
                evaluation_code=evaluation_code,
                learning_signal=learning_signal,
                evaluation_digest=evaluation_digest,
            )
            if evaluation.status == "completed":
                self.db.rollback()
                return

            evaluation.status = "retry_required"
            if evaluation.memory_item_id is None:
                evaluation.memory_item_id = memory_item_id
            evaluation.attempts = max(
                evaluation.attempts,
                attempts,
                1,
            )
            evaluation.last_error_code = error_code
            evaluation.completed_at = None
            self.db.commit()
        except Exception:
            self.db.rollback()

    @staticmethod
    def _memory_key(
        work_skill_execution_id: int,
    ) -> str:
        return (
            "work-skill-outcome:"
            f"{work_skill_execution_id}:v1"
        )

    @staticmethod
    def _memory_content(
        *,
        execution: WorkSkillExecution,
        evaluation_code: str,
        learning_signal: str,
    ) -> str:
        return (
            f"Work Skill execution {execution.id} ended with terminal "
            f"status {execution.status}; evaluation={evaluation_code}; "
            f"learning_signal={learning_signal}."
        )

    @staticmethod
    def _memory_context(
        *,
        execution: WorkSkillExecution,
        work_item,
        evaluation_code: str,
        learning_signal: str,
    ) -> dict[str, object]:
        return {
            "work_item_id": work_item.id,
            "work_skill_execution_id": execution.id,
            "skill_version_id": execution.skill_version_id,
            "skill_invocation_id": execution.skill_invocation_id,
            "terminal_status": execution.status,
            "evaluation_code": evaluation_code,
            "learning_signal": learning_signal,
            "evaluator_version": EVALUATOR_VERSION,
        }

    @staticmethod
    def _evidence_input(
        *,
        execution: WorkSkillExecution,
        evaluation_code: str,
        learning_signal: str,
    ) -> EvidenceInput:
        source_reference = (
            f"skill-invocation:{execution.skill_invocation_id}"
            if execution.skill_invocation_id is not None
            else f"work-skill-execution:{execution.id}"
        )
        return EvidenceInput(
            relation="supports",
            source_type="system",
            source_reference=source_reference,
            evidence_text=(
                f"Terminal status {execution.status} for Work Skill "
                f"execution {execution.id} deterministically maps to "
                f"{evaluation_code} with learning signal "
                f"{learning_signal}."
            ),
            weight=Decimal("1.000"),
            observed_at=execution.finished_at,
            created_by_user_id=None,
            context_data={},
        )
