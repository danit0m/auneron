from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalError
from app.core.approval_errors import ApprovalNotFoundError
from app.core.skill_authorization import SkillExecutionGrant
from app.core.skill_authorization import authorize_skill_execution
from app.core.skill_errors import SkillExecutionTimeoutError
from app.core.skill_errors import SkillInvocationInProgressError
from app.core.skill_errors import SkillRuntimeError
from app.core.work_skill_observability import (
    log_work_skill_execution_event,
)
from app.core.work_outcome_evaluation_observability import (
    log_work_outcome_evaluation_event,
)
from app.core.work_skill_rate_limiting import (
    WorkSkillDispatchRateLimiter,
)
from app.core.work_skill_rate_limiting import (
    work_skill_dispatch_rate_limiter,
)
from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkStateError
from app.core.work_errors import WorkValidationError
from app.models.approval import ApprovalRequest
from app.models.skill import SkillInvocation
from app.models.user import User
from app.models.work import WorkItem
from app.models.work_skill_execution import WorkSkillExecution
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.skill_repository import SkillRepository
from app.repositories.work_repository import WorkRepository
from app.repositories.work_skill_execution_repository import (
    WorkSkillExecutionRepository,
)
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import ApprovalService
from app.services.approval_service import approval_input_identity
from app.services.governed_skill_execution import (
    GovernedSkillExecutionResult,
)
from app.services.governed_skill_execution import (
    GovernedSkillExecutionService,
)
from app.services.skill_runtime import SkillInvocationActor
from app.services.skill_runtime_context import (
    WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL,
)
from app.services.skill_runtime_context import WorkLearningRuntimeContext
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService
from app.services.work_outcome_evaluation import (
    WorkOutcomeEvaluationService,
)
from app.services.work_learning_runtime_context_snapshot import (
    WorkLearningRuntimeContextSnapshotService,
)


WorkSkillExecutionOutcome = Literal[
    "ready",
    "approval_pending",
    "rate_limited",
    "in_progress",
    "retry_required",
    "configuration_retry_required",
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
]


@dataclass(frozen=True)
class WorkSkillExecutionResult:
    execution: WorkSkillExecution
    work_item: WorkItem
    outcome: WorkSkillExecutionOutcome
    duplicate: bool
    retry_after_seconds: int | None = None


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


def _safe_error_code(
    error: Exception,
) -> str:
    if isinstance(
        error,
        SkillExecutionTimeoutError,
    ):
        return "skill_timed_out"
    if isinstance(
        error,
        SkillRuntimeError,
    ):
        return "skill_runtime_failed"
    if isinstance(
        error,
        ApprovalError,
    ):
        return "governance_failed"
    return "execution_failed"


