import re
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Final

from jsonschema import Draft202012Validator

from app.intelligence.planning.canonical import DOMAIN_REGISTRY
from app.intelligence.planning.canonical import digest
from app.intelligence.planning.limits import PLAN_LIMITS
from app.intelligence.planning.limits import PlanLimits


REGISTRY_VERSION: Final[str] = "capreg.v1"

# INT-IMPL-1: no capability may be executable. The registry DESCRIBES
# capabilities; it never grants authority, and no executor exists.
EXECUTION_ENABLED_IN_THIS_PHASE: Final[bool] = False

CAPABILITY_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,3}$"
)

# Separation requirements DECLARED by a capability. They are not
# enforced here: I != D is a future execution-gate requirement that the
# current approval flow does not guarantee for agent-originated steps.
INITIATOR_NE_DECIDER: Final[str] = "initiator_ne_decider"
EXECUTOR_NE_DECIDER: Final[str] = "executor_ne_decider"


class EffectClass(str, Enum):
    READ = "READ"
    PROPOSE = "PROPOSE"
    MUTATE = "MUTATE"


class ApprovalKind(str, Enum):
    NONE = "NONE"
    # Describes the EXISTING rbac-authorised corridor only.
    RBAC_ONLY = "RBAC_ONLY"
    PER_STEP_APPROVAL = "PER_STEP_APPROVAL"


@dataclass(frozen=True)
class ApprovalPolicy:
    kind: ApprovalKind
    approver_permission: str | None = None

    def __post_init__(self) -> None:
        if self.kind is ApprovalKind.PER_STEP_APPROVAL:
            if not self.approver_permission:
                raise ValueError("PER_STEP_APPROVAL needs an approver.")
        elif self.approver_permission is not None:
            raise ValueError("Only PER_STEP_APPROVAL names an approver.")

    def descriptor(self) -> dict[str, str]:
        result = {"kind": self.kind.value}

        if self.approver_permission is not None:
            result["approver_permission"] = self.approver_permission

        return result


@dataclass(frozen=True)
class Prerequisite:
    """
    Structural rule: the step must have, among its transitive DAG
    ancestors, at least one step whose capability is in `any_of` and
    whose `bind` parameters are EQUAL to the step's own, and it must
    carry `required_precondition`. It proves a dependency and a stated
    precondition -- never that the evaluation was favourable.
    """

    any_of: tuple[str, ...]
    bind: tuple[str, ...]
    required_precondition: tuple[str, str, bool | int | str]

    def descriptor(self) -> dict[str, Any]:
        fact, op, value = self.required_precondition

        return {
            "any_of": sorted(self.any_of),
            "bind": sorted(self.bind),
            "required_precondition": {"fact": fact, "op": op, "value": value},
        }


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_id: str
    version: int
    registered: bool
    enabled: bool
    llm_proposable: bool
    executable: bool
    effect_class: EffectClass
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    required_permissions: tuple[str, ...]
    approval_policy: ApprovalPolicy
    # Key of the governed action in ACTION_METADATA (None for reads).
    reference_key: str | None = None
    corridor: str | None = None
    separation_requirements: tuple[str, ...] = ()
    prerequisites: tuple[Prerequisite, ...] = ()
    _validator: Draft202012Validator = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not CAPABILITY_ID_PATTERN.fullmatch(self.capability_id):
            raise ValueError("Invalid capability_id.")

        if isinstance(self.version, bool) or self.version < 1:
            raise ValueError("version must be an integer >= 1.")

        if self.executable and not EXECUTION_ENABLED_IN_THIS_PHASE:
            raise ValueError("No capability is executable in this phase.")

        if not self.registered and (self.enabled or self.llm_proposable):
            raise ValueError("Unregistered capability cannot be enabled.")

        if self.llm_proposable and not self.enabled:
            raise ValueError("A disabled capability cannot be proposable.")

        if self.effect_class is EffectClass.READ:
            if self.approval_policy.kind is not ApprovalKind.NONE:
                raise ValueError("READ capabilities take no approval policy.")

            if self.reference_key is not None:
                raise ValueError("READ capabilities have no action reference.")
        elif self.approval_policy.kind is ApprovalKind.NONE:
            raise ValueError("Mutating capabilities need an approval policy.")

        if not schema_is_closed(self.input_schema):
            raise ValueError("input_schema must be closed.")

        if tuple(sorted(set(self.required_permissions))) != tuple(
            self.required_permissions
        ):
            raise ValueError("required_permissions must be sorted and unique.")

        Draft202012Validator.check_schema(dict(self.input_schema))
        object.__setattr__(
            self,
            "_validator",
            Draft202012Validator(dict(self.input_schema)),
        )

    @property
    def validator(self) -> Draft202012Validator:
        return self._validator

    def descriptor(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "capability_id": self.capability_id,
            "version": self.version,
            "registered": self.registered,
            "enabled": self.enabled,
            "llm_proposable": self.llm_proposable,
            "executable": self.executable,
            "effect_class": self.effect_class.value,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "required_permissions": list(self.required_permissions),
            "approval_policy": self.approval_policy.descriptor(),
            "separation_requirements": sorted(self.separation_requirements),
            "prerequisites": [p.descriptor() for p in self.prerequisites],
        }

        if self.reference_key is not None:
            result["reference_key"] = self.reference_key

        if self.corridor is not None:
            result["corridor"] = self.corridor

        return result


