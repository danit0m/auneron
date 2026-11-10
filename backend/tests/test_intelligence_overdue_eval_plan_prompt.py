"""Stage 2 prompt of the overdue evaluation (adjustment approved on 2026-11-10): offline checks.

Synthetic data only. No model, Ollama, network or database. Nothing here is evidence of model
quality: it only checks that the rule written in the prompt is the rule of the scorer, that the
plan the prompt describes is accepted by the adapter and the real validator, and that the
evaluation rule does not depend on the runtime financial policy.
"""

import ast
import dataclasses
import datetime as dt
import itertools
import json
from pathlib import Path

import pytest

import intelligence_overdue_eval_runner as rn
import intelligence_shadow_evaluator as ev
import intelligence_shadow_scenarios as sc
from test_intelligence_plan_validator import ALL_PERMISSIONS
from test_intelligence_plan_validator import build_real_policy_reference

from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.codes import STAGE_OF
from app.intelligence.planning.codes import Stage
from app.intelligence.planning.schema import CONTRACT

TESTS = Path(__file__).resolve().parent
FACT = {"fact": "episode.eligible_mark_overdue", "op": "eq", "value": True}
FROZEN = sc.scenario_by_id()


# --- a) the rule of the prompt is the rule of the scorer --------------------------------------------------

def prompt_rule(account: sc.AccountFacts, as_of: dt.date) -> bool:
    """The five conditions of the "Rule" line of PROMPT_PLAN, written independently of the scorer."""
    overdue_on = dt.date.fromisoformat(account.due_date) + dt.timedelta(days=account.grace_days + 1)
    return (as_of >= overdue_on and account.paid is False and account.status == "current"
            and account.hold is False and account.balance_cents > 0)


def grid():
    due = dt.date(2026, 9, 10)
    for grace, paid, balance, status, hold, since, offset in itertools.product(
            (0, 1, 3), (True, False), (-1, 0, 1, 5000), ("current", "overdue"), (True, False),
            (None, "2026-09-20"), range(-2, 8)):
        account = sc.AccountFacts("ACC-G", due.isoformat(), grace, paid, balance, status, hold, since)
        yield account, due + dt.timedelta(days=grace + 1 + offset)


def assessed(account, as_of):
    # Every account of the grid is well formed: the scorer must answer, never raise.
    return sc._assess(account, as_of)


def test_the_rule_line_of_the_prompt_equals_the_scorer_on_an_exhaustive_grid() -> None:
    checked = mutations = 0
    for account, as_of in grid():
        if account.balance_cents < 0:
            continue  # outside the domain: the dataset loader refuses it (next test)
        result = assessed(account, as_of)
        if result is None:
            continue
        checked += 1
        is_mutation = isinstance(result, sc.Mutation)
        mutations += is_mutation
        assert prompt_rule(account, as_of) is is_mutation, (account, as_of, result)
    assert checked > 1000 and 0 < mutations < checked


def test_a_negative_balance_never_reaches_the_model_so_the_rule_line_is_exact_on_the_domain() -> None:
    # The prompt says balance_cents greater than 0; the scorer alone would still give a mutation for a
    # negative balance on an otherwise due account. That account is outside the domain of this
    # evaluation: `negative_balance` is an excluded combination and the dataset loader refuses any
    # balance below 0, so on every account that can reach the model the two rules are the same.
    account = sc.AccountFacts("ACC-G", "2026-09-10", 3, False, -1, "current", False, None)
    as_of = dt.date(2026, 9, 14)
    assert isinstance(sc._assess(account, as_of), sc.Mutation)
    assert prompt_rule(account, as_of) is False
    assert "negative_balance" in rn.EXCLUDED_COMBINATIONS
    raw = {"ref": "CTA-990201", "due_date": "2026-09-10", "grace_days": 3, "paid": False,
           "status": "current", "balance_cents": -1, "overdue_since": None, "hold": False}
    assert set(raw) == rn.ACCOUNT_KEYS
    with pytest.raises(rn.DatasetError, match="balance_cents"):
        rn._load_account(raw, "c1")
    raw["balance_cents"] = 1
    assert rn._load_account(raw, "c1").balance_cents == 1  # the same account with a valid balance loads
    assert all(a.balance_cents >= 0 for s in sc.SCENARIOS for a in s.accounts)


