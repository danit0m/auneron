from dataclasses import dataclass
from typing import Literal

from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.models.skill import SkillCapability
from app.models.skill import SkillVersion


AutonomyDisposition = Literal[
    "autonomous_allowed",
    "approval_required",
    "blocked",
]
AutonomyReason = Literal[
    "low_risk_read_only",
    "human_explicit_path",
    "mutating_requires_human",
    "external_requires_sensitive_human",
]


@dataclass(frozen=True)
class AutonomyPolicyDecision:
    disposition: AutonomyDisposition
    reason: AutonomyReason
    risk_level: str
    required_approval_permission: str | None

    @property
    def autonomous_allowed(self) -> bool:
        return self.disposition == "autonomous_allowed"

    @property
    def requires_approval(self) -> bool:
        return self.disposition == "approval_required"


def classify_skill_risk(
    *,
    version: SkillVersion,
    capabilities: tuple[SkillCapability, ...],
) -> tuple[str, str]:
    external_capability = any(
        capability.resource_scope == "external"
        for capability in capabilities
    )

    if version.execution_mode == "read_only":
        if any(
            capability.access_mode != "read"
            for capability in capabilities
        ):
            raise ApprovalStateError(
                "Skill read_only declara capability incompatível."
            )
        if external_capability:
            raise ApprovalStateError(
                "Capability external exige execution_mode external."
            )
        return "low", "approval:decide"

    if version.execution_mode == "mutating":
        if external_capability:
            raise ApprovalStateError(
                "Capability external exige execution_mode external."
            )
        return "high", "approval:decide"

    if version.execution_mode == "external":
        return "critical", "approval:decide_sensitive"

    raise ApprovalStateError(
        "execution_mode publicado é inválido."
    )


def evaluate_skill_autonomy(
    *,
    actor_type: str,
    version: SkillVersion,
    capabilities: tuple[SkillCapability, ...],
) -> AutonomyPolicyDecision:
    """
    Decide somente a política de autonomia.

    Não autentica ator, não autoriza escopo, não cria ApprovalRequest,
    não consome ApprovalDecision e não chama o runtime.
    """
    if actor_type not in {
        "user",
        "agent",
        "system",
        "integration",
    }:
        raise ApprovalValidationError(
            "actor_type inválido para política de autonomia."
        )

    risk_level, required_permission = classify_skill_risk(
        version=version,
        capabilities=capabilities,
    )

    if actor_type == "user":
        return AutonomyPolicyDecision(
            disposition="blocked",
            reason="human_explicit_path",
            risk_level=risk_level,
            required_approval_permission=None,
        )

    if risk_level == "low":
        return AutonomyPolicyDecision(
            disposition="autonomous_allowed",
            reason="low_risk_read_only",
            risk_level=risk_level,
            required_approval_permission=None,
        )

    if risk_level == "high":
        return AutonomyPolicyDecision(
            disposition="approval_required",
            reason="mutating_requires_human",
            risk_level=risk_level,
            required_approval_permission=required_permission,
        )

    if risk_level == "critical":
        return AutonomyPolicyDecision(
            disposition="approval_required",
            reason="external_requires_sensitive_human",
            risk_level=risk_level,
            required_approval_permission=required_permission,
        )

    raise ApprovalStateError(
        "risk_level não suportado pela política de autonomia."
    )