def schema_is_closed(schema: Any) -> bool:
    """Every object schema must forbid unknown properties."""
    if isinstance(schema, Mapping):
        if schema.get("type") == "object" and (
            schema.get("additionalProperties") is not False
        ):
            return False

        return all(schema_is_closed(value) for value in schema.values())

    if isinstance(schema, (list, tuple)):
        return all(schema_is_closed(value) for value in schema)

    return True


class CapabilityRegistry:
    """Closed, immutable, versioned. Built once from code; the model has
    no path to add, enable or alter a capability."""

    def __init__(
        self,
        definitions: tuple[CapabilityDefinition, ...],
        *,
        version: str = REGISTRY_VERSION,
        limits: PlanLimits = PLAN_LIMITS,
    ) -> None:
        by_key: dict[tuple[str, int], CapabilityDefinition] = {}

        for definition in definitions:
            key = (definition.capability_id, definition.version)

            if key in by_key:
                raise ValueError("Duplicate capability definition.")

            by_key[key] = definition

        self._definitions = tuple(
            sorted(
                definitions,
                key=lambda d: (d.capability_id, d.version),
            )
        )
        self._by_key = by_key
        self.version = version
        self.limits = limits
        self._digest = digest(DOMAIN_REGISTRY, self.descriptor())

    @property
    def definitions(self) -> tuple[CapabilityDefinition, ...]:
        return self._definitions

    @property
    def digest(self) -> str:
        return self._digest

    def descriptor(self) -> dict[str, Any]:
        return {
            "registry_version": self.version,
            "limits": self.limits.descriptor(),
            "capabilities": [d.descriptor() for d in self._definitions],
        }

    def get(
        self,
        capability_id: str,
        version: int,
    ) -> CapabilityDefinition | None:
        return self._by_key.get((capability_id, version))

    def versions_of(self, capability_id: str) -> tuple[int, ...]:
        return tuple(
            sorted(v for (cid, v) in self._by_key if cid == capability_id)
        )

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted({cid for (cid, _) in self._by_key}))


# ----------------------------------------------------------------------
# V1 registry
# ----------------------------------------------------------------------

_ACCOUNT_REF: Final[dict[str, Any]] = {
    "type": "string",
    "pattern": "^acct_[a-z0-9]{6,12}$",
}
_DUE_DATE: Final[dict[str, Any]] = {
    "type": "string",
    "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
}
# Outputs are DECLARED (a contract for the future executor) but are
# never consumed by another step in plan.v1: there is no dataflow.
_OUTPUT_OPAQUE: Final[dict[str, Any]] = {"type": "object"}


def _account_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["account_ref"],
        "properties": {"account_ref": dict(_ACCOUNT_REF)},
    }


def _episode_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["account_ref", "due_date"],
        "properties": {
            "account_ref": dict(_ACCOUNT_REF),
            "due_date": dict(_DUE_DATE),
        },
    }


