from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from app.intelligence.planning.capabilities import ApprovalKind
from app.intelligence.planning.capabilities import CapabilityDefinition
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.capabilities import EffectClass


# Fixed mismatch tokens (never input values).
NO_REFERENCE_ENTRY: Final[str] = "no_reference_entry"
EXECUTABLE_NOT_ALLOWED: Final[str] = "executable_not_allowed"
LLM_PROPOSABLE_VS_INITIATION_ACTOR: Final[str] = (
    "llm_proposable_vs_initiation_actor"
)
CORRIDOR: Final[str] = "corridor"
APPROVAL_POLICY_VS_AUTHORITY_MODEL: Final[str] = (
    "approval_policy_vs_authority_model"
)
AUTONOMY_POLICY_MISMATCH: Final[str] = "autonomy_policy_mismatch"
RBAC_POLICY_MISMATCH: Final[str] = "rbac_policy_mismatch"
KIND_VS_APPROVAL: Final[str] = "kind_vs_approval"
UNKNOWN_PERMISSION: Final[str] = "unknown_permission"
READ_REFERENCE_MISSING: Final[str] = "read_reference_missing"
READ_PERMISSIONS_MISSING: Final[str] = "read_permissions_missing"
READ_AUTONOMY_MISMATCH: Final[str] = "read_autonomy_mismatch"
EFFECT_UNSUPPORTED: Final[str] = "effect_unsupported"


@dataclass(frozen=True)
class ActionPolicyEntry:
    """Snapshot of one ACTION_METADATA entry."""

    capability_kind: str
    initiation_actor: str
    authority_model: str
    required_authority: str
    execution_corridor: str


@dataclass(frozen=True)
class AutonomyEntry:
    """Snapshot of evaluate_skill_autonomy for an AGENT actor."""

    disposition: str
    risk_level: str
    approver_permission: str | None


@dataclass(frozen=True)
class PolicyReference:
    """
    Injected, plain-data snapshot of the EXISTING sources of truth
    (ACTION_METADATA, the autonomy policy, the RBAC vocabulary and the
    permissions of the existing read routes). It is assembled OUTSIDE
    this package; the registry is never a second source of authority:
    any disagreement is a rejection, never a choice of the more
    permissive reading.
    """

    actions: Mapping[str, ActionPolicyEntry]
    autonomy_by_effect: Mapping[str, AutonomyEntry]
    known_permissions: frozenset[str]
    read_route_permissions: Mapping[str, frozenset[str]]
    source: str = "synthetic"


def reconcile_capability(
    definition: CapabilityDefinition,
    reference: PolicyReference,
) -> tuple[str, ...]:
    """Mismatch tokens between ONE capability and the reference; empty
    means consistent. Sorted and unique, so the result is deterministic."""
    problems: set[str] = set()

    if definition.executable:
        problems.add(EXECUTABLE_NOT_ALLOWED)

    if not set(definition.required_permissions) <= reference.known_permissions:
        problems.add(UNKNOWN_PERMISSION)

    approval = definition.approval_policy

    if (
        approval.approver_permission is not None
        and approval.approver_permission not in reference.known_permissions
    ):
        problems.add(UNKNOWN_PERMISSION)

    if definition.effect_class is EffectClass.PROPOSE:
        problems.add(EFFECT_UNSUPPORTED)
        return tuple(sorted(problems))

    if definition.reference_key is None:
        _reconcile_read(definition, reference, problems)
    else:
        _reconcile_governed(definition, reference, problems)

    return tuple(sorted(problems))


def _reconcile_governed(
    definition: CapabilityDefinition,
    reference: PolicyReference,
    problems: set[str],
) -> None:
    entry = reference.actions.get(definition.reference_key or "")

    if entry is None:
        problems.add(NO_REFERENCE_ENTRY)
        return

    # INV-3 applies ONLY to governed capabilities that have an action
    # reference: a model may propose it iff the action is agent-initiated.
    if definition.llm_proposable != (entry.initiation_actor == "agent"):
        problems.add(LLM_PROPOSABLE_VS_INITIATION_ACTOR)

    if definition.corridor != entry.execution_corridor:
        problems.add(CORRIDOR)

    approval = definition.approval_policy
    is_skill_action = entry.capability_kind == "skill_action"

    if is_skill_action != (approval.kind is ApprovalKind.PER_STEP_APPROVAL):
        problems.add(KIND_VS_APPROVAL)

    if entry.authority_model == "approval":
        if (
            approval.kind is not ApprovalKind.PER_STEP_APPROVAL
            or approval.approver_permission != entry.required_authority
        ):
            problems.add(APPROVAL_POLICY_VS_AUTHORITY_MODEL)

        autonomy = reference.autonomy_by_effect.get(
            definition.effect_class.value
        )

        if (
            autonomy is None
            or autonomy.disposition != "approval_required"
            or autonomy.approver_permission != approval.approver_permission
        ):
            problems.add(AUTONOMY_POLICY_MISMATCH)
    else:
        if (
            approval.kind is not ApprovalKind.RBAC_ONLY
            or entry.required_authority
            not in definition.required_permissions
        ):
            problems.add(RBAC_POLICY_MISMATCH)


def _reconcile_read(
    definition: CapabilityDefinition,
    reference: PolicyReference,
    problems: set[str],
) -> None:
    # Reads have no ACTION_METADATA entry: they are checked against the
    # permissions of the existing read routes and the autonomy policy.
    required = reference.read_route_permissions.get(definition.capability_id)

    if required is None:
        problems.add(READ_REFERENCE_MISSING)
    elif not required <= set(definition.required_permissions):
        problems.add(READ_PERMISSIONS_MISSING)

    autonomy = reference.autonomy_by_effect.get(EffectClass.READ.value)

    if (
        autonomy is None
        or autonomy.disposition != "autonomous_allowed"
        or autonomy.approver_permission is not None
    ):
        problems.add(READ_AUTONOMY_MISMATCH)


def reconcile_registry(
    registry: CapabilityRegistry,
    reference: PolicyReference,
) -> dict[str, tuple[str, ...]]:
    """Whole-registry check (tests / start-up). Registered capabilities
    only; the result maps `capability_id@version` to its mismatches."""
    result: dict[str, tuple[str, ...]] = {}

    for definition in registry.definitions:
        if not definition.registered:
            continue

        problems = reconcile_capability(definition, reference)

        if problems:
            result[f"{definition.capability_id}@{definition.version}"] = problems

    return result
