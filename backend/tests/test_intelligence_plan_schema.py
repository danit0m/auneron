"""INTELLIGENCE-PLANNING -- strict plan.v1 contract and generated schema."""

import copy
import random

import pytest
from jsonschema import Draft202012Validator

from app.intelligence.planning import schema as s
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.capabilities import schema_is_closed

from test_intelligence_plan_validator import good_plan
from test_intelligence_plan_validator import policy  # noqa: F401
from test_intelligence_plan_validator import read_only_plan
from test_intelligence_plan_validator import step_of
from test_intelligence_plan_validator import validate


pytestmark = pytest.mark.contract


def test_the_generated_schema_is_a_valid_closed_draft_2020_12_schema():
    schema = s.build_plan_schema(REGISTRY_V1)

    Draft202012Validator.check_schema(schema)
    assert schema_is_closed(schema)
    assert schema["$schema"] == s.JSON_SCHEMA_DIALECT


def test_the_skeleton_schema_is_closed_too():
    skeleton = s.skeleton_schema()

    Draft202012Validator.check_schema(skeleton)
    assert skeleton["additionalProperties"] is False
    assert set(skeleton["required"]) == set(s.PROPOSAL_FIELDS)


def test_the_model_view_hides_server_fields_and_unusable_capabilities():
    text = repr(s.build_plan_schema(REGISTRY_V1))

    for hidden in (
        "execution_digest",
        "presentation_digest",
        "registry_digest",
        "account.mark_paid",  # disabled
        "work.escalate_to_human",  # not llm-proposable
    ):
        assert hidden not in text, hidden

    for offered in ("account.mark_overdue", "nba.evaluate", "memory.recall"):
        assert offered in text


def test_schema_and_layered_validator_agree_on_a_corpus(policy):  # noqa: F811
    schema = Draft202012Validator(s.build_plan_schema(REGISTRY_V1))
    rng = random.Random(5)
    corpus = [good_plan(), read_only_plan()]

    for _ in range(200):
        plan = copy.deepcopy(rng.choice(corpus))
        step = rng.choice(plan["steps"])
        field = rng.choice(["account_ref", "due_date", "query", "limit"])
        step["params"][field] = rng.choice(["acct_7f3a9c", "2026-09-30", "x", 3, 99, "", None])
        plan["plan_version"] = rng.choice([1, 2, 0, -1])
        corpus.append(plan)

    for plan in corpus:
        if validate(plan, policy).admitted:
            assert schema.is_valid(plan)  # layered admit => schema admit


def test_predicate_vocabulary_is_closed_and_typed():
    assert set(s.PREDICATE_FACTS) == {
        "account.status",
        "account.days_overdue",
        "episode.eligible_mark_overdue",
        "episode.recommendable",
        "work.active_escalation_exists",
    }
    ok = {"fact": "account.days_overdue", "op": "gte", "value": 3}

    assert s.predicate_problem(ok) is None
    assert s.predicate_problem({"fact": "x", "op": "eq", "value": 1})
    assert s.predicate_problem({"fact": "account.days_overdue", "op": "eq", "value": "3"})
    assert s.predicate_problem({"fact": "episode.eligible_mark_overdue", "op": "gte", "value": True})
    assert s.predicate_problem({"fact": "account.status", "op": "eq", "value": 1})


def test_proposal_and_step_fields_match_the_contract():
    assert s.CONTRACT == "plan.v1"
    assert set(s.PROPOSAL_FIELDS) >= {"contract", "registry_version", "objective_ref", "plan_version", "summary", "steps"}
    assert set(s.STEP_FIELDS) >= {"step_key", "capability_id", "capability_version", "params", "depends_on", "preconditions", "expected_outcomes", "on_failure", "rationale"}
    assert s.ON_FAILURE_VALUES == ("abort", "escalate")


def test_refs_are_opaque_by_pattern(policy):  # noqa: F811
    for bad in ("acct_", "acct_ABC123", "ACCT_abc123", "acct_abc 123", "12345", "acct_abc123456789abc", "acct_../etc"):
        plan = good_plan()
        step_of(plan, "ctx")["params"]["account_ref"] = bad
        assert not validate(plan, policy).admitted, bad

    for bad in ("obj_", "obj_ABCDEFGH", "obj_short", "x" * 12):
        plan = good_plan()
        plan["objective_ref"] = bad
        assert not validate(plan, policy).admitted, bad


def test_step_key_and_on_failure_rules(policy):  # noqa: F811
    for bad in ("", "1a", "A", "a-b", "a" * 33, "a b"):
        plan = read_only_plan()
        step_of(plan, "ctx")["step_key"] = bad
        assert not validate(plan, policy).admitted, bad

    plan = read_only_plan()
    step_of(plan, "ctx")["on_failure"] = "retry"
    assert not validate(plan, policy).admitted

    plan = read_only_plan()
    step_of(plan, "ctx")["on_failure"] = "escalate"
    assert validate(plan, policy).admitted


def test_boolean_is_never_an_integer(policy):  # noqa: F811
    plan = read_only_plan()
    plan["plan_version"] = True
    assert not validate(plan, policy).admitted

    plan = read_only_plan()
    step_of(plan, "ctx")["capability_version"] = True
    assert not validate(plan, policy).admitted

    mem = good_plan()
    mem["steps"].append(
        {
            **step_of(good_plan(), "ctx"),
            "step_key": "mem",
            "capability_id": "memory.recall",
            "params": {"account_ref": "acct_7f3a9c", "query": "q", "limit": True},
        }
    )
    assert not validate(mem, policy).admitted
