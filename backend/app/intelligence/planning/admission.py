from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import Final

from app.intelligence.planning.codes import Violation


EXECUTION_BLOCKED_REASON: Final[str] = "no_executor_in_this_phase"


class AdmissionDecision(str, Enum):
    REJECTED = "rejected"
    # Only READ steps: structurally admissible, still not executable.
    ADMITTED_READ_ONLY = "admitted_read_only"
    # At least one MUTATE step: every approval-bearing step still needs
    # its own human approval through the existing flow.
    ADMITTED_PENDING_STEP_APPROVALS = "admitted_pending_step_approvals"


@dataclass(frozen=True)
class StepApproval:
    step_key: str
    capability_id: str
    approver_permission: str | None
    corridor: str | None


@dataclass(frozen=True)
class PlanAdmission:
    """
    Deterministic, immutable outcome of structural admissibility.

    It is NOT an authorisation, an approval or a token: it has no
    execution method, `is_grant` is always False, and
    `bool(admission)` raises so nobody can write `if admission:` and
    mistake "admitted" for "allowed". Real eligibility, real authority
    and real approvals are verified later, by the execution gate.
    """

    decision: AdmissionDecision
    violations: tuple[Violation, ...]
    registry_digest: str
    context_digest: str | None
    execution_digest: str | None
    presentation_digest: str | None
    idempotency_key: str | None
    objective_ref: str | None
    plan_version: int | None
    step_keys: tuple[str, ...]
    mutating_step_keys: tuple[str, ...]
    requires_step_approvals: tuple[StepApproval, ...]
    separation_requirements: tuple[str, ...]
    initiator_ref: str | None
    eligibility_verified: bool = False
    is_grant: bool = False
    synthetic_authority: bool = True
    execution_blocked_reason: str = EXECUTION_BLOCKED_REASON

    def __post_init__(self) -> None:
        # Invariants that must hold for every instance, however built.
        if self.eligibility_verified is not False:
            raise ValueError("eligibility is never verified at admission.")

        if self.is_grant is not False:
            raise ValueError("An admission is never an authorisation.")

        if self.decision is AdmissionDecision.REJECTED:
            if not self.violations:
                raise ValueError("A rejection needs violations.")

            if self.execution_digest is not None:
                raise ValueError("A rejected plan carries no digest.")
        elif self.violations:
            raise ValueError("An admitted plan has no violations.")

    def __bool__(self) -> bool:
        raise TypeError(
            "Do not use an admission as a boolean; read .decision."
        )

    @property
    def admitted(self) -> bool:
        return self.decision is not AdmissionDecision.REJECTED

    def audit_view(self) -> dict[str, Any]:
        """Content-free summary (codes, counts, digests) safe to log."""
        return {
            "decision": self.decision.value,
            "violation_codes": sorted({v.code for v in self.violations}),
            "violation_count": len(self.violations),
            "step_count": len(self.step_keys),
            "mutating_step_count": len(self.mutating_step_keys),
            "registry_digest": self.registry_digest,
            "execution_digest": self.execution_digest,
            "presentation_digest": self.presentation_digest,
            "eligibility_verified": self.eligibility_verified,
            "is_grant": self.is_grant,
            "execution_blocked_reason": self.execution_blocked_reason,
        }
