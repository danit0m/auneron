import copy
import re
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from typing import Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.intelligence.planning.canonical import CanonicalError
from app.intelligence.planning.canonical import canonical_bytes
from app.intelligence.planning.capabilities import CapabilityDefinition
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.capabilities import schema_is_closed
from app.intelligence.planning.schema import build_plan_schema
from app.intelligence.proposal.contracts import INSTRUCTIONS_TEXT
from app.intelligence.proposal.contracts import ProjectionRejectionCode


# Field names that exist only inside the server. They may never be a key
# of the projected schema. Matched exactly (a key is not prose), so plain
# words such as `executable` are covered here and nowhere else.
INTERNAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "registry_digest",
        "execution_digest",
        "presentation_digest",
        "context_digest",
        "projection_digest",
        "policy_digest",
        "idempotency_key",
        "is_grant",
        "eligibility_verified",
        "llm_proposable",
        "executable",
        "enabled",
        "registered",
        "separation_requirements",
        "approval_policy",
        "required_permissions",
        "reference_key",
        "synthetic_authority",
        "replay_protected",
        "corridor",
        "prerequisites",
        "approver_permission",
    }
)

# 32+ hex characters, or a 40+ character opaque run: shapes of digests
# and tokens, whatever they spell.
_OPAQUE: Final[re.Pattern[str]] = re.compile(
    r"[0-9a-fA-F]{32,}|[A-Za-z0-9+/_=-]{40,}"
)
_COMPOUND_MARKS: Final[str] = "_.:/"


@dataclass(frozen=True)
class RenderOutcome:
    schema_json: str | None
    schema_bytes: int
    codes: tuple[ProjectionRejectionCode, ...]


def is_printable_ascii(text: str) -> bool:
    return all(0x20 <= ord(char) <= 0x7E for char in text)


def _is_compound(token: str) -> bool:
    return any(mark in token for mark in _COMPOUND_MARKS)


def walk_strings(value: Any) -> tuple[list[str], list[str]]:
    """(keys, string values) of a JSON value, iteratively."""
    keys: list[str] = []
    values: list[str] = []
    stack: list[Any] = [value]

    while stack:
        item = stack.pop()

        if isinstance(item, Mapping):
            for key, child in item.items():
                keys.append(key)
                stack.append(child)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, str):
            values.append(item)

    return keys, values


def forbidden_identifiers(
    definitions: Iterable[CapabilityDefinition],
    selected_ids: Iterable[str],
    known_permissions: Iterable[str],
) -> frozenset[str]:
    """
    Compound internal identifiers that must not appear in projected text:
    ids of every capability that is NOT projected (disabled and
    non-proposable ones included), action references, corridors,
    separation tokens, approval kinds and every permission string.
    A string equal to a projected capability id is public and removed:
    the reference key of a capability is the same text as its id.
    """
    found: set[str] = set(known_permissions)
    public = {item.casefold() for item in selected_ids}

    for definition in definitions:
        found.add(definition.capability_id)
        found.update(definition.required_permissions)
        found.update(definition.separation_requirements)
        found.add(definition.approval_policy.kind.value)

        for optional in (
            definition.reference_key,
            definition.corridor,
            definition.approval_policy.approver_permission,
        ):
            if optional:
                found.add(optional)

    found.update(key for key in INTERNAL_KEYS if _is_compound(key))

    return frozenset(
        item.casefold() for item in found if _is_compound(item)
    ) - frozenset(public)


def has_leak(
    schema: Mapping[str, Any],
    instructions: str,
    forbidden: frozenset[str],
) -> bool:
    keys, values = walk_strings(schema)

    if any(key in INTERNAL_KEYS for key in keys):
        return True

    for text in values + keys + [instructions]:
        lowered = text.casefold()

        if any(item in lowered for item in forbidden):
            return True

        if _OPAQUE.search(text):
            return True

    return False


def pin_schema(
    schema: dict[str, Any],
    objective_ref: str,
    plan_version: int,
) -> dict[str, Any]:
    """Fixes the objective and the plan version: the model may not choose."""
    pinned = copy.deepcopy(schema)
    pinned["properties"]["objective_ref"] = {"const": objective_ref}
    pinned["properties"]["plan_version"] = {"const": plan_version}
    return pinned


def render_schema(
    selected: tuple[CapabilityDefinition, ...],
    registry: CapabilityRegistry,
    objective_ref: str,
    plan_version: int,
    max_schema_bytes: int,
    forbidden: frozenset[str],
) -> tuple[dict[str, Any] | None, RenderOutcome]:
    """
    Builds the projected schema from a SUB-registry made of the selected
    definitions. The sub-registry exists only for this call: its digest
    is never used, and it is never handed to a validator.
    """
    sub_registry = CapabilityRegistry(
        selected,
        version=registry.version,
        limits=registry.limits,
    )
    try:
        generated = build_plan_schema(sub_registry, registry.limits)
        schema = pin_schema(generated, objective_ref, plan_version)
    except (KeyError, TypeError):
        # The generator returned something that is not a plan schema.
        return None, RenderOutcome(
            None, 0, (ProjectionRejectionCode.SCHEMA_INVALID,)
        )

    codes: list[ProjectionRejectionCode] = []

    try:
        Draft202012Validator.check_schema(schema)
        closed = schema_is_closed(schema)
    except SchemaError:
        closed = False

    if not closed:
        return None, RenderOutcome(
            None, 0, (ProjectionRejectionCode.SCHEMA_INVALID,)
        )

    keys, values = walk_strings(schema)

    if not all(is_printable_ascii(text) for text in keys + values):
        return None, RenderOutcome(
            None, 0, (ProjectionRejectionCode.PROJECTION_TEXT_INVALID,)
        )

    try:
        text = canonical_bytes(schema).decode("ascii")
    except (CanonicalError, UnicodeDecodeError):
        return None, RenderOutcome(
            None, 0, (ProjectionRejectionCode.SCHEMA_INVALID,)
        )

    size = len(text.encode("ascii"))

    if size > max_schema_bytes:
        codes.append(ProjectionRejectionCode.SCHEMA_TOO_LARGE)

    # The scan covers what the registry generated. The two pinned values
    # are caller data already constrained by their own patterns; a
    # 32-character reference made of hex digits is legitimate and would
    # otherwise look like a digest.
    if has_leak(generated, INSTRUCTIONS_TEXT, forbidden):
        codes.append(ProjectionRejectionCode.INTERNAL_TERM_LEAK)

    if codes:
        return None, RenderOutcome(None, size, tuple(codes))

    return schema, RenderOutcome(text, size, ())
