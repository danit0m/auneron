from typing import Any
from typing import Final

from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.limits import PLAN_LIMITS
from app.intelligence.planning.limits import PlanLimits


CONTRACT: Final[str] = "plan.v1"
JSON_SCHEMA_DIALECT: Final[str] = (
    "https://json-schema.org/draft/2020-12/schema"
)

OBJECTIVE_REF_PATTERN: Final[str] = "^obj_[a-z0-9]{8,32}$"
STEP_KEY_PATTERN: Final[str] = "^[a-z][a-z0-9_]{0,31}$"
CAPABILITY_ID_SCHEMA_PATTERN: Final[str] = (
    "^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){1,3}$"
)
ON_FAILURE_VALUES: Final[tuple[str, ...]] = ("abort", "escalate")

# Closed vocabulary of facts a precondition / expected outcome may talk
# about. They are ASSERTIONS the future execution gate must re-evaluate;
# a plan never carries evidence or results.
PREDICATE_FACTS: Final[dict[str, str]] = {
    "account.status": "string",
    "account.days_overdue": "integer",
    "episode.eligible_mark_overdue": "boolean",
    "episode.recommendable": "boolean",
    "work.active_escalation_exists": "boolean",
}
PREDICATE_OPS_BY_TYPE: Final[dict[str, tuple[str, ...]]] = {
    "string": ("eq", "ne"),
    "boolean": ("eq", "ne"),
    "integer": ("eq", "ne", "gte", "lte"),
}

PROPOSAL_FIELDS: Final[tuple[str, ...]] = (
    "contract",
    "registry_version",
    "objective_ref",
    "plan_version",
    "summary",
    "steps",
)
STEP_FIELDS: Final[tuple[str, ...]] = (
    "step_key",
    "capability_id",
    "capability_version",
    "params",
    "depends_on",
    "preconditions",
    "expected_outcomes",
    "on_failure",
    "rationale",
)

_PREDICATE_SKELETON: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fact", "op", "value"],
    "properties": {
        "fact": {"type": "string"},
        "op": {"type": "string"},
        "value": {"type": ["string", "boolean", "integer"]},
    },
}


def skeleton_schema() -> dict[str, Any]:
    """
    Generic closed structure used by validator stage 3. It deliberately
    carries NO size or capability knowledge, so that limits (stage 4),
    capabilities (stage 5) and parameters (stage 6) produce their own
    specific codes instead of one opaque schema failure.
    """
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "additionalProperties": False,
        "required": list(PROPOSAL_FIELDS),
        "properties": {
            "contract": {"type": "string"},
            "registry_version": {"type": "string"},
            "objective_ref": {
                "type": "string",
                "pattern": OBJECTIVE_REF_PATTERN,
            },
            "plan_version": {"type": "integer", "minimum": 1},
            "summary": {"type": "string"},
            "steps": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(STEP_FIELDS),
                    "properties": {
                        "step_key": {
                            "type": "string",
                            "pattern": STEP_KEY_PATTERN,
                        },
                        "capability_id": {
                            "type": "string",
                            "pattern": CAPABILITY_ID_SCHEMA_PATTERN,
                        },
                        "capability_version": {
                            "type": "integer",
                            "minimum": 1,
                        },
                        "params": {"type": "object"},
                        "depends_on": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "pattern": STEP_KEY_PATTERN,
                            },
                        },
                        "preconditions": {
                            "type": "array",
                            "items": _PREDICATE_SKELETON,
                        },
                        "expected_outcomes": {
                            "type": "array",
                            "items": _PREDICATE_SKELETON,
                        },
                        "on_failure": {"enum": list(ON_FAILURE_VALUES)},
                        "rationale": {"type": "string"},
                    },
                },
            },
        },
    }


def predicate_problem(predicate: dict[str, Any]) -> str | None:
    """Fixed-token description of what is wrong with a predicate."""
    value_type = PREDICATE_FACTS.get(predicate["fact"])

    if value_type is None:
        return "fact"

    if predicate["op"] not in PREDICATE_OPS_BY_TYPE[value_type]:
        return "op"

    value = predicate["value"]
    matches = {
        "boolean": isinstance(value, bool),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
    }[value_type]

    if not matches:
        return "value_type"

    if value_type == "integer" and value < 0:
        return "value_range"

    return None


def _predicate_schema(limits: PlanLimits) -> dict[str, Any]:
    branches: list[dict[str, Any]] = []

    for fact, value_type in sorted(PREDICATE_FACTS.items()):
        value_schema: dict[str, Any] = {"type": value_type}

        if value_type == "integer":
            value_schema["minimum"] = 0

        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["fact", "op", "value"],
                "properties": {
                    "fact": {"const": fact},
                    "op": {"enum": list(PREDICATE_OPS_BY_TYPE[value_type])},
                    "value": value_schema,
                },
            }
        )

    return {"anyOf": branches}


def build_plan_schema(
    registry: CapabilityRegistry,
    limits: PlanLimits = PLAN_LIMITS,
    *,
    llm_view: bool = True,
) -> dict[str, Any]:
    """
    Server-side plan.v1 schema generated FROM the registry (one
    definition, no hand-copied second list). With llm_view only the
    capabilities a model may actually propose are offered. It is the
    document handed to the gateway as `output_schema`; the validator
    still runs afterwards and remains the authority on admission.
    Adapting it to a real provider's schema subset is out of scope.
    """
    variants = []

    for definition in registry.definitions:
        if not definition.registered or not definition.enabled:
            continue

        if llm_view and not definition.llm_proposable:
            continue

        variants.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": list(STEP_FIELDS),
                "properties": {
                    "step_key": {
                        "type": "string",
                        "pattern": STEP_KEY_PATTERN,
                    },
                    "capability_id": {"const": definition.capability_id},
                    "capability_version": {"const": definition.version},
                    "params": dict(definition.input_schema),
                    "depends_on": {
                        "type": "array",
                        "maxItems": limits.max_dependencies,
                        "items": {
                            "type": "string",
                            "pattern": STEP_KEY_PATTERN,
                        },
                    },
                    "preconditions": {
                        "type": "array",
                        "maxItems": limits.max_preconditions,
                        "items": {"$ref": "#/$defs/predicate"},
                    },
                    "expected_outcomes": {
                        "type": "array",
                        "maxItems": limits.max_expected_outcomes,
                        "items": {"$ref": "#/$defs/predicate"},
                    },
                    "on_failure": {"enum": list(ON_FAILURE_VALUES)},
                    "rationale": {
                        "type": "string",
                        "maxLength": limits.max_rationale_chars,
                    },
                },
            }
        )

    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "additionalProperties": False,
        "required": list(PROPOSAL_FIELDS),
        "properties": {
            "contract": {"const": CONTRACT},
            "registry_version": {"const": registry.version},
            "objective_ref": {
                "type": "string",
                "pattern": OBJECTIVE_REF_PATTERN,
            },
            "plan_version": {"type": "integer", "minimum": 1},
            "summary": {
                "type": "string",
                "maxLength": limits.max_summary_chars,
            },
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": limits.max_steps,
                "items": {"anyOf": variants},
            },
        },
        "$defs": {"predicate": _predicate_schema(limits)},
    }
