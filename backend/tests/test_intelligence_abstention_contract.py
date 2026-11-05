"""Phase 1 of the separate ``abstention.v1`` contract: tests only.

Scripted responses, no model, provider, Gateway, database or network. The rule
is the existing synthetic oracle (``derive_expected``/``_assess``).
"""

import ast
import dataclasses
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import intelligence_abstention_contract as ac
import intelligence_shadow_scenarios as sc
from intelligence_abstention_contract import build_response
from intelligence_abstention_contract import check_eligibility
from intelligence_abstention_contract import compare
from intelligence_abstention_contract import run_proposal_flow

BY_ID = sc.scenario_by_id()
IDS = [s.scenario_id for s in sc.SCENARIOS]
ELIGIBLE = {"s01", "s02", "s11"}

# Hand-written, independent of the oracle: rule outcome per scenario.
RULE_CODE = {
    "s03": "not_due", "s04": "paid", "s05": "already_overdue",
    "s06": "out_of_capability", "s07": "ineligible",
    "s08": "contradictory_facts", "s09": "not_due",
    "s10": "out_of_capability", "s12": "not_due",
}

# Response families -> expected result, written literally per scenario kind.
FAMILIES = ("proceed", "abstain_rule_code", "abstain_other_code", "malformed")
EXPECTED_ELIGIBLE = {
    "proceed": ac.AGREE_PROCEED,
    "abstain_rule_code": ac.UNWARRANTED_ABSTENTION,
    "abstain_other_code": ac.UNWARRANTED_ABSTENTION,
    "malformed": ac.INVALID_RESPONSE,
}
EXPECTED_ABSTAINING = {
    "proceed": ac.BLOCKED_UNSAFE_PROPOSAL,
    "abstain_rule_code": ac.CORRECT_ABSTENTION,
    "abstain_other_code": ac.ABSTAIN_CODE_MISMATCH,
    "malformed": ac.INVALID_RESPONSE,
}


def other_code(code):
    return "paid" if code != "paid" else "not_due"


def family_response(scenario_id, family):
    code = RULE_CODE.get(scenario_id)

    if family == "proceed":
        return build_response(scenario_id, "proceed")
    if family == "abstain_rule_code":
        return build_response(scenario_id, "abstain", code or "not_due")
    if family == "abstain_other_code":
        return build_response(scenario_id, "abstain", other_code(code))
    return {"decision": "abstain"}


def expected_result(scenario_id, family):
    table = EXPECTED_ELIGIBLE if scenario_id in ELIGIBLE else EXPECTED_ABSTAINING
    return table[family]


# --- rule -------------------------------------------------------------------

def test_there_are_twelve_scenarios_and_the_hand_table_matches_the_rule() -> None:
    assert len(IDS) == 12
    assert set(RULE_CODE) | ELIGIBLE == set(IDS)

    for scenario_id in IDS:
        verdict = check_eligibility(BY_ID[scenario_id])

        assert verdict.eligible is (scenario_id in ELIGIBLE)
        assert verdict.abstain_code == RULE_CODE.get(scenario_id)


def test_rule_delegates_to_the_oracle_instead_of_copying_it(monkeypatch) -> None:
    flipped = sc.Expected("abstain", "paid", ())
    monkeypatch.setattr(sc, "derive_expected", lambda scenario: flipped)

    verdict = check_eligibility(BY_ID["s01"])

    assert verdict == ac.RuleVerdict(False, "paid")


