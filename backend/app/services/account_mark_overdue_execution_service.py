from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.pilot_mutation_errors import PilotMutationAuthorizationError
from app.core.pilot_mutation_errors import PilotMutationConflictError
from app.core.pilot_mutation_errors import PilotMutationStateError
from app.core.pilot_mutation_errors import PilotMutationValidationError
from app.models.account import Account
from app.models.approval import ApprovalConsumption
from app.models.skill import SkillInvocation
from app.models.work import WorkEvent
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.skill_repository import SkillRepository
from app.repositories.work_repository import WorkRepository
from app.repositories.work_skill_execution_repository import WorkSkillExecutionRepository
from app.services.governed_skill_execution import GovernedSkillExecutionService
from app.services.skill_runtime import SkillInvocationActor
from app.services.skill_runtime import _canonical_json, _digest_bytes, _fingerprint
from app.services.work_service import WorkActor, WorkManagerService

PILOT_SKILL_KEY = "account.mark_overdue"
PILOT_PROVIDER = "auneron.core"
PILOT_HANDLER_REFERENCE = "app.skills.account:mark_overdue"
PILOT_CAPABILITY_KEY = "account.status.mark_overdue"
PILOT_PROTOCOL = "auneron.pilot.account_mark_overdue.v1"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AccountMarkOverdueExecutionResult:
    work_item_id: int
    work_skill_execution_id: int
    approval_request_id: int
    approval_consumption_id: int
    invocation_id: int
    invocation_status: str
    duplicate: bool
    output: dict[str, Any]


class AccountMarkOverdueExecutionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.approvals = ApprovalRepository(db)
        self.skills = SkillRepository(db)
        self.works = WorkRepository(db)
        self.executions = WorkSkillExecutionRepository(db)
        self.governed = GovernedSkillExecutionService(db)
        self.work_service = WorkManagerService(db)

    @staticmethod
    def _receipt_key(approval_request_id: int) -> str:
        return f"effect:account.mark_overdue:approval:{approval_request_id}"

    def _validate_catalog(self, validation) -> None:
        skill = validation.skill
        version = validation.version
        if (
            skill.skill_key != PILOT_SKILL_KEY
            or skill.provider != PILOT_PROVIDER
            or version.runtime_kind != "internal_python"
            or version.execution_mode != "mutating"
            or version.handler_reference != PILOT_HANDLER_REFERENCE
        ):
            raise PilotMutationAuthorizationError("Pilot catalog mismatch.")
        matches = [
            c for c in validation.capabilities
            if c.capability_key == PILOT_CAPABILITY_KEY
            and c.access_mode == "write"
            and c.resource_scope == "account"
        ]
        if len(matches) != 1:
            raise PilotMutationAuthorizationError("Required capability missing/ambiguous.")
        if any(c.resource_scope == "external" for c in validation.capabilities):
            raise PilotMutationAuthorizationError("External capability forbidden.")

    def execute(self, *, work_item_id: int, approval_request_id: int,
                authority_user_id: int, actor_reference: str,
                input_payload: Any) -> AccountMarkOverdueExecutionResult:
        if not isinstance(actor_reference, str) or not actor_reference.startswith("agent:"):
            raise PilotMutationValidationError("actor_reference must be agent:<name>.")
        actor = SkillInvocationActor(actor_type="agent", actor_reference=actor_reference, actor_user_id=None)
        execution = self.executions.lock_by_work_item(work_item_id)
        work_item = self.works.lock_by_id(work_item_id)
        if execution is None or work_item is None:
            raise PilotMutationStateError("Pilot Work/WSE missing.")
        if work_item.status not in {"ready", "in_progress"}:
            raise PilotMutationStateError("Pilot Work must be ready/in_progress.")
        if execution.execution_mode != "mutating" or execution.approval_request_id != approval_request_id:
            raise PilotMutationConflictError("WSE/Approval mismatch.")

        validation = self.governed.validate_approved_action_only(
            execution.skill_version_id,
            actor=actor,
            authority_user_id=authority_user_id,
            input_payload=input_payload,
            approval_request_id=approval_request_id,
        )
        self._validate_catalog(validation)
        if validation.grant.account_id is None or work_item.scope_type != "account" or work_item.account_id != validation.grant.account_id or work_item.subject_user_id is not None:
            raise PilotMutationAuthorizationError("Work scope mismatch.")
        if execution.input_digest != validation.input_digest:
            raise PilotMutationConflictError("WSE input digest mismatch.")
        context = work_item.context_data if isinstance(work_item.context_data, dict) else {}
        if context.get("protocol") != PILOT_PROTOCOL or context.get("action_type") != PILOT_SKILL_KEY or context.get("approval_request_id") != approval_request_id or context.get("skill_version_id") != execution.skill_version_id or context.get("input_digest") != validation.input_digest:
            raise PilotMutationConflictError("Work context mismatch.")
        normalized_input = validation.normalized_input
        if not isinstance(normalized_input, dict) or set(normalized_input) != {"account_id", "expected_status", "expected_due_date"}:
            raise PilotMutationValidationError("Frozen input shape mismatch.")
        if normalized_input["expected_status"] != "aberto" or normalized_input["account_id"] != work_item.account_id:
            raise PilotMutationConflictError("Frozen input values mismatch.")
        try:
            expected_due = date.fromisoformat(normalized_input["expected_due_date"])
        except Exception as error:
            raise PilotMutationValidationError("expected_due_date invalid.") from error
        if context.get("expected_due_date") != expected_due.isoformat():
            raise PilotMutationConflictError("Durable due date mismatch.")

        receipt_key = self._receipt_key(approval_request_id)
        receipt = self.works.find_event_by_idempotency_key(work_item_id=work_item.id, idempotency_key=receipt_key)
        if receipt is not None:
            account = self.db.get(Account, work_item.account_id)
            invocation = self.db.get(SkillInvocation, execution.skill_invocation_id) if execution.skill_invocation_id is not None else None
            if account is None or account.status != "atrasado" or execution.status != "succeeded" or execution.approval_consumption_id is None or invocation is None or invocation.status != "succeeded":
                raise PilotMutationConflictError("Receipt/terminal ledgers diverge.")
            output = invocation.output_payload if isinstance(invocation.output_payload, dict) else {}
            return AccountMarkOverdueExecutionResult(work_item.id, execution.id, approval_request_id, execution.approval_consumption_id, invocation.id, invocation.status, True, output)

        if work_item.status == "ready":
            work_item = self.work_service.transition_status(
                work_item.id,
                expected_version=work_item.version,
                actor=WorkActor(actor_type="system", actor_reference=f"system:work:{work_item.id}", actor_user_id=None),
                status="in_progress",
                idempotency_key=f"work:{work_item.id}:pilot:in_progress",
            ).work_item
            execution = self.executions.lock_by_work_item(work_item.id)
            if execution is None:
                raise PilotMutationStateError("WSE disappeared after transition.")

        account = self.db.execute(select(Account).where(Account.id == work_item.account_id).with_for_update()).scalar_one_or_none()
        if account is None:
            raise PilotMutationStateError("Account missing.")
        if account.status == "atrasado":
            raise PilotMutationConflictError("Account already atrasado without pilot receipt.")
        if account.status != "aberto":
            raise PilotMutationStateError("Only aberto may transition.")
        if account.vencimento != expected_due:
            raise PilotMutationConflictError("Due date changed after Approval.")
        if account.vencimento >= date.today():
            raise PilotMutationStateError("Account is not overdue.")

        runtime_key = f"approval:{approval_request_id}"
        if self.approvals.get_consumption_by_request(approval_request_id) is not None:
            raise PilotMutationConflictError("Consumption exists without receipt.")
        if self.skills.find_invocation_by_idempotency(version_id=validation.version.id, actor_type="agent", actor_reference=actor_reference, idempotency_key=runtime_key) is not None:
            raise PilotMutationConflictError("Invocation exists without receipt.")
        now = _utc_now()
        output = {"action": PILOT_SKILL_KEY, "account_id": account.id, "previous_status": "aberto", "new_status": "atrasado", "changed": True}
        normalized_output, output_bytes = _canonical_json(output, field_name="output", max_bytes=validation.version.max_output_bytes)
        invocation = SkillInvocation(
            skill_version_id=validation.version.id,
            actor_type="agent", actor_reference=actor_reference, actor_user_id=None,
            idempotency_key=runtime_key,
            request_fingerprint=_fingerprint(version=validation.version, actor=actor, normalized_input=validation.normalized_input),
            input_digest=validation.input_digest,
            status="succeeded", output_payload=normalized_output,
            output_digest=_digest_bytes(output_bytes), output_bytes=len(output_bytes),
            error_code=None, duration_ms=0, started_at=now, finished_at=now,
        )
        try:
            self.skills.add_invocation(invocation)
            consumption = ApprovalConsumption(
                approval_request_id=validation.request.id,
                approval_decision_id=validation.decision.id,
                skill_invocation_id=invocation.id,
                consumer_actor_type="agent", consumer_reference=actor_reference,
                authority_user_id=validation.authority.id,
                authority_reference=f"user:{validation.authority.id}",
                authority_role=validation.authority.role,
                runtime_idempotency_key=runtime_key,
                request_fingerprint=validation.request.request_fingerprint,
                input_digest=validation.input_digest,
                status="consumed", error_code=None,
                reserved_at=now, finalized_at=now,
            )
            self.approvals.add_consumption(consumption)
            account.status = "atrasado"
            execution.approval_consumption_id = consumption.id
            execution.skill_invocation_id = invocation.id
            execution.status = "succeeded"
            execution.last_error_code = None
            execution.dispatch_attempts = execution.dispatch_attempts + 1
            execution.started_at = execution.started_at or now
            execution.finished_at = now
            self.works.add_event(WorkEvent(
                work_item_id=work_item.id, event_type="system_note",
                actor_type="agent", actor_reference=actor_reference, actor_user_id=None,
                idempotency_key=receipt_key,
                event_data={"kind": "business_effect_receipt", "protocol": PILOT_PROTOCOL, "action": PILOT_SKILL_KEY, "account_id": account.id, "approval_request_id": validation.request.id, "skill_invocation_id": invocation.id, "input_digest": validation.input_digest, "previous_status": "aberto", "new_status": "atrasado", "expected_due_date": expected_due.isoformat()},
                created_at=now,
            ))
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()
            raise PilotMutationConflictError("Concurrent pilot effect conflict.") from error
        except Exception:
            self.db.rollback()
            raise

        fresh_work = self.works.lock_by_id(work_item.id)
        if fresh_work is not None and fresh_work.status == "in_progress":
            self.work_service.transition_status(
                fresh_work.id,
                expected_version=fresh_work.version,
                actor=WorkActor(actor_type="system", actor_reference=f"system:work:{fresh_work.id}", actor_user_id=None),
                status="completed",
                idempotency_key=f"work:{fresh_work.id}:skill:succeeded",
            )
        return AccountMarkOverdueExecutionResult(work_item.id, execution.id, approval_request_id, consumption.id, invocation.id, "succeeded", False, output)