def _memory_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["account_ref", "query", "limit"],
        "properties": {
            "account_ref": dict(_ACCOUNT_REF),
            "query": {"type": "string", "minLength": 1, "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
    }


ELIGIBILITY_PRECONDITION: Final[tuple[str, str, bool]] = (
    "episode.eligible_mark_overdue",
    "eq",
    True,
)
ELIGIBILITY_EVIDENCE: Final[tuple[str, ...]] = (
    "action_space.evaluate",
    "nba.evaluate",
)
_MARK_PREREQUISITE: Final[Prerequisite] = Prerequisite(
    any_of=ELIGIBILITY_EVIDENCE,
    bind=("account_ref", "due_date"),
    required_precondition=ELIGIBILITY_PRECONDITION,
)
_NO_APPROVAL: Final[ApprovalPolicy] = ApprovalPolicy(ApprovalKind.NONE)
_STEP_APPROVAL: Final[ApprovalPolicy] = ApprovalPolicy(
    ApprovalKind.PER_STEP_APPROVAL, "approval:decide"
)


def _read(
    capability_id: str,
    schema: dict[str, Any],
    permissions: tuple[str, ...],
) -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_id=capability_id,
        version=1,
        registered=True,
        enabled=True,
        llm_proposable=True,
        executable=False,
        effect_class=EffectClass.READ,
        input_schema=schema,
        output_schema=dict(_OUTPUT_OPAQUE),
        required_permissions=permissions,
        approval_policy=_NO_APPROVAL,
    )


def build_v1_registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        (
            _read(
                "account.get_context",
                _account_schema(),
                ("approval:read", "clients.view"),
            ),
            _read(
                "nba.evaluate",
                _episode_schema(),
                ("approval:read", "clients.view"),
            ),
            _read(
                "action_space.evaluate",
                _episode_schema(),
                ("approval:read", "clients.view"),
            ),
            _read(
                "memory.recall",
                _memory_schema(),
                ("clients.view", "memory:read"),
            ),
            CapabilityDefinition(
                capability_id="work.escalate_to_human",
                version=1,
                registered=True,
                enabled=True,
                llm_proposable=False,
                executable=False,
                effect_class=EffectClass.MUTATE,
                input_schema=_episode_schema(),
                output_schema=dict(_OUTPUT_OPAQUE),
                required_permissions=("work:create",),
                approval_policy=ApprovalPolicy(ApprovalKind.RBAC_ONLY),
                reference_key="escalate_to_human",
                corridor="work_materialization",
            ),
            CapabilityDefinition(
                capability_id="account.mark_overdue",
                version=1,
                registered=True,
                enabled=True,
                llm_proposable=True,
                executable=False,
                effect_class=EffectClass.MUTATE,
                input_schema=_episode_schema(),
                output_schema=dict(_OUTPUT_OPAQUE),
                required_permissions=("clients.manage", "skill:execute_mutating"),
                approval_policy=_STEP_APPROVAL,
                reference_key="account.mark_overdue",
                corridor="advisory_approval_work_skill",
                separation_requirements=(INITIATOR_NE_DECIDER,),
                prerequisites=(_MARK_PREREQUISITE,),
            ),
            CapabilityDefinition(
                capability_id="account.mark_paid",
                version=1,
                registered=True,
                enabled=False,
                llm_proposable=False,
                executable=False,
                effect_class=EffectClass.MUTATE,
                input_schema=_episode_schema(),
                output_schema=dict(_OUTPUT_OPAQUE),
                required_permissions=("clients.manage", "skill:execute_mutating"),
                approval_policy=_STEP_APPROVAL,
                reference_key="account.mark_paid",
                corridor="direct_approval_skill",
                separation_requirements=(
                    INITIATOR_NE_DECIDER,
                    EXECUTOR_NE_DECIDER,
                ),
                prerequisites=(_MARK_PREREQUISITE,),
            ),
        )
    )


# Pin of the V1 registry digest. Changing any capability, schema, flag or
# limit changes the digest; the validator then rejects EVERY plan until a
# reviewed change updates this constant together with its tests.
PINNED_REGISTRY_DIGEST: Final[str] = (
    "46f77593c243cc02047a354ba636e806a6e94a1ba10e9b8db0fdcd9cf31d3a3f"
)

REGISTRY_V1: Final[CapabilityRegistry] = build_v1_registry()