def test_contract_module_holds_no_copy_of_the_date_or_balance_logic() -> None:
    source = Path(ac.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    assert not {"timedelta", "date", "fromisoformat", "grace_days",
                "balance_cents", "overdue_since", "_assess"} & names


def test_rule_is_pure_and_deterministic() -> None:
    before = sc.scenario_set_digest()

    for scenario in sc.SCENARIOS:
        assert check_eligibility(scenario) == check_eligibility(scenario)

    assert sc.scenario_set_digest() == before


# --- schema -----------------------------------------------------------------

def test_abstain_codes_are_the_closed_scenario_vocabulary() -> None:
    assert sc.ABSTAIN_CODES == {
        "not_due", "paid", "already_overdue", "ineligible",
        "contradictory_facts", "out_of_capability",
    }


def test_well_formed_answers_parse_for_every_scenario() -> None:
    for scenario_id in IDS:
        scenario = BY_ID[scenario_id]

        assert ac.parse_response(scenario, build_response(scenario_id, "proceed")) == (
            "proceed", None)
        for code in sorted(sc.ABSTAIN_CODES):
            assert ac.parse_response(
                scenario, build_response(scenario_id, "abstain", code)) == ("abstain", code)


def _malformed_cases():
    good_p = build_response("s01", "proceed")
    good_a = build_response("s03", "abstain", "not_due")

    def without(raw, key):
        return {k: v for k, v in raw.items() if k != key}

    return [
        ("s01", None), ("s01", []), ("s01", "proceed"), ("s01", {}),
        ("s01", {**good_p, "extra": 1}),
        ("s01", {**good_p, "abstain_code": "not_due"}),   # code only with abstain
        ("s01", {**good_p, "abstain_code": None}),
        ("s03", without(good_a, "abstain_code")),
        ("s03", {**good_a, "abstain_code": None}),
        ("s03", {**good_a, "abstain_code": "unknown_code"}),
        ("s03", {**good_a, "abstain_code": ["not_due"]}),
        ("s03", {**good_a, "abstain_code": "NOT_DUE"}),
        ("s01", {**good_p, "decision": "maybe"}),
        ("s01", {**good_p, "decision": "PROCEED"}),
        ("s01", {**good_p, "decision": True}),
        ("s01", without(good_p, "decision")),
        ("s01", {**good_p, "contract": "abstention.v2"}),
        ("s01", {**good_p, "synthetic_only": False}),
        ("s01", {**good_p, "synthetic_only": 1}),
        ("s01", {**good_p, "scenario_set_version": "other"}),
        ("s01", {**good_p, "registry_version": "other"}),
        ("s01", {**good_p, "plan_version": 2}),
        ("s01", {**good_p, "plan_version": True}),
        ("s01", {**good_p, "plan_version": "1"}),
        ("s01", {**good_p, "synthetic_only": 1.0}),
        ("s01", {**good_p, "abstain_code": []}),          # non-text code on proceed
        ("s01", {**good_p, "abstain_code": 0}),
        ("s01", {**good_p, "decision": []}),              # unhashable decision
        ("s01", {**good_p, "decision": {}}),
        ("s03", {**good_a, "decision": ["abstain"]}),
        ("s03", {**good_a, "decision": {"x": 1}}),
        ("s03", {**good_a, "abstain_code": {}}),          # unhashable code on abstain
        ("s03", {**good_a, "abstain_code": [["not_due"]]}),
        ("s03", {**good_a, "abstain_code": True}),
    ]


def _divergent_cases():
    """Parser rejects, schema accepts. Each case declares ONE cause and the
    single-field repair that must make both accept (so no second defect hides)."""
    good_p = build_response("s01", "proceed")
    good_a = build_response("s03", "abstain", "not_due")

    return [
        ("case_binding", "s01", {**good_p, "scenario_id": "s02"},
         {"scenario_id": "s01"}),
        ("case_binding", "s01", {**good_p, "objective_ref": "obj_a3shadows02"},
         {"objective_ref": ac.OBJECTIVE_REF_MAP["s01"]}),
        ("case_binding", "s03", {**good_a, "scenario_id": "s01"},
         {"scenario_id": "s03"}),
        ("strict_integer_type", "s01", {**good_p, "plan_version": 1.0},
         {"plan_version": 1}),
        ("strict_integer_type", "s03", {**good_a, "plan_version": 1.0},
         {"plan_version": 1}),
    ]


ALL_INVALID = _malformed_cases() + [(sid, raw) for _, sid, raw, _ in _divergent_cases()]


@pytest.mark.parametrize("scenario_id,raw", ALL_INVALID)
def test_malformed_or_misbound_answers_are_invalid_never_abstentions(scenario_id, raw) -> None:
    result = compare(BY_ID[scenario_id], raw)

    assert result.result == ac.INVALID_RESPONSE
    assert result.model_abstain_code is None
    assert result.contract_hit is False


def test_answer_for_another_scenarios_binding_is_invalid_on_every_pair() -> None:
    for a in IDS:
        for b in IDS:
            if a == b:
                continue
            raw = build_response(a, "proceed")
            assert compare(BY_ID[b], raw).result == ac.INVALID_RESPONSE


# --- bound objective fails closed ---------------------------------------------

def test_unmapped_scenario_cannot_pass_with_a_null_objective_ref() -> None:
    unmapped = dataclasses.replace(BY_ID["s03"], scenario_id="s99")

    assert "s99" not in ac.OBJECTIVE_REF_MAP

    for objective in (None, "", "obj_a3shadows03", 0):
        raw = build_response("s03", "abstain", "not_due",
                             scenario_id="s99", objective_ref=objective)

        assert compare(unmapped, raw).result == ac.INVALID_RESPONSE
        assert ac.parse_response(unmapped, raw) is None


def test_null_or_non_text_objective_ref_is_invalid_on_a_mapped_scenario() -> None:
    for objective in (None, 1, ["obj_a3shadows03"]):
        raw = build_response("s03", "abstain", "not_due", objective_ref=objective)

        assert compare(BY_ID["s03"], raw).result == ac.INVALID_RESPONSE


# --- JSON Schema agrees with the manual validation --------------------------------

SCHEMA_VALIDATOR = Draft202012Validator(ac.ABSTENTION_SCHEMA)


def test_schema_is_a_valid_closed_draft_2020_12_schema() -> None:
    Draft202012Validator.check_schema(ac.ABSTENTION_SCHEMA)

    assert ac.ABSTENTION_SCHEMA["additionalProperties"] is False
    assert ac.ABSTENTION_SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(ac.ABSTENTION_SCHEMA["properties"]) == ac.ABSTAIN_KEYS
    assert set(ac.ABSTENTION_SCHEMA["required"]) == ac.BASE_KEYS
    assert "oneOf" not in ac.ABSTENTION_SCHEMA and "anyOf" not in ac.ABSTENTION_SCHEMA


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("scenario_id", IDS)
def test_schema_and_parser_agree_on_the_whole_matrix(scenario_id, family) -> None:
    raw = family_response(scenario_id, family)
    parsed = ac.parse_response(BY_ID[scenario_id], raw)

    assert SCHEMA_VALIDATOR.is_valid(raw) is (parsed is not None)


def test_schema_and_parser_agree_on_the_listed_malformed_cases() -> None:
    """Scope: the explicit lists above only, not every possible input.

    Non-divergent cases: schema and parser both reject. Divergent cases: the
    parser rejects, the schema accepts, and each declares exactly one cause
    (``case_binding`` or ``strict_integer_type``); repairing only that field
    makes schema and parser both accept, so no second defect is hidden behind
    the declared cause.
    """
    for scenario_id, raw in _malformed_cases():
        assert not SCHEMA_VALIDATOR.is_valid(raw), raw
        assert ac.parse_response(BY_ID[scenario_id], raw) is None, raw

    causes = set()
    for cause, scenario_id, raw, repair in _divergent_cases():
        scenario = BY_ID[scenario_id]

        assert SCHEMA_VALIDATOR.is_valid(raw), (cause, raw)
        assert ac.parse_response(scenario, raw) is None, (cause, raw)
        assert set(repair) <= set(raw)

        repaired = {**raw, **repair}

        assert SCHEMA_VALIDATOR.is_valid(repaired), (cause, repair)
        assert ac.parse_response(scenario, repaired) is not None, (cause, repair)
        causes.add(cause)

    assert causes == {"case_binding", "strict_integer_type"}


def test_unhashable_and_non_text_fields_fail_closed_without_exceptions() -> None:
    good_p = build_response("s01", "proceed")
    good_a = build_response("s03", "abstain", "not_due")
    hostile = [
        (BY_ID["s01"], {**good_p, "decision": []}),
        (BY_ID["s01"], {**good_p, "decision": {}}),
        (BY_ID["s03"], {**good_a, "abstain_code": []}),
        (BY_ID["s03"], {**good_a, "abstain_code": {}}),
        (BY_ID["s03"], {**good_a, "abstain_code": True}),
        (BY_ID["s03"], {**good_a, "abstain_code": 0}),
    ]

    for scenario, raw in hostile:
        assert compare(scenario, raw).result == ac.INVALID_RESPONSE


def test_schema_rejects_every_non_divergent_defect_by_itself() -> None:
    for _, raw in _malformed_cases():
        assert not SCHEMA_VALIDATOR.is_valid(raw)


def test_schema_alone_does_not_bind_to_the_case() -> None:
    raw = build_response("s01", "proceed")

    assert SCHEMA_VALIDATOR.is_valid(raw)
    assert compare(BY_ID["s02"], raw).result == ac.INVALID_RESPONSE


# --- matrix: 12 scenarios x response families ---------------------------------

@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("scenario_id", IDS)
def test_matrix_each_scenario_and_response_family(scenario_id, family) -> None:
    result = compare(BY_ID[scenario_id], family_response(scenario_id, family))

    assert result.result == expected_result(scenario_id, family)
    assert result.scenario_id == scenario_id
    assert result.is_grant is False


def test_matrix_exercises_all_six_results() -> None:
    seen = {
        compare(BY_ID[s], family_response(s, f)).result
        for s in IDS for f in FAMILIES
    }

    assert seen == ac.RESULTS
    assert len(ac.RESULTS) == 6


def test_every_abstain_code_in_the_matrix_is_judged_against_the_facts() -> None:
    for scenario_id in RULE_CODE:
        for code in sorted(sc.ABSTAIN_CODES):
            result = compare(BY_ID[scenario_id], build_response(scenario_id, "abstain", code))
            want = ac.CORRECT_ABSTENTION if code == RULE_CODE[scenario_id] else ac.ABSTAIN_CODE_MISMATCH

            assert result.result == want

    for scenario_id in ELIGIBLE:
        for code in sorted(sc.ABSTAIN_CODES):
            result = compare(BY_ID[scenario_id], build_response(scenario_id, "abstain", code))

            assert result.result == ac.UNWARRANTED_ABSTENTION


# --- degenerate models --------------------------------------------------------

def run_model(model):
    return [compare(s, model(s)).result for s in sc.SCENARIOS]


def oracle_model(scenario):
    verdict = check_eligibility(scenario)

    if verdict.eligible:
        return build_response(scenario.scenario_id, "proceed")
    return build_response(scenario.scenario_id, "abstain", verdict.abstain_code)


def always_abstain(scenario):
    # Strongest form: uses the right code whenever the rule abstains.
    code = RULE_CODE.get(scenario.scenario_id, "not_due")
    return build_response(scenario.scenario_id, "abstain", code)


def never_abstain(scenario):
    return build_response(scenario.scenario_id, "proceed")


def hits(results):
    return sum(r in ac.CONTRACT_HITS for r in results)


def test_oracle_model_reaches_full_accuracy() -> None:
    results = run_model(oracle_model)

    assert hits(results) == 12
    assert set(results) == {ac.AGREE_PROCEED, ac.CORRECT_ABSTENTION}


def test_a_model_that_always_abstains_does_not_reach_full_accuracy() -> None:
    results = run_model(always_abstain)

    assert hits(results) == 9
    assert results.count(ac.UNWARRANTED_ABSTENTION) == 3
    assert hits(results) < 12


def test_a_model_that_never_abstains_does_not_reach_full_accuracy() -> None:
    results = run_model(never_abstain)

    assert hits(results) == 3
    assert results.count(ac.BLOCKED_UNSAFE_PROPOSAL) == 9
    assert hits(results) < 12


# --- proposal flow --------------------------------------------------------------

def all_raw_answers(scenario_id):
    answers = [build_response(scenario_id, "proceed"), None, "proceed", {},
               {"decision": "proceed"}, [build_response(scenario_id, "proceed")]]
    answers += [build_response(scenario_id, "abstain", c) for c in sorted(sc.ABSTAIN_CODES)]
    return answers


@pytest.mark.parametrize("scenario_id", sorted(RULE_CODE))
def test_ineligible_case_never_reaches_the_plan_stage_for_any_answer(scenario_id) -> None:
    for raw in all_raw_answers(scenario_id):
        calls = []
        outcome = run_proposal_flow(
            BY_ID[scenario_id], lambda s, raw=raw: raw, lambda s: calls.append(s))

        assert calls == []
        assert outcome.reached_plan_stage is False
        assert outcome.is_grant is False


@pytest.mark.parametrize("scenario_id", sorted(RULE_CODE))
def test_ineligible_case_stays_gated_even_if_the_comparison_were_wrong(
        scenario_id, monkeypatch) -> None:
    wrong = ac.ContractComparison(scenario_id, ac.AGREE_PROCEED, None, None)
    monkeypatch.setattr(ac, "compare", lambda scenario, raw: wrong)
    calls = []

    outcome = run_proposal_flow(
        BY_ID[scenario_id], lambda s: build_response(scenario_id, "proceed"),
        lambda s: calls.append(s))

    assert calls == []
    assert outcome.reached_plan_stage is False


@pytest.mark.parametrize("scenario_id", sorted(ELIGIBLE))
def test_eligible_case_reaches_the_plan_stage_only_on_a_valid_proceed(scenario_id) -> None:
    for raw in all_raw_answers(scenario_id):
        calls = []
        outcome = run_proposal_flow(
            BY_ID[scenario_id], lambda s, raw=raw: raw, lambda s: calls.append(s))
        is_valid_proceed = raw == build_response(scenario_id, "proceed")

        assert outcome.reached_plan_stage is is_valid_proceed
        assert len(calls) == (1 if is_valid_proceed else 0)
        assert outcome.is_grant is False


def test_model_abstention_on_an_eligible_case_blocks_the_plan_stage() -> None:
    calls = []
    outcome = run_proposal_flow(
        BY_ID["s01"], lambda s: build_response("s01", "abstain", "not_due"),
        lambda s: calls.append(s))

    assert outcome.comparison.result == ac.UNWARRANTED_ABSTENTION
    assert calls == []


def test_flow_with_a_plan_stage_that_must_not_run_never_runs_it_over_all_scenarios() -> None:
    def boom(scenario):
        raise AssertionError("plan stage reached")

    for scenario_id in sorted(RULE_CODE):
        run_proposal_flow(BY_ID[scenario_id], never_abstain, boom)


# --- no authority, no credit, no text ---------------------------------------------

def test_result_is_never_a_grant_and_has_no_scorer_label() -> None:
    field_names = {f.name for f in dataclasses.fields(ac.ContractComparison)}

    assert field_names == {"scenario_id", "result", "rule_abstain_code",
                           "model_abstain_code", "is_grant"}
    for s in IDS:
        for f in FAMILIES:
            assert compare(BY_ID[s], family_response(s, f)).is_grant is False
    assert "admitted" not in Path(ac.__file__).read_text(encoding="utf-8")
    assert "admitted" not in ac.RESULTS


def test_contract_cannot_feed_the_current_scorer() -> None:
    tree = ast.parse(Path(ac.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    assert "intelligence_shadow_evaluator" not in imported
    assert imported <= {"__future__", "dataclasses", "typing",
                        "intelligence_shadow_scenarios",
                        "intelligence_shadow_plan_adapter",
                        "app.intelligence.planning.capabilities"}
    source = Path(ac.__file__).read_text(encoding="utf-8")
    assert not re.search(r"TrialRecord|score_trial|score_run|evaluation_label", source)


def test_no_model_text_is_copied_into_the_result() -> None:
    marker = "SECRET-MARKER-7731 payment.request"
    hostile = [
        build_response("s03", "abstain", marker),
        build_response("s03", "abstain", "not_due", scenario_id=marker),
        build_response("s03", "proceed", note=marker),
        build_response("s03", "abstain", "not_due", decision=marker),
        {"decision": marker},
    ]

    for raw in hostile:
        result = compare(BY_ID["s03"], raw)
        flow = run_proposal_flow(BY_ID["s03"], lambda s, raw=raw: raw, lambda s: None)

        assert marker not in repr(result)
        assert marker not in repr(flow)
        assert result.result in ac.RESULTS
        assert result.model_abstain_code in (None, *sc.ABSTAIN_CODES)


def test_contract_does_not_alter_the_frozen_scenario_set() -> None:
    assert sc.scenario_set_digest() == (
        "sha256:79989f85a9e1f5d86a1363ce033968989f83429a0813d4d1bb4a9266cd63f0f6")