def test_the_rule_line_is_in_the_prompt_and_states_the_five_conditions() -> None:
    assert ("Rule: an account becomes overdue on due_date + grace_days + 1. An account may be marked "
            "overdue only when as_of is on or after that date, paid is false, status is current, hold "
            "is false and balance_cents is greater than 0.\n") in rn.PROMPT_PLAN


# --- b) the recipe plan is accepted ------------------------------------------------------------------------

def synthetic(scenario_id):
    """A frozen scenario with its references swapped for CTA-9901xx ones (plan form acct_cta9901xx)."""
    scenario = FROZEN[scenario_id]
    refs = {a.ref: f"CTA-9901{i:02d}" for i, a in enumerate(scenario.accounts, start=1)}
    accounts = tuple(dataclasses.replace(a, ref=refs[a.ref]) for a in scenario.accounts)
    expected = dataclasses.replace(scenario.expected, mutations=tuple(
        dataclasses.replace(m, target_ref=refs[m.target_ref]) for m in scenario.expected.mutations))
    return dataclasses.replace(scenario, accounts=accounts, expected=expected)


def step(key, capability, params, depends_on=(), preconditions=(), rationale="Motivo."):
    return {
        "step_key": key, "capability_id": capability, "capability_version": 1,
        "params": dict(params), "depends_on": list(depends_on),
        "preconditions": [dict(p) for p in preconditions], "expected_outcomes": [],
        "on_failure": "abort", "rationale": rationale,
    }


def params_of(account):
    return {"account_ref": rn.plan_ref(account.ref), "due_date": account.due_date}


def recipe(scenario, account, summary="Mark the account overdue.", rationale="Mark the account overdue."):
    params = params_of(account)
    return {
        "contract": CONTRACT, "registry_version": REGISTRY_V1.version,
        "objective_ref": rn.objective_ref_for(scenario.scenario_id), "plan_version": 1,
        "summary": summary,
        "steps": [
            step("evaluate", "action_space.evaluate", params, rationale="Check the account."),
            step("mark", "account.mark_overdue", params, depends_on=["evaluate"],
                 preconditions=[FACT], rationale=rationale),
        ],
    }


def eligible_account(scenario):
    (mutation,) = scenario.expected.mutations
    return next(a for a in scenario.accounts if a.ref == mutation.target_ref)


@pytest.fixture(scope="module")
def validator():
    return rn.make_real_validator(build_real_policy_reference(), ALL_PERMISSIONS)


@pytest.mark.parametrize("scenario_id", ["s01", "s02", "s11"])
def test_the_recipe_plan_maps_to_exactly_the_expected_mutation(scenario_id) -> None:
    scenario = synthetic(scenario_id)
    plan = recipe(scenario, eligible_account(scenario))
    normalized = rn.normalize_plan(scenario, plan, frozenset())

    assert normalized.status == "mapped" and normalized.code is None
    (mutation,) = scenario.expected.mutations
    mutating = [s for s in normalized.output["steps"] if s["effective_date"] is not None]
    assert mutating == [{"capability": mutation.capability, "target_ref": mutation.target_ref,
                         "effective_date": mutation.effective_date}]


@pytest.mark.parametrize("scenario_id", ["s01", "s02", "s11"])
def test_the_recipe_plan_passes_the_real_validator_up_to_the_parameters_stage(
        scenario_id, validator) -> None:
    scenario = synthetic(scenario_id)
    plan = recipe(scenario, eligible_account(scenario))
    admission = validator(scenario, json.dumps(plan))

    assert not admission.internal_defect
    early = [getattr(v.code, "value", v.code) for v in admission.violations
             if STAGE_OF[v.code] <= Stage.PARAMETERS]
    assert early == []


@pytest.mark.parametrize("scenario_id", ["s01", "s02", "s11"])
def test_the_recipe_dependency_is_what_the_real_validator_requires(scenario_id, validator) -> None:
    scenario = synthetic(scenario_id)
    plan = recipe(scenario, eligible_account(scenario))
    assert plan["steps"][1]["depends_on"] == ["evaluate"]
    assert [v.code for v in validator(scenario, json.dumps(plan)).violations] == []

    plan["steps"][1]["depends_on"] = []
    codes = [getattr(v.code, "value", v.code) for v in validator(scenario, json.dumps(plan)).violations]
    assert codes == ["effect_eligibility_dependency_missing"]


# --- c) the free text trap ---------------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["summary", "rationale"])
@pytest.mark.parametrize("text", ["account.mark_overdue", "mark_overdue",
                                  "Run account.mark_overdue now."])
