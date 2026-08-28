from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.advisory_proposal_errors import (
    AdvisoryProposalDispatchNotAllowedError,
)
from app.core.advisory_proposal_errors import AdvisoryProposalValidationError
from app.core.approval_errors import ApprovalValidationError
from app.core.authentication import AuthenticatedSession
from app.services.approval_service import approval_input_identity
from app.services.authenticated_advisory_proposal_consumption_service import (
    AuthenticatedAdvisoryProposalConsumptionService,
)
from app.services.governed_skill_execution import GovernedSkillExecutionService
from app.services.skill_runtime import SkillInvocationActor


@dataclass(frozen=True)
class AuthenticatedAdvisoryProposalDispatchResult:
    """
    Safe result of one governed read-only advisory proposal action.

    This value is not an authority token and contains no session, role,
    permission, idempotency-key, Approval, Work, or raw-input state.
    """

    proposal_id: int
    binding_id: int
    skill_version_id: int
    skill_id: int
    agent_name: str
    actor_reference: str
    invocation_id: int
    invocation_status: str
    duplicate: bool
    output: Any


def _normalize_input(
    input_payload: Any,
) -> Any:
    try:
        normalized, _ = approval_input_identity(
            input_payload
        )
    except ApprovalValidationError as error:
        raise AdvisoryProposalValidationError(
            "Advisory proposal dispatch input is invalid."
        ) from error

    return normalized


class AuthenticatedAdvisoryProposalDispatchService:
    """
    Internal 25L bridge from one authenticated advisory proposal candidate to
    the existing governed Skill execution boundary.

    The adapter accepts only revalidated `read_only` + `internal_python`
    candidates, derives actor attribution and runtime idempotency server-side,
    and never invokes the runtime directly.
    """

    def __init__(
        self,
        db: Session,
        *,
        consumption_service: (
            AuthenticatedAdvisoryProposalConsumptionService | None
        ) = None,
        governed_service: GovernedSkillExecutionService | None = None,
    ) -> None:
        self.db = db
        self.consumption_service = (
            consumption_service
            if consumption_service is not None
            else AuthenticatedAdvisoryProposalConsumptionService(db)
        )
        self.governed_service = (
            governed_service
            if governed_service is not None
            else GovernedSkillExecutionService(db)
        )

    def dispatch(
        self,
        *,
        proposal_id: int,
        authenticated: AuthenticatedSession,
        binding_id: int,
        input_payload: Any,
    ) -> AuthenticatedAdvisoryProposalDispatchResult:
        normalized_input = _normalize_input(
            input_payload
        )

        candidate = self.consumption_service.validate(
            proposal_id=proposal_id,
            authenticated=authenticated,
            binding_id=binding_id,
            input_payload=normalized_input,
        )

        if candidate.execution_mode != "read_only":
            raise AdvisoryProposalDispatchNotAllowedError(
                "25L advisory dispatch permits read_only Skills only."
            )

        if candidate.runtime_kind != "internal_python":
            raise AdvisoryProposalDispatchNotAllowedError(
                "25L advisory dispatch permits internal_python runtime only."
            )

        actor_reference = (
            f"agent:{candidate.agent_name}"
        )
        actor = SkillInvocationActor(
            actor_type="agent",
            actor_reference=actor_reference,
            actor_user_id=None,
        )
        idempotency_key = (
            f"advisory:{candidate.proposal_id}:"
            f"{candidate.binding_id}"
        )

        governed = self.governed_service.execute(
            candidate.skill_version_id,
            actor=actor,
            authority_user_id=candidate.authority_user_id,
            input_payload=normalized_input,
            idempotency_key=idempotency_key,
            approval_request_id=None,
            runtime_context=None,
        )

        invocation_result = governed.invocation
        invocation = invocation_result.invocation

        return AuthenticatedAdvisoryProposalDispatchResult(
            proposal_id=candidate.proposal_id,
            binding_id=candidate.binding_id,
            skill_version_id=candidate.skill_version_id,
            skill_id=candidate.skill_id,
            agent_name=candidate.agent_name,
            actor_reference=actor_reference,
            invocation_id=invocation.id,
            invocation_status=invocation.status,
            duplicate=invocation_result.duplicate,
            output=invocation_result.output,
        )
