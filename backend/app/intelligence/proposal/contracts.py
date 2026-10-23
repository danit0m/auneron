import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from enum import IntEnum
from types import MappingProxyType
from typing import Any
from typing import Final


# proj.v1: the deterministic, side-effect-free projection that narrows
# the capability registry to what ONE initiator may be offered. It is a
# guide for a model, never an authorisation: the full validator remains
# the only authority on admission.
PROJECTION_CONTRACT: Final[str] = "proj.v1"
INSTRUCTIONS_TEMPLATE_ID: Final[str] = "plan-instructions.v1"

# Frozen, versioned text. Any literal change needs a new approval and a
# new template id; tests pin both the text and its SHA-256.
INSTRUCTIONS_TEXT: Final[str] = (
    "Return exactly one JSON object that conforms to the provided "
    "JSON Schema. "
    "Use only the capabilities present in the schema and do not add "
    "fields that the schema does not define. "
    "Do not include digests, server-generated identifiers, "
    "credentials or any text outside the JSON object."
)
INSTRUCTIONS_SHA256: Final[str] = (
    "ff48f1c23aecca5b67548c2845df0b7dbf2165af9c271b9212c1d8448fdf87bb"
)

OBJECTIVE_REF_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^obj_[a-z0-9]{8,32}$"
)
ACCOUNT_REF_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^acct_[a-z0-9]{6,12}$"
)
PROFILE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9-]{0,63}$"
)
DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")

MAX_PLAN_VERSION: Final[int] = 2**53 - 1
MAX_PROFILE_TOKEN_CAP: Final[int] = 10_000_000
MAX_SCHEMA_BYTES_CEILING: Final[int] = 1_048_576
MAX_ACCOUNT_REFS_CEILING: Final[int] = 64


class Stage(IntEnum):
    """Fixed order. The first stage with a rejection ends the run."""

    REQUEST = 0
    REGISTRY = 1
    AUTHORITY = 2
    POLICY = 3
    SCOPE = 4
    SELECTION = 5
    RENDERING = 6
    INTERNAL = 7
    VERIFICATION = 8


class ProjectionRejectionCode(str, Enum):
    REQUEST_INVALID = "request_invalid"
    REGISTRY_INTEGRITY_FAILURE = "registry_integrity_failure"
    AUTHORITY_CONTEXT_INVALID = "authority_context_invalid"
    POLICY_REFERENCE_MISSING = "policy_reference_missing"
    POLICY_REFERENCE_INVALID = "policy_reference_invalid"
    SCOPE_INACCESSIBLE = "scope_inaccessible"
    PROJECTION_EMPTY = "projection_empty"
    SCHEMA_TOO_LARGE = "schema_too_large"
    SCHEMA_INVALID = "schema_invalid"
    PROJECTION_TEXT_INVALID = "projection_text_invalid"
    INTERNAL_TERM_LEAK = "internal_term_leak"
    PROJECTION_INTERNAL_ERROR = "projection_internal_error"
    PROJECTION_MALFORMED = "projection_malformed"
    PROJECTION_TAMPERED = "projection_tampered"
    PROJECTION_STALE = "projection_stale"


STAGE_OF: Final[Mapping[ProjectionRejectionCode, Stage]] = MappingProxyType({
    ProjectionRejectionCode.REQUEST_INVALID: Stage.REQUEST,
    ProjectionRejectionCode.REGISTRY_INTEGRITY_FAILURE: Stage.REGISTRY,
    ProjectionRejectionCode.AUTHORITY_CONTEXT_INVALID: Stage.AUTHORITY,
    ProjectionRejectionCode.POLICY_REFERENCE_MISSING: Stage.POLICY,
    ProjectionRejectionCode.POLICY_REFERENCE_INVALID: Stage.POLICY,
    ProjectionRejectionCode.SCOPE_INACCESSIBLE: Stage.SCOPE,
    ProjectionRejectionCode.PROJECTION_EMPTY: Stage.SELECTION,
    ProjectionRejectionCode.SCHEMA_TOO_LARGE: Stage.RENDERING,
    ProjectionRejectionCode.SCHEMA_INVALID: Stage.RENDERING,
    ProjectionRejectionCode.PROJECTION_TEXT_INVALID: Stage.RENDERING,
    ProjectionRejectionCode.INTERNAL_TERM_LEAK: Stage.RENDERING,
    ProjectionRejectionCode.PROJECTION_INTERNAL_ERROR: Stage.INTERNAL,
    ProjectionRejectionCode.PROJECTION_MALFORMED: Stage.VERIFICATION,
    ProjectionRejectionCode.PROJECTION_TAMPERED: Stage.VERIFICATION,
    ProjectionRejectionCode.PROJECTION_STALE: Stage.VERIFICATION,
})


