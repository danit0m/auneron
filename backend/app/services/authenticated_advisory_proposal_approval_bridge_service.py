from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.advisory_proposal_errors import (
    AdvisoryProposalApprovalCorrelationError,
)
from app.core.advisory_proposal_errors import (
    AdvisoryProposalApprovalNotAllowedError,
)
from app.core.advisory_proposal_errors import AdvisoryProposalValidationError
from app.core.approval_errors import ApprovalValidationError
from app.core.authentication import AuthenticatedSession
from app.models.approval import ApprovalRequest
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import ApprovalService
from app.services.approval_service import approval_input_identity
from app.services.authenticated_advisory_proposal_consumption_service import (
    AuthenticatedAdvisoryProposalConsumptionService,
)
from app.services.governed_skill_execution import GovernedSkillExecutionService
from app.services.skill_runtime import SkillInvocationActor


@dataclass(frozen=True)
class AuthenticatedAdvisoryProposalApprovalRequestResult:
    """
    Safe result of creating/replaying one ApprovalRequest for a mutating
    authenticated advisory candidate.

    This value is not authority and contains no session, role, permission,
    idempotency key, input digest, request fingerprint, decision, or raw input.
    """

    proposal_id: int
    binding_id: int
    skill_version_id: int
    skill_id: int
    agent_name: str
    actor_reference: str
    approval_request_id: int
    approval_status: str
    risk_level: str
    duplicate: bool


@dataclass(frozen=True)
class AuthenticatedAdvisoryProposalApprovedDispatchResult:
    """
    Safe result of one approved mutating advisory candidate execution.

    This value is not authority and cannot be reused as Approval, Skill,
    session, scope, or runtime authorization.
    """

    proposal_id: int
    binding_id: int
    skill_version_id: int
    skill_id: int
    agent_name: str
    actor_reference: str
    approval_request_id: int
    approval_consumption_id: int
    invocation_id: int
    invocation_status: str
    duplicate: bool
    output: Any


