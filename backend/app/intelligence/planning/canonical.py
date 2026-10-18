import hashlib
import json
import unicodedata
from collections.abc import Mapping
from typing import Any
from typing import Final

from app.intelligence.planning.codes import RejectionCode
from app.intelligence.planning.codes import Violation
from app.intelligence.planning.codes import make_violation
from app.intelligence.planning.limits import PLAN_LIMITS
from app.intelligence.planning.limits import PlanLimits


# Domain separation: a digest computed for one purpose can never be
# replayed as a digest for another one.
DOMAIN_EXECUTION: Final[bytes] = b"auneron.plan.v1.execution\n"
DOMAIN_PRESENTATION: Final[bytes] = b"auneron.plan.v1.presentation\n"
DOMAIN_REGISTRY: Final[bytes] = b"auneron.capreg.v1\n"
DOMAIN_CONTEXT: Final[bytes] = b"auneron.plan.v1.context\n"
DOMAIN_IDEMPOTENCY: Final[bytes] = b"auneron.plan.v1.idempotency\n"

SERVER_ONLY_FIELDS: Final[tuple[str, ...]] = (
    "registry_digest",
    "execution_digest",
    "presentation_digest",
)


class CanonicalError(ValueError):
    """Raised when a value cannot be canonicalised under the strict rules."""

    def __init__(self, violations: tuple[Violation, ...]) -> None:
        super().__init__(violations[0].code if violations else "canonical")
        self.violations = violations


class InputError(ValueError):
    """Raised by strict_parse; carries a stable rejection code."""

    def __init__(self, code: RejectionCode) -> None:
        super().__init__(code.value)
        self.code = code


# ----------------------------------------------------------------------
# strict parsing
# ----------------------------------------------------------------------


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    seen: dict[str, Any] = {}

    for key, value in pairs:
        if key in seen:
            raise InputError(RejectionCode.INPUT_DUPLICATE_KEY)

        seen[key] = value

    return seen


def _reject_non_finite(_: str) -> Any:
    raise InputError(RejectionCode.INPUT_NON_FINITE_NUMBER)


def strict_parse(
    raw: bytes | str,
    limits: PlanLimits = PLAN_LIMITS,
) -> Any:
    """
    bytes/str -> JSON value, or InputError. Size is checked BEFORE
    decoding or parsing; duplicate keys, NaN and Infinity are refused;
    pathological nesting maps to a typed code instead of a crash.
    """
    if isinstance(raw, str):
        try:
            data = raw.encode("utf-8")
        except UnicodeEncodeError:
            raise InputError(RejectionCode.INPUT_NOT_UTF8) from None
    elif isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
    else:
        raise InputError(RejectionCode.INPUT_NOT_JSON)

    if len(data) > limits.max_json_bytes:
        raise InputError(RejectionCode.INPUT_TOO_LARGE)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError(RejectionCode.INPUT_NOT_UTF8) from None

    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except InputError:
        raise
    except RecursionError:
        raise InputError(RejectionCode.INPUT_NESTING_TOO_DEEP) from None
    except ValueError:
        raise InputError(RejectionCode.INPUT_NOT_JSON) from None

    if not isinstance(value, dict):
        raise InputError(RejectionCode.INPUT_NOT_OBJECT)

    return value


# ----------------------------------------------------------------------
# value rules (null / float / integer range / Unicode)
# ----------------------------------------------------------------------


def _string_issues(text: str) -> RejectionCode | None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return RejectionCode.STRING_ENCODING_INVALID

    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return RejectionCode.STRING_CONTROL_CHARACTER

    if not unicodedata.is_normalized("NFC", text):
        return RejectionCode.STRING_NOT_NFC

    return None


def scan_value(
    value: Any,
    limits: PlanLimits = PLAN_LIMITS,
) -> tuple[list[Violation], int]:
    """
    Walks a parsed JSON value (iteratively, so depth cannot exhaust the
    interpreter stack) and returns (violations, json_depth). A container
    at the top is depth 1.
    """
    violations: list[Violation] = []
    max_depth = 0
    stack: list[tuple[Any, str, int]] = [(value, "$", 1)]

    while stack:
        item, path, depth = stack.pop()

        if isinstance(item, bool):
            continue

        if item is None:
            violations.append(
                make_violation(RejectionCode.VALUE_NULL_FORBIDDEN, path)
            )
        elif isinstance(item, float):
            violations.append(
                make_violation(RejectionCode.VALUE_FLOAT_FORBIDDEN, path)
            )
        elif isinstance(item, int):
            if abs(item) > limits.max_integer:
                violations.append(
                    make_violation(RejectionCode.INTEGER_OUT_OF_RANGE, path)
                )
        elif isinstance(item, str):
            issue = _string_issues(item)

            if issue is not None:
                violations.append(make_violation(issue, path))
        elif isinstance(item, list):
            max_depth = max(max_depth, depth)

            for index, child in enumerate(item):
                stack.append((child, f"{path}[{index}]", depth + 1))
        elif isinstance(item, dict):
            max_depth = max(max_depth, depth)

            for key, child in item.items():
                if not isinstance(key, str):
                    violations.append(
                        make_violation(RejectionCode.SCHEMA_VIOLATION, path, "key")
                    )
                    continue

                issue = _string_issues(key)

                if issue is not None:
                    violations.append(make_violation(issue, f"{path}.<key>"))

                stack.append((child, f"{path}.{key}", depth + 1))
        else:
            violations.append(
                make_violation(RejectionCode.SCHEMA_VIOLATION, path, "type")
            )

    return sorted(violations), max_depth