class ExclusionReason(str, Enum):
    PERMISSION_MISSING = "permission_missing"
    SCOPE_NOT_READABLE = "scope_not_readable"
    SCOPE_NOT_MANAGEABLE = "scope_not_manageable"
    POLICY_MISMATCH = "policy_mismatch"
    PREREQUISITE_UNAVAILABLE = "prerequisite_unavailable"


@dataclass(frozen=True, order=True)
class Rejection:
    """Stable code and fixed position. Never carries an input value."""

    stage: int
    code: ProjectionRejectionCode


def make_rejection(code: ProjectionRejectionCode) -> Rejection:
    return Rejection(stage=int(STAGE_OF[code]), code=code)


@dataclass(frozen=True)
class ProfileView:
    """Plain data about a logical profile; no gateway object."""

    name: str
    max_output_tokens_cap: int


@dataclass(frozen=True)
class ProjectionLimits:
    max_schema_bytes: int = 12288
    max_account_refs: int = 8


@dataclass(frozen=True)
class ProjectionRequest:
    registry: Any
    pinned_registry_digest: str
    policy: Any
    authority: Any
    profile: ProfileView
    objective_ref: str
    plan_version: int
    account_refs: tuple[str, ...]
    limits: ProjectionLimits = field(default_factory=ProjectionLimits)


@dataclass(frozen=True, order=True)
class Exclusion:
    capability_id: str
    reason: ExclusionReason


@dataclass(frozen=True)
class Projection:
    """
    Immutable result of a successful projection. It keeps only
    primitives derived at call time (str, int, tuple, canonical JSON
    text) and no reference to the objects it was built from, so changes
    to those objects afterwards cannot alter it. It carries no
    authority: `is_grant` and `replay_protected` are always False.
    """

    contract: str
    template_id: str
    registry_digest: str
    context_digest: str
    policy_digest: str
    profile_name: str
    profile_max_output_tokens_cap: int
    max_schema_bytes: int
    max_account_refs: int
    plan_limits: tuple[tuple[str, int], ...]
    objective_ref: str
    plan_version: int
    account_refs: tuple[str, ...]
    capability_ids: tuple[str, ...]
    exclusions: tuple[Exclusion, ...]
    output_schema_json: str
    schema_bytes: int
    schema_digest: str
    instructions: str
    instructions_digest: str
    projection_digest: str
    synthetic_authority: bool = True
    is_grant: bool = False
    replay_protected: bool = False

    def __post_init__(self) -> None:
        if (
            self.synthetic_authority is not True
            or self.is_grant is not False
            or self.replay_protected is not False
        ):
            raise ValueError("A projection carries no authority.")

    @property
    def output_schema(self) -> dict[str, Any]:
        """A NEW dict on every access: the stored text is canonical JSON."""
        return json.loads(self.output_schema_json)

    def audit_view(self) -> dict[str, Any]:
        """Content-free summary (codes, counts, digests) safe to log."""
        reasons: dict[str, int] = {}

        for exclusion in self.exclusions:
            reasons[exclusion.reason.value] = (
                reasons.get(exclusion.reason.value, 0) + 1
            )

        return {
            "contract": self.contract,
            "registry_digest": self.registry_digest,
            "context_digest": self.context_digest,
            "policy_digest": self.policy_digest,
            "projection_digest": self.projection_digest,
            "capability_count": len(self.capability_ids),
            "exclusion_reasons": dict(sorted(reasons.items())),
            "schema_bytes": self.schema_bytes,
            "synthetic_authority": self.synthetic_authority,
            "is_grant": self.is_grant,
            "replay_protected": self.replay_protected,
        }


@dataclass(frozen=True)
class ProjectionResult:
    projection: Projection | None
    rejections: tuple[Rejection, ...] = ()

    def __post_init__(self) -> None:
        if (self.projection is None) == (not self.rejections):
            raise ValueError(
                "A result carries a projection or rejections, not both."
            )

    def __bool__(self) -> bool:
        raise TypeError(
            "Do not use a result as a boolean; read .projection."
        )

    @property
    def ok(self) -> bool:
        return self.projection is not None

    @property
    def codes(self) -> tuple[ProjectionRejectionCode, ...]:
        return tuple(rejection.code for rejection in self.rejections)