def _positive_id(
    value: object,
    *,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise AdvisoryProposalValidationError(
            f"{field_name} is invalid."
        )
    return value


def _normalize_input(
    input_payload: Any,
) -> tuple[Any, str]:
    try:
        return approval_input_identity(
            input_payload
        )
    except ApprovalValidationError as error:
        raise AdvisoryProposalValidationError(
            "Advisory proposal Approval bridge input is invalid."
        ) from error


def _actor_reference(
    agent_name: str,
) -> str:
    return f"agent:{agent_name}"


def _approval_key(
    *,
    proposal_id: int,
    binding_id: int,
) -> str:
    return f"advisory:{proposal_id}:{binding_id}"


class AuthenticatedAdvisoryProposalApprovalBridgeService:
    """
    Internal 25M bridge for mutating + internal_python advisory candidates.

    Request and approved-dispatch phases independently re-run 25K current
    validation. The bridge derives requester/actor and Approval correlation
    server-side, never decides Approval, and never invokes the runtime
    directly.
    """

    def __init__(
        self,
        db: Session,
        *,
        consumption_service: (
            AuthenticatedAdvisoryProposalConsumptionService | None
        ) = None,
        approval_service: ApprovalService | None = None,
        governed_service: GovernedSkillExecutionService | None = None,
    ) -> None:
        self.db = db
        self.consumption_service = (
            consumption_service
            if consumption_service is not None
            else AuthenticatedAdvisoryProposalConsumptionService(db)
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

    def _validate_eligibility(
        self,
        candidate,
    ) -> None:
        if candidate.execution_mode != "mutating":
            raise AdvisoryProposalApprovalNotAllowedError(
                "25M Approval bridge permits mutating Skills only."
            )

        if candidate.runtime_kind != "internal_python":
            raise AdvisoryProposalApprovalNotAllowedError(
                "25M Approval bridge permits internal_python runtime only."
            )

    def _correlate_request(
        self,
        *,
        request: ApprovalRequest,
        candidate,
        actor_reference: str,
        idempotency_key: str,
        input_digest: str,
    ) -> None:
        exact = (
            request.action_type == "skill_execution"
            and request.skill_version_id == candidate.skill_version_id
            and request.requester_actor_type == "agent"
            and request.requester_reference == actor_reference
            and request.requester_user_id is None
            and request.idempotency_key == idempotency_key
            and request.input_digest == input_digest
            and request.risk_level == "high"
            and request.required_permission == "approval:decide"
            and request.target_account_id == candidate.account_id
            and request.target_user_id == candidate.subject_user_id
        )

        if not exact:
            raise AdvisoryProposalApprovalCorrelationError(
                "Persisted ApprovalRequest diverges from the current "
                "authenticated advisory candidate."
            )

    def request_approval(
        self,
        *,
        proposal_id: int,
        authenticated: AuthenticatedSession,
        binding_id: int,
        input_payload: Any,
    ) -> AuthenticatedAdvisoryProposalApprovalRequestResult:
        normalized_input, input_digest = _normalize_input(
            input_payload
        )

        candidate = self.consumption_service.validate(
            proposal_id=proposal_id,
            authenticated=authenticated,
            binding_id=binding_id,
            input_payload=normalized_input,
        )

        self._validate_eligibility(
            candidate
        )

        actor_reference = _actor_reference(
            candidate.agent_name
        )
        requester = ApprovalRequester(
            actor_type="agent",
            actor_reference=actor_reference,
            actor_user_id=None,
        )
        idempotency_key = _approval_key(
            proposal_id=candidate.proposal_id,
            binding_id=candidate.binding_id,
        )

        created = (
            self.approval_service
            .create_skill_execution_request(
                version_id=candidate.skill_version_id,
                requester=requester,
                input_payload=normalized_input,
                idempotency_key=idempotency_key,
            )
        )

        request = created.request

        self._correlate_request(
            request=request,
            candidate=candidate,
            actor_reference=actor_reference,
            idempotency_key=idempotency_key,
            input_digest=input_digest,
        )

        if request.status in {
            "rejected",
            "expired",
            "cancelled",
        }:
            raise AdvisoryProposalApprovalCorrelationError(
                "Terminal ApprovalRequest cannot be recycled for this "
                "advisory candidate; a new proposal is required."
            )

        return AuthenticatedAdvisoryProposalApprovalRequestResult(
            proposal_id=candidate.proposal_id,
            binding_id=candidate.binding_id,
            skill_version_id=candidate.skill_version_id,
            skill_id=candidate.skill_id,
            agent_name=candidate.agent_name,
            actor_reference=actor_reference,
            approval_request_id=request.id,
            approval_status=request.status,
            risk_level=request.risk_level,
            duplicate=created.duplicate,
        )

    def dispatch_approved(
        self,
        *,
        proposal_id: int,
        authenticated: AuthenticatedSession,
        binding_id: int,
        input_payload: Any,
        approval_request_id: int,
    ) -> AuthenticatedAdvisoryProposalApprovedDispatchResult:
        normalized_approval_id = _positive_id(
            approval_request_id,
            field_name="approval_request_id",
        )
        normalized_input, input_digest = _normalize_input(
            input_payload
        )

        candidate = self.consumption_service.validate(
            proposal_id=proposal_id,
            authenticated=authenticated,
            binding_id=binding_id,
            input_payload=normalized_input,
        )

        self._validate_eligibility(
            candidate
        )

        actor_reference = _actor_reference(
            candidate.agent_name
        )
        idempotency_key = _approval_key(
            proposal_id=candidate.proposal_id,
            binding_id=candidate.binding_id,
        )

        request = self.approval_service.get_request(
            normalized_approval_id
        )

        self._correlate_request(
            request=request,
            candidate=candidate,
            actor_reference=actor_reference,
            idempotency_key=idempotency_key,
            input_digest=input_digest,
        )

        actor = SkillInvocationActor(
            actor_type="agent",
            actor_reference=actor_reference,
            actor_user_id=None,
        )

        governed = self.governed_service.execute(
            candidate.skill_version_id,
            actor=actor,
            authority_user_id=candidate.authority_user_id,
            input_payload=normalized_input,
            idempotency_key=None,
            approval_request_id=request.id,
            runtime_context=None,
        )

        if (
            governed.approval_request_id != request.id
            or governed.approval_consumption_id is None
        ):
            raise AdvisoryProposalApprovalCorrelationError(
                "Governed execution did not finalize the exact Approval."
            )

        invocation_result = governed.invocation
        invocation = invocation_result.invocation

        return AuthenticatedAdvisoryProposalApprovedDispatchResult(
            proposal_id=candidate.proposal_id,
            binding_id=candidate.binding_id,
            skill_version_id=candidate.skill_version_id,
            skill_id=candidate.skill_id,
            agent_name=candidate.agent_name,
            actor_reference=actor_reference,
            approval_request_id=request.id,
            approval_consumption_id=governed.approval_consumption_id,
            invocation_id=invocation.id,
            invocation_status=invocation.status,
            duplicate=invocation_result.duplicate,
            output=invocation_result.output,
        )