# ----------------------------------------------------------------------
# canonical bytes and digests
# ----------------------------------------------------------------------


def canonical_bytes(value: Any) -> bytes:
    """
    Same algorithm as the approval flow's input canonicalisation
    (sorted keys, compact separators, UTF-8 without ASCII escaping,
    non-finite numbers refused), after the strict value rules.
    """
    violations, _ = scan_value(value)

    if violations:
        raise CanonicalError(tuple(violations))

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(domain: bytes, value: Any) -> str:
    return hashlib.sha256(domain + canonical_bytes(value)).hexdigest()


def _sorted_canonical(items: list[Any]) -> list[Any]:
    return sorted(items, key=canonical_bytes)


def execution_material(
    plan: Mapping[str, Any],
    registry_digest: str,
) -> dict[str, Any]:
    """
    Everything that can change what would be executed -- and nothing
    else. Excludes `summary`, every `rationale`, the original order of
    the `steps` array and of `depends_on`, and all server-only fields.
    """
    steps = []

    for step in sorted(plan["steps"], key=lambda s: s["step_key"]):
        steps.append(
            {
                "step_key": step["step_key"],
                "capability_id": step["capability_id"],
                "capability_version": step["capability_version"],
                "params": step["params"],
                "depends_on": sorted(step["depends_on"]),
                "preconditions": _sorted_canonical(list(step["preconditions"])),
                "expected_outcomes": _sorted_canonical(
                    list(step["expected_outcomes"])
                ),
                "on_failure": step["on_failure"],
            }
        )

    return {
        "contract": plan["contract"],
        "registry_digest": registry_digest,
        "objective_ref": plan["objective_ref"],
        "plan_version": plan["plan_version"],
        "steps": steps,
    }


def execution_digest(plan: Mapping[str, Any], registry_digest: str) -> str:
    return digest(DOMAIN_EXECUTION, execution_material(plan, registry_digest))


def presentation_digest(
    plan: Mapping[str, Any],
    execution_digest_value: str,
) -> str:
    return digest(
        DOMAIN_PRESENTATION,
        {
            "execution_digest": execution_digest_value,
            "summary": plan["summary"],
            "rationales": {
                step["step_key"]: step["rationale"] for step in plan["steps"]
            },
        },
    )


def seal(
    plan: Mapping[str, Any],
    registry_digest: str,
) -> dict[str, str]:
    """Server-side seal. Integrity only: it authenticates nobody and
    authorises nothing."""
    execution = execution_digest(plan, registry_digest)

    return {
        "registry_digest": registry_digest,
        "execution_digest": execution,
        "presentation_digest": presentation_digest(plan, execution),
    }


def verify_seal(
    sealed_plan: Mapping[str, Any],
    registry_digest: str,
) -> list[Violation]:
    """Recomputes the digests of a server-sealed plan; any difference
    (tampering, other registry, edited content) is DIGEST_MISMATCH."""
    body = {
        key: value
        for key, value in sealed_plan.items()
        if key not in SERVER_ONLY_FIELDS
    }
    try:
        expected = seal(body, registry_digest)
    except (KeyError, TypeError, AttributeError, CanonicalError):
        return [
            make_violation(RejectionCode.DIGEST_MISMATCH, "$", "malformed")
        ]

    problems = [
        make_violation(RejectionCode.DIGEST_MISMATCH, f"$.{name}", name)
        for name in SERVER_ONLY_FIELDS
        if sealed_plan.get(name) != expected[name]
    ]
    return sorted(problems)


def derive_idempotency_key(
    objective_ref: str,
    plan_version: int,
    execution_digest_value: str,
) -> str:
    """Deterministic key a store COULD enforce uniqueness on. Deriving
    it prevents nothing by itself: durable idempotency and replay
    protection need persistence (a later gate)."""
    return digest(
        DOMAIN_IDEMPOTENCY,
        {
            "objective_ref": objective_ref,
            "plan_version": plan_version,
            "execution_digest": execution_digest_value,
        },
    )
