from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from sqlalchemy.orm import Session

from app.core.advisory_proposal_errors import AdvisoryProposalApprovalCorrelationError
from app.core.pilot_mutation_errors import PilotMutationAuthorizationError
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.skill_repository import SkillRepository
from app.services.account_mark_overdue_execution_service import AccountMarkOverdueExecutionService
from app.services.approval_service import approval_input_identity
from app.services.authenticated_advisory_proposal_consumption_service import AuthenticatedAdvisoryProposalConsumptionService
from app.services.work_service import WorkActor, WorkManagerService
from app.services.work_skill_execution import WorkSkillExecutionService

PILOT_PROTOCOL = "auneron.pilot.account_mark_overdue.v1"
PILOT_SKILL_KEY = "account.mark_overdue"


@dataclass(frozen=True)
class AuthenticatedAdvisoryProposalWorkMaterializationResult:
    work_item_id: int
    work_skill_execution_id: int
    approval_consumption_id: int
    invocation_id: int
    invocation_status: str
    duplicate: bool
    output: Any


class AuthenticatedAdvisoryProposalWorkMaterializationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.consumption = AuthenticatedAdvisoryProposalConsumptionService(db)
        self.approvals = ApprovalRepository(db)
        self.skills = SkillRepository(db)
        self.work = WorkManagerService(db)
        self.work_execution = WorkSkillExecutionService(db)
        self.effect = AccountMarkOverdueExecutionService(db)

    def materialize_and_execute(self, *, proposal_id: int, authenticated, binding_id: int, input_payload: Any, approval_request_id: int) -> AuthenticatedAdvisoryProposalWorkMaterializationResult:
        normalized_input, input_digest = approval_input_identity(input_payload)
        candidate = self.consumption.validate(proposal_id=proposal_id, authenticated=authenticated, binding_id=binding_id, input_payload=normalized_input)
        if candidate.execution_mode != "mutating" or candidate.runtime_kind != "internal_python" or candidate.account_id is None or candidate.subject_user_id is not None:
            raise PilotMutationAuthorizationError("Pilot candidate shape invalid.")
        skill = self.skills.get_skill(candidate.skill_id)
        if skill is None or skill.skill_key != PILOT_SKILL_KEY:
            raise PilotMutationAuthorizationError("Only account.mark_overdue is allowed.")
        request = self.approvals.get_request(approval_request_id)
        if request is None:
            raise AdvisoryProposalApprovalCorrelationError("ApprovalRequest not found.")
        actor_reference = f"agent:{candidate.agent_name}"
        expected_key = f"advisory:{candidate.proposal_id}:{candidate.binding_id}"
        if request.status != "approved" or request.idempotency_key != expected_key or request.requester_actor_type != "agent" or request.requester_reference != actor_reference or request.requester_user_id is not None or request.skill_version_id != candidate.skill_version_id or request.input_digest != input_digest or request.target_account_id != candidate.account_id or request.target_user_id is not None:
            raise AdvisoryProposalApprovalCorrelationError("ApprovalRequest mismatch.")
        if not isinstance(normalized_input, dict) or not isinstance(normalized_input.get("expected_due_date"), str):
            raise PilotMutationAuthorizationError("expected_due_date required.")
        work_key = f"advisory:{candidate.proposal_id}:binding:{candidate.binding_id}"
        context = {"protocol": PILOT_PROTOCOL, "action_type": PILOT_SKILL_KEY, "proposal_id": candidate.proposal_id, "binding_id": candidate.binding_id, "skill_version_id": candidate.skill_version_id, "approval_request_id": request.id, "input_digest": input_digest, "expected_due_date": normalized_input["expected_due_date"]}
        created = self.work.create(
            work_type="task", title="Mark overdue account", scope_type="account",
            origin_type="agent", origin_reference=f"advisory_proposal:{candidate.proposal_id}",
            actor=WorkActor(actor_type="system", actor_reference="system:advisory-materializer", actor_user_id=None),
            description="Governed pilot action account.mark_overdue.",
            work_key=work_key, account_id=candidate.account_id, subject_user_id=None,
            context_data=context, idempotency_key=f"{work_key}:materialize",
        )
        item = created.work_item
        if item.context_data != context:
            raise AdvisoryProposalApprovalCorrelationError("Persisted Work context mismatch.")
        if item.status == "backlog":
            item = self.work.transition_status(
                item.id, expected_version=item.version,
                actor=WorkActor(actor_type="system", actor_reference=f"system:work:{item.id}", actor_user_id=None),
                status="ready", idempotency_key=f"work:{item.id}:pilot:ready",
            ).work_item
        configured = self.work_execution.configure_with_existing_approval(
            item.id, version_id=candidate.skill_version_id,
            authority_user_id=candidate.authority_user_id,
            input_payload=normalized_input, approval_request_id=request.id,
        )
        result = self.effect.execute(
            work_item_id=item.id, approval_request_id=request.id,
            authority_user_id=candidate.authority_user_id,
            actor_reference=actor_reference, input_payload=normalized_input,
        )
        return AuthenticatedAdvisoryProposalWorkMaterializationResult(
            item.id, configured.execution.id, result.approval_consumption_id,
            result.invocation_id, result.invocation_status,
            created.duplicate or configured.duplicate or result.duplicate,
            result.output,
        )