def test_a_capability_name_in_free_text_is_free_text_evidence(field, text) -> None:
    scenario = synthetic("s01")
    plan = recipe(scenario, eligible_account(scenario), **{field: text})
    normalized = rn.normalize_plan(scenario, plan, frozenset())

    assert normalized.status == "refused" and normalized.code == rn.FREE_TEXT_EVIDENCE


def test_plain_words_in_free_text_are_mapped() -> None:
    scenario = synthetic("s01")
    plan = recipe(scenario, eligible_account(scenario), summary="Mark the account overdue.",
                  rationale="Mark the account overdue.")

    assert rn.normalize_plan(scenario, plan, frozenset()).status == "mapped"


# --- d) a read-only plan for an abstention case is not a mutating proposal --------------------------------

def test_a_read_only_plan_for_a_hold_case_is_not_a_mutating_proposal() -> None:
    scenario = synthetic("s07")
    account = scenario.accounts[0]
    plan = {
        "contract": CONTRACT, "registry_version": REGISTRY_V1.version,
        "objective_ref": rn.objective_ref_for(scenario.scenario_id), "plan_version": 1,
        "summary": "The account is on hold.",
        "steps": [step("evaluate", "nba.evaluate", params_of(account), rationale="Check the account.")],
    }
    normalized = rn.normalize_plan(scenario, plan, frozenset())

    # Current behaviour, recorded: the adapter maps the read step to account.read and the plan is
    # "mapped" (outcome "propose") with NO mutating step; it carries no mutation, so the scorer finds
    # no unwarranted mutation. The abstention itself is judged by stage 1, not by this plan.
    assert normalized.status == "mapped" and normalized.code is None
    assert normalized.output["steps"] == [
        {"capability": "account.read", "target_ref": account.ref, "effective_date": None}]
    assert ev.unwarranted_mutations(scenario, normalized.output) == 0


# --- e) the evaluation rule does not import the runtime financial policy -----------------------------------

ALLOWED_APP_IMPORTS = {
    "intelligence_overdue_eval_runner.py": {
        "app.intelligence.gateway.contracts",
        "app.intelligence.planning.authority",
        "app.intelligence.planning.capabilities",
        "app.intelligence.planning.schema",
        "app.intelligence.planning.validator",
    },
    "intelligence_shadow_scenarios.py": set(),
    "intelligence_abstention_contract.py": {"app.intelligence.planning.capabilities"},
    "intelligence_overdue_eval_plan_format.py": {"app.intelligence.planning.schema"},
    # the frozen adapter and scorer also decide what counts as correct
    "intelligence_shadow_plan_adapter.py": {
        "app.intelligence.planning.capabilities",
        "app.intelligence.planning.schema",
    },
    "intelligence_shadow_evaluator.py": set(),
}


def app_imports(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.split(".")[0] == "app"}
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.split(".")[0] == "app":
                found.add(node.module)
                found |= {f"{node.module}.{a.name}" for a in node.names
                          if f"{node.module}.{a.name}" in FORBIDDEN_RUNTIME}
    return found


FORBIDDEN_RUNTIME = {
    "app.core.governed_financial_action_eligibility", "app.core.receivable_lifecycle",
    "app.services.overdue_detection_service",
}


EVALUATION_MODULES = sorted(
    {p.name for pattern in ("intelligence_overdue_eval_*.py", "intelligence_shadow_*.py",
                            "intelligence_abstention_contract.py", "run_overdue_eval.py")
     for p in TESTS.glob(pattern)})


def test_the_boundary_covers_every_evaluation_module() -> None:
    assert set(ALLOWED_APP_IMPORTS) <= set(EVALUATION_MODULES)
    assert {"intelligence_overdue_eval_transport.py", "intelligence_overdue_eval_http.py",
            "run_overdue_eval.py"} <= set(EVALUATION_MODULES)


@pytest.mark.parametrize("name", EVALUATION_MODULES)
def test_the_evaluation_rule_imports_no_runtime_financial_policy(name) -> None:
    # A module not listed above may import nothing from app.* (empty allowlist by default).
    extra = app_imports(TESTS / name) - ALLOWED_APP_IMPORTS.get(name, set())
    assert not extra, (
        f"{name} imports {sorted(extra)} from app.*. The rule of this evaluation is versioned "
        "separately (freeze 3, a3-02.shadow-scenarios.v2) from the financial policy of the runtime "
        "(shared business rules). Any new app.* import, above all a financial policy module, needs "
        "its own version, freeze, dataset and round, and an equivalence table, before it is allowed.")