class WorkSkillExecutionService:
    """
    Fronteira interna e durável Work -> GovernedSkillExecutionService.

    O Work descreve continuidade operacional, mas nunca concede autoridade.
    `actor_type`, `actor_reference`, dispatch keys e autoridade são derivados
    ou revalidados pelo servidor. O payload executado não é persistido nesta
    tabela; somente seu digest é durável.
    """

    def __init__(
        self,
        db: Session,
        *,
        execution_repository: WorkSkillExecutionRepository | None = None,
        work_repository: WorkRepository | None = None,
        skill_repository: SkillRepository | None = None,
        approval_repository: ApprovalRepository | None = None,
        work_service: WorkManagerService | None = None,
        approval_service: ApprovalService | None = None,
        governed_service: GovernedSkillExecutionService | None = None,
        limiter: WorkSkillDispatchRateLimiter | None = None,
        outcome_evaluation_service: (
            WorkOutcomeEvaluationService | None
        ) = None,
        learning_context_snapshot_service: (
            WorkLearningRuntimeContextSnapshotService | None
        ) = None,
    ) -> None:
        self.db = db
        self.execution_repository = (
            execution_repository
            if execution_repository is not None
            else WorkSkillExecutionRepository(db)
        )
        self.work_repository = (
            work_repository
            if work_repository is not None
            else WorkRepository(db)
        )
        self.skill_repository = (
            skill_repository
            if skill_repository is not None
            else SkillRepository(db)
        )
        self.approval_repository = (
            approval_repository
            if approval_repository is not None
            else ApprovalRepository(db)
        )
        self.work_service = (
            work_service
            if work_service is not None
            else WorkManagerService(db)
        )
        self.approval_service = (
            approval_service
            if approval_service is not None
            else ApprovalService(db)
        )
        self.governed_service = (
            governed_service
            if governed_service is not None
            else GovernedSkillExecutionService(db)
        )
        self.limiter = (
            limiter
            if limiter is not None
            else work_skill_dispatch_rate_limiter
        )
        self.outcome_evaluation_service = (
            outcome_evaluation_service
            if outcome_evaluation_service is not None
            else WorkOutcomeEvaluationService(db)
        )
        self.learning_context_snapshot_service = (
            learning_context_snapshot_service
            if learning_context_snapshot_service is not None
            else WorkLearningRuntimeContextSnapshotService(db)
        )

    def configure(
        self,
        work_item_id: int,
        *,
        version_id: int,
        authority_user_id: int,
        input_payload: Any,
    ) -> WorkSkillExecutionResult:
        normalized_work_id = _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        normalized_version_id = _positive_id(
            version_id,
            field_name="version_id",
        )
        normalized_authority_id = _positive_id(
            authority_user_id,
            field_name="authority_user_id",
        )
        normalized_input, input_digest = (
            approval_input_identity(
                input_payload
            )
        )

        work_item = self.work_repository.lock_by_id(
            normalized_work_id
        )
        if work_item is None:
            raise WorkNotFoundError(
                "Trabalho inexistente."
            )
        if work_item.status != "ready":
            raise WorkStateError(
                "Somente Work em ready pode ser configurado para execução."
            )

        version = self.skill_repository.get_version(
            normalized_version_id
        )
        if (
            version is None
            or version.status != "published"
        ):
            raise WorkNotFoundError(
                "Versão de Skill inexistente ou não publicada."
            )
        if version.execution_mode == "external":
            raise WorkStateError(
                "Execução external por Work permanece bloqueada no 24E.2."
            )

        self._runtime_context_protocol(
            version
        )

        authority, grant = self._authorize_current_action(
            work_item=work_item,
            version_id=normalized_version_id,
            authority_user_id=normalized_authority_id,
            input_payload=normalized_input,
        )

        actor_reference = self._actor_reference(
            work_item.id
        )
        dispatch_key = self._dispatch_key(
            work_item.id,
            normalized_version_id,
        )

        existing = (
            self.execution_repository
            .get_by_work_item(
                work_item.id
            )
        )
        if existing is not None:
            self._validate_existing_configuration(
                existing=existing,
                version_id=normalized_version_id,
                authority_user_id=normalized_authority_id,
                actor_reference=actor_reference,
                dispatch_key=dispatch_key,
                execution_mode=grant.version.execution_mode,
                input_digest=input_digest,
            )

            if (
                existing.execution_mode == "mutating"
                and existing.status == "configured"
            ):
                existing = self._ensure_approval(
                    existing,
                    input_payload=normalized_input,
                )

            return self._result(
                existing,
                work_item=work_item,
                outcome=self._outcome_for_status(
                    existing.status
                ),
                duplicate=True,
            )

        execution = WorkSkillExecution(
            work_item_id=work_item.id,
            skill_version_id=grant.version.id,
            approval_request_id=None,
            approval_consumption_id=None,
            skill_invocation_id=None,
            authority_user_id=authority.id,
            authority_role=authority.role,
            actor_type="system",
            actor_reference=actor_reference,
            dispatch_key=dispatch_key,
            execution_mode=grant.version.execution_mode,
            input_digest=input_digest,
            status=(
                "ready"
                if grant.version.execution_mode
                == "read_only"
                else "configured"
            ),
            last_error_code=None,
            dispatch_attempts=0,
            started_at=None,
            finished_at=None,
        )

        try:
            self.execution_repository.add(
                execution
            )
            self.db.commit()
            self.db.refresh(
                execution
            )
        except IntegrityError as error:
            self.db.rollback()
            existing = (
                self.execution_repository
                .get_by_work_item(
                    work_item.id
                )
            )
            if existing is None:
                raise WorkConflictError(
                    "Conflito ao persistir execução de Work."
                ) from error
            self._validate_existing_configuration(
                existing=existing,
                version_id=normalized_version_id,
                authority_user_id=normalized_authority_id,
                actor_reference=actor_reference,
                dispatch_key=dispatch_key,
                execution_mode=grant.version.execution_mode,
                input_digest=input_digest,
            )
            execution = existing
        except Exception:
            self.db.rollback()
            raise

        if execution.execution_mode == "mutating":
            execution = self._ensure_approval(
                execution,
                input_payload=normalized_input,
            )

        log_work_skill_execution_event(
            "work.skill_execution.configured",
            work_item_id=work_item.id,
            work_skill_execution_id=execution.id,
            skill_version_id=execution.skill_version_id,
            authority_user_id=execution.authority_user_id,
            status=execution.status,
            dispatch_attempts=execution.dispatch_attempts,
        )

        return self._result(
            execution,
            work_item=work_item,
            outcome=self._outcome_for_status(
                execution.status
            ),
            duplicate=False,
        )

    def configure_with_existing_approval(
        self,
        work_item_id: int,
        *,
        version_id: int,
        authority_user_id: int,
        input_payload: Any,
        approval_request_id: int,
    ) -> WorkSkillExecutionResult:
        normalized_work_id = _positive_id(work_item_id, field_name="work_item_id")
        normalized_version_id = _positive_id(version_id, field_name="version_id")
        normalized_authority_id = _positive_id(
            authority_user_id, field_name="authority_user_id"
        )
        normalized_approval_id = _positive_id(
            approval_request_id, field_name="approval_request_id"
        )
        normalized_input, input_digest = approval_input_identity(input_payload)
        work_item = self.work_repository.lock_by_id(normalized_work_id)
        if work_item is None:
            raise WorkNotFoundError("Trabalho inexistente.")
        if work_item.status != "ready":
            raise WorkStateError("Somente Work ready aceita Approval 25M.")
        authority, grant = self._authorize_current_action(
            work_item=work_item,
            version_id=normalized_version_id,
            authority_user_id=normalized_authority_id,
            input_payload=normalized_input,
        )
        if grant.version.execution_mode != "mutating":
            raise WorkStateError("Approval 25M exige Skill mutating.")
        request = self.approval_repository.get_request(normalized_approval_id)
        if (
            request is None
            or request.status != "approved"
            or request.skill_version_id != grant.version.id
            or request.input_digest != input_digest
            or request.target_account_id != work_item.account_id
            or request.target_user_id != work_item.subject_user_id
            or request.requester_actor_type != "agent"
            or not request.requester_reference.startswith("agent:")
        ):
            raise WorkStateError("ApprovalRequest 25M diverge do Work/Skill/input.")
        actor_reference = self._actor_reference(work_item.id)
        dispatch_key = self._dispatch_key(work_item.id, normalized_version_id)
        existing = self.execution_repository.get_by_work_item(work_item.id)
        if existing is not None:
            self._validate_existing_configuration(
                existing=existing,
                version_id=normalized_version_id,
                authority_user_id=normalized_authority_id,
                actor_reference=actor_reference,
                dispatch_key=dispatch_key,
                execution_mode="mutating",
                input_digest=input_digest,
            )
            if existing.approval_request_id != normalized_approval_id:
                raise WorkConflictError("Work vinculado a outra ApprovalRequest.")
            if existing.status == "configured":
                existing.status = "ready"
                self.db.commit()
                self.db.refresh(existing)
            return self._result(
                existing,
                work_item=work_item,
                outcome=self._outcome_for_status(existing.status),
                duplicate=True,
            )
        execution = WorkSkillExecution(
            work_item_id=work_item.id,
            skill_version_id=grant.version.id,
            approval_request_id=normalized_approval_id,
            approval_consumption_id=None,
            skill_invocation_id=None,
            authority_user_id=authority.id,
            authority_role=authority.role,
            actor_type="system",
            actor_reference=actor_reference,
            dispatch_key=dispatch_key,
            execution_mode="mutating",
            input_digest=input_digest,
            status="ready",
            last_error_code=None,
            dispatch_attempts=0,
            started_at=None,
            finished_at=None,
        )
        try:
            self.execution_repository.add(execution)
            self.db.commit()
            self.db.refresh(execution)
        except IntegrityError as error:
            self.db.rollback()
            existing = self.execution_repository.get_by_work_item(work_item.id)
            if existing is None:
                raise WorkConflictError(
                    "Conflito ao persistir WorkSkillExecution 25O."
                ) from error
            self._validate_existing_configuration(
                existing=existing,
                version_id=normalized_version_id,
                authority_user_id=normalized_authority_id,
                actor_reference=actor_reference,
                dispatch_key=dispatch_key,
                execution_mode="mutating",
                input_digest=input_digest,
            )
            if existing.approval_request_id != normalized_approval_id:
                raise WorkConflictError(
                    "Conflito concorrente de ApprovalRequest 25O."
                ) from error
            execution = existing
        return self._result(
            execution,
            work_item=work_item,
            outcome=self._outcome_for_status(execution.status),
            duplicate=False,
        )

    def dispatch(
        self,
        work_item_id: int,
        *,
        input_payload: Any,
    ) -> WorkSkillExecutionResult:
        normalized_work_id = _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        normalized_input, input_digest = (
            approval_input_identity(
                input_payload
            )
        )

        execution = (
            self.execution_repository
            .lock_by_work_item(
                normalized_work_id
            )
        )
        if execution is None:
            raise WorkNotFoundError(
                "Execução governada não configurada para o Work."
            )

        work_item = self.work_repository.lock_by_id(
            normalized_work_id
        )
        if work_item is None:
            raise WorkNotFoundError(
                "Trabalho inexistente."
            )

        self._validate_server_identity(
            execution
        )
        if execution.input_digest != input_digest:
            raise WorkConflictError(
                "Payload atual diverge da ação Work configurada."
            )

        if execution.status == "configured":
            if execution.execution_mode != "mutating":
                raise WorkStateError(
                    "Estado configured inesperado para ação read_only."
                )
            execution = self._ensure_approval(
                execution,
                input_payload=normalized_input,
            )

        if execution.status == "approval_pending":
            refreshed = self._refresh_approval(
                execution,
                work_item=work_item,
            )
            if refreshed is not None:
                return refreshed
            execution = (
                self.execution_repository
                .lock_by_work_item(
                    normalized_work_id
                )
            )
            assert execution is not None

        if execution.status in {
            "succeeded",
            "failed",
            "timed_out",
            "cancelled",
        }:
            work_item = self._repair_work_from_terminal(
                execution,
                work_item,
            )
            return self._result(
                execution,
                work_item=work_item,
                outcome=self._outcome_for_status(
                    execution.status
                ),
                duplicate=True,
            )

        if execution.status != "ready":
            raise WorkStateError(
                "Execução Work não está pronta para dispatch."
            )

        existing_invocation = self._find_runtime_invocation(
            execution
        )
        if existing_invocation is not None:
            return self._reconcile_invocation(
                execution,
                work_item=work_item,
                invocation=existing_invocation,
                duplicate=True,
            )

        authority, grant = self._authorize_current_action(
            work_item=work_item,
            version_id=execution.skill_version_id,
            authority_user_id=execution.authority_user_id,
            input_payload=normalized_input,
        )
        runtime_context_protocol = self._runtime_context_protocol(
            grant.version
        )

        if work_item.status not in {
            "ready",
            "in_progress",
        }:
            raise WorkStateError(
                "Work não está em estado executável."
            )

        retry_after = self.limiter.consume(
            authority_user_id=authority.id
        )
        if retry_after is not None:
            log_work_skill_execution_event(
                "work.skill_execution.rate_limited",
                work_item_id=work_item.id,
                work_skill_execution_id=execution.id,
                skill_version_id=execution.skill_version_id,
                authority_user_id=authority.id,
                retry_after_seconds=retry_after,
                status=execution.status,
                outcome="rate_limited",
                dispatch_attempts=execution.dispatch_attempts,
            )
            return WorkSkillExecutionResult(
                execution=execution,
                work_item=work_item,
                outcome="rate_limited",
                duplicate=False,
                retry_after_seconds=retry_after,
            )

        runtime_context: WorkLearningRuntimeContext | None = None
        if runtime_context_protocol is not None:
            runtime_context = (
                self.learning_context_snapshot_service.get_or_create(
                    work_skill_execution_id=execution.id,
                    work_item_id=work_item.id,
                    skill_version_id=execution.skill_version_id,
                    authority_user_id=authority.id,
                )
            )

            execution = (
                self.execution_repository
                .lock_by_work_item(
                    normalized_work_id
                )
            )
            if execution is None:
                raise WorkNotFoundError(
                    "Execução governada não configurada para o Work."
                )
            work_item = self.work_repository.lock_by_id(
                normalized_work_id
            )
            if work_item is None:
                raise WorkNotFoundError(
                    "Trabalho inexistente."
                )

            self._validate_server_identity(
                execution
            )
            if execution.input_digest != input_digest:
                raise WorkConflictError(
                    "Payload atual diverge da ação Work configurada."
                )

            if execution.status in {
                "succeeded",
                "failed",
                "timed_out",
                "cancelled",
            }:
                work_item = self._repair_work_from_terminal(
                    execution,
                    work_item,
                )
                return self._result(
                    execution,
                    work_item=work_item,
                    outcome=self._outcome_for_status(
                        execution.status
                    ),
                    duplicate=True,
                )

            if execution.status != "ready":
                raise WorkStateError(
                    "Execução Work não está pronta para dispatch."
                )

            existing_invocation = self._find_runtime_invocation(
                execution
            )
            if existing_invocation is not None:
                return self._reconcile_invocation(
                    execution,
                    work_item=work_item,
                    invocation=existing_invocation,
                    duplicate=True,
                )

            authority, grant = self._authorize_current_action(
                work_item=work_item,
                version_id=execution.skill_version_id,
                authority_user_id=execution.authority_user_id,
                input_payload=normalized_input,
            )
            if (
                self._runtime_context_protocol(
                    grant.version
                )
                != runtime_context_protocol
            ):
                raise WorkConflictError(
                    "Runtime context declarado divergiu durante dispatch."
                )

            if work_item.status not in {
                "ready",
                "in_progress",
            }:
                raise WorkStateError(
                    "Work não está em estado executável."
                )

        if work_item.status == "ready":
            work_item = self.work_service.transition_status(
                work_item.id,
                expected_version=work_item.version,
                actor=self._work_actor(
                    work_item.id
                ),
                status="in_progress",
                idempotency_key=self._work_event_key(
                    work_item.id,
                    "start",
                ),
            ).work_item

        execution.dispatch_attempts += 1
        if execution.started_at is None:
            execution.started_at = _utc_now()
        try:
            self.db.commit()
            self.db.refresh(
                execution
            )
        except Exception:
            self.db.rollback()
            raise

        actor = self._skill_actor(
            work_item.id
        )

        try:
            governed_kwargs = {
                "actor": actor,
                "authority_user_id": authority.id,
                "input_payload": normalized_input,
                "idempotency_key": (
                    execution.dispatch_key
                    if execution.execution_mode
                    == "read_only"
                    else None
                ),
                "approval_request_id": (
                    execution.approval_request_id
                    if execution.execution_mode
                    == "mutating"
                    else None
                ),
            }
            if runtime_context is not None:
                governed_kwargs["runtime_context"] = (
                    runtime_context.payload
                )

            governed_result = self.governed_service.execute(
                execution.skill_version_id,
                **governed_kwargs,
            )
        except SkillInvocationInProgressError:
            invocation = self._find_runtime_invocation(
                execution
            )
            if invocation is not None:
                self._attach_invocation(
                    execution,
                    invocation,
                )
            return self._result(
                execution,
                work_item=work_item,
                outcome="in_progress",
                duplicate=True,
            )
        except SkillExecutionTimeoutError as error:
            return self._finalize_error(
                execution,
                work_item=work_item,
                error=error,
                status="timed_out",
            )
        except (
            SkillRuntimeError,
            ApprovalError,
        ) as error:
            return self._finalize_error(
                execution,
                work_item=work_item,
                error=error,
                status="failed",
            )
        except Exception as error:
            result = self._finalize_error(
                execution,
                work_item=work_item,
                error=error,
                status="failed",
            )
            raise RuntimeError(
                "Falha inesperada na execução governada de Work."
            ) from error

        return self._finalize_success(
            execution,
            work_item=work_item,
            governed_result=governed_result,
        )

    def reconcile(
        self,
        work_item_id: int,
    ) -> WorkSkillExecutionResult:
        normalized_work_id = _positive_id(
            work_item_id,
            field_name="work_item_id",
        )
        execution = (
            self.execution_repository
            .lock_by_work_item(
                normalized_work_id
            )
        )
        if execution is None:
            raise WorkNotFoundError(
                "Execução governada não configurada para o Work."
            )
        work_item = self.work_repository.lock_by_id(
            normalized_work_id
        )
        if work_item is None:
            raise WorkNotFoundError(
                "Trabalho inexistente."
            )

        self._validate_server_identity(
            execution
        )

        if execution.status == "approval_pending":
            refreshed = self._refresh_approval(
                execution,
                work_item=work_item,
            )
            if refreshed is not None:
                return refreshed
            execution = (
                self.execution_repository
                .lock_by_work_item(
                    normalized_work_id
                )
            )
            assert execution is not None

        if execution.status in {
            "succeeded",
            "failed",
            "timed_out",
            "cancelled",
        }:
            work_item = self._repair_work_from_terminal(
                execution,
                work_item,
            )
            return self._result(
                execution,
                work_item=work_item,
                outcome=self._outcome_for_status(
                    execution.status
                ),
                duplicate=True,
            )

        if execution.status == "configured":
            return self._result(
                execution,
                work_item=work_item,
                outcome="configuration_retry_required",
                duplicate=True,
            )

        invocation = self._find_runtime_invocation(
            execution
        )
        if invocation is not None:
            return self._reconcile_invocation(
                execution,
                work_item=work_item,
                invocation=invocation,
                duplicate=True,
            )

        if work_item.status == "in_progress":
            return self._result(
                execution,
                work_item=work_item,
                outcome="retry_required",
                duplicate=True,
            )

        return self._result(
            execution,
            work_item=work_item,
            outcome="ready",
            duplicate=True,
        )

    def _ensure_approval(
        self,
        execution: WorkSkillExecution,
        *,
        input_payload: Any,
    ) -> WorkSkillExecution:
        if execution.execution_mode != "mutating":
            raise WorkStateError(
                "Somente ação mutating usa Approval no 24E.2."
            )

        if execution.approval_request_id is not None:
            if execution.status == "configured":
                execution.status = "approval_pending"
                self.db.commit()
                self.db.refresh(execution)
            return execution

        created = (
            self.approval_service
            .create_skill_execution_request(
                version_id=execution.skill_version_id,
                requester=ApprovalRequester(
                    actor_type="system",
                    actor_reference=execution.actor_reference,
                ),
                input_payload=input_payload,
                idempotency_key=self._approval_key(
                    execution.work_item_id
                ),
            )
        )

        execution = (
            self.execution_repository
            .lock_by_work_item(
                execution.work_item_id
            )
        )
        if execution is None:
            raise WorkStateError(
                "Ledger Work desapareceu durante criação de Approval."
            )

        if (
            execution.approval_request_id is not None
            and execution.approval_request_id
            != created.request.id
        ):
            raise WorkConflictError(
                "Work já está vinculado a outra Approval."
            )

        execution.approval_request_id = (
            created.request.id
        )
        execution.status = "approval_pending"

        try:
            self.db.commit()
            self.db.refresh(
                execution
            )
        except Exception:
            self.db.rollback()
            raise

        return execution

    def _refresh_approval(
        self,
        execution: WorkSkillExecution,
        *,
        work_item: WorkItem,
    ) -> WorkSkillExecutionResult | None:
        if execution.approval_request_id is None:
            raise WorkStateError(
                "approval_pending sem ApprovalRequest."
            )

        request = self.approval_repository.get_request(
            execution.approval_request_id
        )
        if request is None:
            raise WorkStateError(
                "ApprovalRequest vinculada não foi encontrada."
            )

        if request.status == "pending":
            return self._result(
                execution,
                work_item=work_item,
                outcome="approval_pending",
                duplicate=True,
            )

        if (
            request.status == "approved"
            and request.expires_at > _utc_now()
        ):
            execution.status = "ready"
            try:
                self.db.commit()
                self.db.refresh(
                    execution
                )
            except Exception:
                self.db.rollback()
                raise
            return None

        return self._finalize_cancelled(
            execution,
            work_item=work_item,
            error_code=(
                "approval_expired"
                if request.status == "approved"
                else "approval_" + request.status
            ),
        )

    @staticmethod
    def _runtime_context_protocol(
        version,
    ) -> str | None:
        manifest = (
            version.manifest
            if isinstance(version.manifest, dict)
            else {}
        )
        declared = manifest.get(
            "runtime_context_protocol"
        )
        if declared is None:
            return None

        if (
            declared
            != WORK_LEARNING_RUNTIME_CONTEXT_PROTOCOL
        ):
            raise WorkStateError(
                "Work Skill declarou runtime context protocol não suportado."
            )

        if (
            version.runtime_kind != "internal_python"
            or version.execution_mode != "read_only"
        ):
            raise WorkStateError(
                "Work learning runtime context exige "
                "internal_python read_only."
            )

        return declared

    def _authorize_current_action(
        self,
        *,
        work_item: WorkItem,
        version_id: int,
        authority_user_id: int | None,
        input_payload: Any,
    ) -> tuple[User, SkillExecutionGrant]:
        if authority_user_id is None:
            raise WorkStateError(
                "Usuário-principal da execução não está disponível."
            )

        authority = self.db.get(
            User,
            authority_user_id,
        )
        if authority is None or not authority.active:
            raise WorkStateError(
                "Usuário-principal inexistente ou inativo."
            )

        grant = authorize_skill_execution(
            db=self.db,
            role=authority.role,
            actor_user_id=authority.id,
            session_elevated=False,
            version_id=version_id,
            input_payload=input_payload,
        )

        if grant.version.execution_mode == "external":
            raise WorkStateError(
                "Execução external por Work permanece bloqueada no 24E.2."
            )

        self._validate_work_scope(
            work_item=work_item,
            grant=grant,
        )
        return authority, grant

    @staticmethod
    def _validate_work_scope(
        *,
        work_item: WorkItem,
        grant: SkillExecutionGrant,
    ) -> None:
        if work_item.scope_type == "global":
            valid = (
                grant.account_id is None
                and grant.subject_user_id is None
            )
        elif work_item.scope_type == "account":
            valid = (
                grant.account_id
                == work_item.account_id
                and grant.subject_user_id is None
            )
        elif work_item.scope_type == "user":
            valid = (
                grant.account_id is None
                and grant.subject_user_id
                == work_item.subject_user_id
            )
        else:
            valid = False

        if not valid:
            raise WorkStateError(
                "Escopo da ação Skill diverge do escopo do Work."
            )

    @staticmethod
    def _validate_existing_configuration(
        *,
        existing: WorkSkillExecution,
        version_id: int,
        authority_user_id: int,
        actor_reference: str,
        dispatch_key: str,
        execution_mode: str,
        input_digest: str,
    ) -> None:
        if (
            existing.skill_version_id != version_id
            or existing.authority_user_id
            != authority_user_id
            or existing.actor_type != "system"
            or existing.actor_reference
            != actor_reference
            or existing.dispatch_key
            != dispatch_key
            or existing.execution_mode
            != execution_mode
            or existing.input_digest
            != input_digest
        ):
            raise WorkConflictError(
                "Work já possui outra ação Skill configurada."
            )

    def _find_runtime_invocation(
        self,
        execution: WorkSkillExecution,
    ) -> SkillInvocation | None:
        runtime_key = self._runtime_key(
            execution
        )
        if runtime_key is None:
            return None

        return (
            self.skill_repository
            .find_invocation_by_idempotency(
                version_id=execution.skill_version_id,
                actor_type="system",
                actor_reference=execution.actor_reference,
                idempotency_key=runtime_key,
            )
        )

    def _runtime_key(
        self,
        execution: WorkSkillExecution,
    ) -> str | None:
        if execution.execution_mode == "read_only":
            return execution.dispatch_key
        if (
            execution.execution_mode == "mutating"
            and execution.approval_request_id is not None
        ):
            return (
                f"approval:{execution.approval_request_id}"
            )
        return None

    def _reconcile_invocation(
        self,
        execution: WorkSkillExecution,
        *,
        work_item: WorkItem,
        invocation: SkillInvocation,
        duplicate: bool,
    ) -> WorkSkillExecutionResult:
        self._attach_invocation(
            execution,
            invocation,
        )

        if invocation.status == "running":
            return self._result(
                execution,
                work_item=work_item,
                outcome="in_progress",
                duplicate=duplicate,
            )
        if invocation.status == "succeeded":
            return self._finalize_success_from_invocation(
                execution,
                work_item=work_item,
                invocation=invocation,
                duplicate=duplicate,
            )
        if invocation.status == "timed_out":
            return self._finalize_terminal_from_invocation(
                execution,
                work_item=work_item,
                invocation=invocation,
                status="timed_out",
                error_code="skill_timed_out",
                duplicate=duplicate,
            )
        return self._finalize_terminal_from_invocation(
            execution,
            work_item=work_item,
            invocation=invocation,
            status="failed",
            error_code="skill_runtime_failed",
            duplicate=duplicate,
        )

    def _finalize_success(
        self,
        execution: WorkSkillExecution,
        *,
        work_item: WorkItem,
        governed_result: GovernedSkillExecutionResult,
    ) -> WorkSkillExecutionResult:
        invocation = (
            governed_result.invocation.invocation
        )
        execution.skill_invocation_id = invocation.id
        execution.approval_consumption_id = (
            governed_result.approval_consumption_id
        )
        execution.status = "succeeded"
        execution.last_error_code = None
        execution.finished_at = _utc_now()

        try:
            self.db.commit()
            self.db.refresh(
                execution
            )
        except Exception:
            self.db.rollback()
            raise

        work_item = self._complete_work(
            work_item
        )
        self._evaluate_terminal_outcome_best_effort(
            execution
        )

        return self._result(
            execution,
            work_item=work_item,
            outcome="succeeded",
            duplicate=(
                governed_result.invocation.duplicate
            ),
        )

    def _finalize_success_from_invocation(
        self,
        execution: WorkSkillExecution,
        *,
        work_item: WorkItem,
        invocation: SkillInvocation,
        duplicate: bool,
    ) -> WorkSkillExecutionResult:
        execution.skill_invocation_id = invocation.id
        self._attach_consumption(
            execution
        )
        execution.status = "succeeded"
        execution.last_error_code = None
        execution.finished_at = (
            invocation.finished_at
            or _utc_now()
        )
        try:
            self.db.commit()
            self.db.refresh(
                execution
            )
        except Exception:
            self.db.rollback()
            raise
        work_item = self._complete_work(
            work_item
        )
        self._evaluate_terminal_outcome_best_effort(
            execution
        )
        return self._result(
            execution,
            work_item=work_item,
            outcome="succeeded",
            duplicate=duplicate,
        )

    def _finalize_error(
        self,
        execution: WorkSkillExecution,
        *,
        work_item: WorkItem,
        error: Exception,
        status: Literal[
            "failed",
            "timed_out",
        ],
    ) -> WorkSkillExecutionResult:
        invocation = self._find_runtime_invocation(
            execution
        )
        if invocation is not None:
            execution.skill_invocation_id = (
                invocation.id
            )
        self._attach_consumption(
            execution
        )
        execution.status = status
        execution.last_error_code = (
            _safe_error_code(
                error
            )
        )
        execution.finished_at = _utc_now()

        try:
            self.db.commit()
            self.db.refresh(
                execution
            )
        except Exception:
            self.db.rollback()
            raise

        work_item = self._block_work(
            work_item,
            timeout=(
                status == "timed_out"
            ),
        )
        self._evaluate_terminal_outcome_best_effort(
            execution
        )

        return self._result(
            execution,
            work_item=work_item,
            outcome=status,
            duplicate=False,
        )

    def _finalize_terminal_from_invocation(
        self,
        execution: WorkSkillExecution,
        *,
        work_item: WorkItem,
        invocation: SkillInvocation,
        status: Literal[
            "failed",
            "timed_out",
        ],
        error_code: str,
        duplicate: bool,
    ) -> WorkSkillExecutionResult:
        execution.skill_invocation_id = invocation.id
        self._attach_consumption(
            execution
        )
        execution.status = status
        execution.last_error_code = error_code
        execution.finished_at = (
            invocation.finished_at
            or _utc_now()
        )
        try:
            self.db.commit()
            self.db.refresh(
                execution
            )
        except Exception:
            self.db.rollback()
            raise

        work_item = self._block_work(
            work_item,
            timeout=(
                status == "timed_out"
            ),
        )
        self._evaluate_terminal_outcome_best_effort(
            execution
        )
        return self._result(
            execution,
            work_item=work_item,
            outcome=status,
            duplicate=duplicate,
        )

    def _finalize_cancelled(
        self,
        execution: WorkSkillExecution,
        *,
        work_item: WorkItem,
        error_code: str,
    ) -> WorkSkillExecutionResult:
        execution.status = "cancelled"
        execution.last_error_code = error_code
        execution.finished_at = _utc_now()
        try:
            self.db.commit()
            self.db.refresh(
                execution
            )
        except Exception:
            self.db.rollback()
            raise

        if work_item.status not in {
            "cancelled",
            "completed",
        }:
            work_item = self.work_service.transition_status(
                work_item.id,
                expected_version=work_item.version,
                actor=self._work_actor(
                    work_item.id
                ),
                status="cancelled",
                reason="Execução governada cancelada.",
                idempotency_key=self._work_event_key(
                    work_item.id,
                    "cancelled",
                ),
            ).work_item

        self._evaluate_terminal_outcome_best_effort(
            execution
        )
        return self._result(
            execution,
            work_item=work_item,
            outcome="cancelled",
            duplicate=True,
        )

    def _evaluate_terminal_outcome_best_effort(
        self,
        execution: WorkSkillExecution,
    ) -> None:
        try:
            self.outcome_evaluation_service.evaluate(
                execution.id
            )
        except Exception:
            self.db.rollback()
            log_work_outcome_evaluation_event(
                "work.outcome_evaluation.best_effort_failed",
                work_item_id=execution.work_item_id,
                work_skill_execution_id=execution.id,
                terminal_status=execution.status,
                error_code="outcome_evaluation_retry_required",
                outcome="retry_required",
            )

    def _attach_invocation(
        self,
        execution: WorkSkillExecution,
        invocation: SkillInvocation,
    ) -> None:
        if (
            execution.skill_invocation_id is not None
            and execution.skill_invocation_id
            != invocation.id
        ):
            raise WorkConflictError(
                "Work está vinculado a outra SkillInvocation."
            )
        execution.skill_invocation_id = (
            invocation.id
        )
        self._attach_consumption(
            execution
        )
        try:
            self.db.commit()
            self.db.refresh(
                execution
            )
        except Exception:
            self.db.rollback()
            raise

    def _attach_consumption(
        self,
        execution: WorkSkillExecution,
    ) -> None:
        if execution.approval_request_id is None:
            return
        consumption = (
            self.approval_repository
            .get_consumption_by_request(
                execution.approval_request_id
            )
        )
        if consumption is None:
            return
        if (
            execution.approval_consumption_id
            is not None
            and execution.approval_consumption_id
            != consumption.id
        ):
            raise WorkConflictError(
                "Work está vinculado a outro ApprovalConsumption."
            )
        execution.approval_consumption_id = (
            consumption.id
        )

    def _complete_work(
        self,
        work_item: WorkItem,
    ) -> WorkItem:
        if work_item.status == "completed":
            return work_item
        if work_item.status != "in_progress":
            raise WorkStateError(
                "Resultado succeeded exige Work in_progress."
            )
        return self.work_service.transition_status(
            work_item.id,
            expected_version=work_item.version,
            actor=self._work_actor(
                work_item.id
            ),
            status="completed",
            idempotency_key=self._work_event_key(
                work_item.id,
                "succeeded",
            ),
        ).work_item

    def _block_work(
        self,
        work_item: WorkItem,
        *,
        timeout: bool,
    ) -> WorkItem:
        if work_item.status == "blocked":
            return work_item
        if work_item.status != "in_progress":
            raise WorkStateError(
                "Falha terminal exige Work in_progress."
            )
        reason = (
            "Execução governada excedeu o tempo limite."
            if timeout
            else "Execução governada falhou."
        )
        return self.work_service.transition_status(
            work_item.id,
            expected_version=work_item.version,
            actor=self._work_actor(
                work_item.id
            ),
            status="blocked",
            reason=reason,
            idempotency_key=self._work_event_key(
                work_item.id,
                "timed-out"
                if timeout
                else "failed",
            ),
        ).work_item

    def _repair_work_from_terminal(
        self,
        execution: WorkSkillExecution,
        work_item: WorkItem,
    ) -> WorkItem:
        if execution.status == "succeeded":
            return self._complete_work(
                work_item
            )
        if execution.status == "failed":
            return self._block_work(
                work_item,
                timeout=False,
            )
        if execution.status == "timed_out":
            return self._block_work(
                work_item,
                timeout=True,
            )
        if execution.status == "cancelled":
            if work_item.status == "cancelled":
                return work_item
            if work_item.status == "completed":
                raise WorkStateError(
                    "Work completed diverge de execução cancelada."
                )
            return self.work_service.transition_status(
                work_item.id,
                expected_version=work_item.version,
                actor=self._work_actor(
                    work_item.id
                ),
                status="cancelled",
                reason="Execução governada cancelada.",
                idempotency_key=self._work_event_key(
                    work_item.id,
                    "cancelled",
                ),
            ).work_item
        return work_item

    def _validate_server_identity(
        self,
        execution: WorkSkillExecution,
    ) -> None:
        expected_reference = self._actor_reference(
            execution.work_item_id
        )
        expected_dispatch_key = self._dispatch_key(
            execution.work_item_id,
            execution.skill_version_id,
        )
        if (
            execution.actor_type != "system"
            or execution.actor_reference
            != expected_reference
            or execution.dispatch_key
            != expected_dispatch_key
        ):
            raise WorkStateError(
                "Identidade server-side do ledger Work é inválida."
            )
        if execution.execution_mode == "external":
            raise WorkStateError(
                "Execução external por Work permanece bloqueada no 24E.2."
            )

    @staticmethod
    def _actor_reference(
        work_item_id: int,
    ) -> str:
        return f"system:work:{work_item_id}"

    @staticmethod
    def _dispatch_key(
        work_item_id: int,
        version_id: int,
    ) -> str:
        return (
            f"work:{work_item_id}:skill:{version_id}"
        )

    @staticmethod
    def _approval_key(
        work_item_id: int,
    ) -> str:
        return (
            f"work:{work_item_id}:approval"
        )

    @staticmethod
    def _work_event_key(
        work_item_id: int,
        suffix: str,
    ) -> str:
        return (
            f"work:{work_item_id}:skill:{suffix}"
        )

    @classmethod
    def _skill_actor(
        cls,
        work_item_id: int,
    ) -> SkillInvocationActor:
        return SkillInvocationActor(
            actor_type="system",
            actor_reference=cls._actor_reference(
                work_item_id
            ),
        )

    @classmethod
    def _work_actor(
        cls,
        work_item_id: int,
    ) -> WorkActor:
        return WorkActor(
            actor_type="system",
            actor_reference=cls._actor_reference(
                work_item_id
            ),
        )

    @staticmethod
    def _outcome_for_status(
        status: str,
    ) -> WorkSkillExecutionOutcome:
        mapping: dict[
            str,
            WorkSkillExecutionOutcome,
        ] = {
            "configured": "configuration_retry_required",
            "approval_pending": "approval_pending",
            "ready": "ready",
            "succeeded": "succeeded",
            "failed": "failed",
            "timed_out": "timed_out",
            "cancelled": "cancelled",
        }
        try:
            return mapping[status]
        except KeyError as error:
            raise WorkStateError(
                "Estado de execução Work inválido."
            ) from error

    @staticmethod
    def _result(
        execution: WorkSkillExecution,
        *,
        work_item: WorkItem,
        outcome: WorkSkillExecutionOutcome,
        duplicate: bool,
    ) -> WorkSkillExecutionResult:
        log_work_skill_execution_event(
            "work.skill_execution.result",
            work_item_id=work_item.id,
            work_skill_execution_id=execution.id,
            skill_version_id=execution.skill_version_id,
            authority_user_id=execution.authority_user_id,
            approval_request_id=execution.approval_request_id,
            skill_invocation_id=execution.skill_invocation_id,
            status=execution.status,
            outcome=outcome,
            duplicate=duplicate,
            dispatch_attempts=execution.dispatch_attempts,
            error_code=execution.last_error_code,
        )
        return WorkSkillExecutionResult(
            execution=execution,
            work_item=work_item,
            outcome=outcome,
            duplicate=duplicate,
        )
