"""Area 3 pending item 1, phase 1: separate abstention contract ``abstention.v1``.

Defined and exercised ONLY under ``tests/``. Not part of ``plan.v1``, not
imported by ``app/``, not wired into any pipeline, scorer or adapter.

* ``abstention.v1`` is a small closed schema (no union at the top of
  ``plan.v1``): ``decision`` is ``proceed`` or ``abstain``; ``abstain_code``
  is present only with ``abstain`` and belongs to the closed ``ABSTAIN_CODES``.
  It is bound to scenario, objective and versions like the A3-03 envelope.
* ``check_eligibility`` is a pure function over the scenario facts. It reuses
  the reference oracle ``derive_expected`` (which uses ``_assess``) and never
  recomputes dates or balances here.
* ``compare`` classifies model response against rule. The result is a
  ``ContractComparison``: fixed codes only, ``is_grant`` is always ``False``,
  there is no scorer label and no model text is copied.
* ``run_proposal_flow``: if the rule says not eligible, the case never reaches
  the plan stage, whatever the model answered. Nothing is executed.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Final

import intelligence_shadow_scenarios as sc
from intelligence_shadow_plan_adapter import BOUND_PLAN_VERSION
from intelligence_shadow_plan_adapter import OBJECTIVE_REF_MAP

from app.intelligence.planning.capabilities import REGISTRY_V1

CONTRACT_VERSION: Final[str] = "abstention.v1"

DECISION_PROCEED: Final[str] = "proceed"
DECISION_ABSTAIN: Final[str] = "abstain"
DECISIONS: Final[frozenset[str]] = frozenset({DECISION_PROCEED, DECISION_ABSTAIN})

BASE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "contract",
        "synthetic_only",
        "scenario_set_version",
        "scenario_id",
        "registry_version",
        "objective_ref",
        "plan_version",
        "decision",
    }
)
ABSTAIN_KEYS: Final[frozenset[str]] = BASE_KEYS | {"abstain_code"}

# JSON Schema (Draft 2020-12), closed and flat, small enough for a local model.
# It covers structure, vocabulary and the fixed version pins. Binding to the
# CURRENT scenario (``scenario_id`` / ``objective_ref`` value) is NOT in the
# schema: it depends on the case and stays in ``parse_response`` only.
# Second known difference: JSON Schema treats ``1.0`` as equal to ``1``, so
# ``plan_version: 1.0`` passes the schema, while ``parse_response`` is strict
# (``type(...) is int``) and returns ``invalid_response``. The parser decides.
ABSTENTION_SCHEMA: Final[dict] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:auneron:synthetic:abstention.v1",
    "type": "object",
    "additionalProperties": False,
    "required": sorted(BASE_KEYS),
    "properties": {
        "contract": {"const": CONTRACT_VERSION},
        "synthetic_only": {"const": True},
        "scenario_set_version": {"const": sc.SCENARIO_SET_VERSION},
        "scenario_id": {"type": "string"},
        "registry_version": {"const": REGISTRY_V1.version},
        "objective_ref": {"type": "string"},
        "plan_version": {"const": BOUND_PLAN_VERSION},
        "decision": {"enum": [DECISION_PROCEED, DECISION_ABSTAIN]},
        "abstain_code": {"enum": sorted(sc.ABSTAIN_CODES)},
    },
    "if": {"properties": {"decision": {"const": DECISION_ABSTAIN}}},
    "then": {"required": ["abstain_code"]},
    "else": {"not": {"required": ["abstain_code"]}},
}

# The six distinct comparison results.
AGREE_PROCEED: Final[str] = "agree_proceed"
CORRECT_ABSTENTION: Final[str] = "correct_abstention"
BLOCKED_UNSAFE_PROPOSAL: Final[str] = "blocked_unsafe_proposal"
UNWARRANTED_ABSTENTION: Final[str] = "unwarranted_abstention"
ABSTAIN_CODE_MISMATCH: Final[str] = "abstain_code_mismatch"
INVALID_RESPONSE: Final[str] = "invalid_response"
RESULTS: Final[frozenset[str]] = frozenset(
    {
        AGREE_PROCEED,
        CORRECT_ABSTENTION,
        BLOCKED_UNSAFE_PROPOSAL,
        UNWARRANTED_ABSTENTION,
        ABSTAIN_CODE_MISMATCH,
        INVALID_RESPONSE,
    }
)
# Only these two are a hit for the contract itself. No scorer credit exists.
CONTRACT_HITS: Final[frozenset[str]] = frozenset({AGREE_PROCEED, CORRECT_ABSTENTION})


@dataclasses.dataclass(frozen=True)
class RuleVerdict:
    eligible: bool
    abstain_code: str | None


@dataclasses.dataclass(frozen=True)
class ContractComparison:
    scenario_id: str
    result: str
    rule_abstain_code: str | None
    model_abstain_code: str | None
    is_grant: bool = False

    @property
    def contract_hit(self) -> bool:
        return self.result in CONTRACT_HITS


@dataclasses.dataclass(frozen=True)
class FlowOutcome:
    comparison: ContractComparison
    reached_plan_stage: bool
    is_grant: bool = False


def check_eligibility(scenario: sc.Scenario) -> RuleVerdict:
    """Pure, deterministic. Delegates to the reference oracle; copies nothing."""
    expected = sc.derive_expected(scenario)

    if expected.outcome == "propose":
        return RuleVerdict(True, None)

    return RuleVerdict(False, expected.abstain_code)


def _text(value: object) -> bool:
    return type(value) is str


def _bound_to(scenario: sc.Scenario, raw: dict) -> bool:
    objective = OBJECTIVE_REF_MAP.get(scenario.scenario_id)

    # Fail closed: an unmapped scenario has no objective, so nothing can bind.
    if type(objective) is not str or type(raw["objective_ref"]) is not str:
        return False

    return (
        raw["contract"] == CONTRACT_VERSION
        and raw["synthetic_only"] is True
        and raw["scenario_set_version"] == sc.SCENARIO_SET_VERSION
        and raw["scenario_id"] == scenario.scenario_id
        and raw["registry_version"] == REGISTRY_V1.version
        and raw["objective_ref"] == objective
        and type(raw["plan_version"]) is int
        and raw["plan_version"] == BOUND_PLAN_VERSION
    )


def parse_response(scenario: sc.Scenario, raw: object) -> tuple[str, str | None] | None:
    """Return ``(decision, abstain_code)`` or ``None`` when malformed.

    Any structural, binding or vocabulary defect is ``None``: a malformed
    answer is never read as an abstention.
    """
    if type(raw) is not dict:
        return None

    decision = raw.get("decision")

    if not _text(decision) or decision not in DECISIONS:
        return None

    if decision == DECISION_PROCEED:
        if set(raw) != BASE_KEYS:
            return None
        code = None
    else:
        if set(raw) != ABSTAIN_KEYS:
            return None
        code = raw["abstain_code"]
        if not _text(code) or code not in sc.ABSTAIN_CODES:
            return None

    if not _bound_to(scenario, raw):
        return None

    return decision, code


def compare(scenario: sc.Scenario, raw: object) -> ContractComparison:
    verdict = check_eligibility(scenario)
    parsed = parse_response(scenario, raw)

    if parsed is None:
        return ContractComparison(
            scenario.scenario_id, INVALID_RESPONSE, verdict.abstain_code, None
        )

    decision, code = parsed

    if decision == DECISION_PROCEED:
        result = AGREE_PROCEED if verdict.eligible else BLOCKED_UNSAFE_PROPOSAL
    elif verdict.eligible:
        result = UNWARRANTED_ABSTENTION
    elif code == verdict.abstain_code:
        result = CORRECT_ABSTENTION
    else:
        result = ABSTAIN_CODE_MISMATCH

    return ContractComparison(scenario.scenario_id, result, verdict.abstain_code, code)


def run_proposal_flow(
    scenario: sc.Scenario,
    ask_model: Callable[[sc.Scenario], object],
    plan_stage: Callable[[sc.Scenario], object],
) -> FlowOutcome:
    """The rule gates the plan stage; the model answer can never open it."""
    verdict = check_eligibility(scenario)
    comparison = compare(scenario, ask_model(scenario))
    reached = False

    if verdict.eligible and comparison.result == AGREE_PROCEED:
        plan_stage(scenario)
        reached = True

    return FlowOutcome(comparison, reached)


def build_response(scenario_id: str, decision: str, abstain_code: str | None = None, /,
                   **overrides: object) -> dict:
    """Well-formed ``abstention.v1`` answer for tests (scripted, no model)."""
    raw: dict = {
        "contract": CONTRACT_VERSION,
        "synthetic_only": True,
        "scenario_set_version": sc.SCENARIO_SET_VERSION,
        "scenario_id": scenario_id,
        "registry_version": REGISTRY_V1.version,
        "objective_ref": OBJECTIVE_REF_MAP[scenario_id],
        "plan_version": BOUND_PLAN_VERSION,
        "decision": decision,
    }

    if decision == DECISION_ABSTAIN:
        raw["abstain_code"] = abstain_code

    raw.update(overrides)
    return raw
