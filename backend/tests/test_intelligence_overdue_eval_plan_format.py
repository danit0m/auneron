"""Plan-format round of the overdue evaluation (Area 3), offline.

Covers: the `format` of the plan stage (a JSON Schema generated from the plan.v1 source of truth),
its equivalence with the structural stages of the real validator on a SYNTHETIC corpus written
here, `think` off, the manifest coverage of the new schema and module, and the final prompts.

No model, Ollama, network, database or dataset is used. Nothing here is evidence of model quality.
"""

import copy
import dataclasses
import hashlib
import json
import random
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import intelligence_abstention_contract as ac
import intelligence_overdue_eval_plan_format as pf
import intelligence_overdue_eval_runner as rn
import intelligence_overdue_eval_transport as tr
import intelligence_shadow_scenarios as sc

from app.intelligence.planning.capabilities import PINNED_REGISTRY_DIGEST
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.capabilities import snapshot_registry
from app.intelligence.planning.codes import STAGE_OF
from app.intelligence.planning.codes import Stage
from app.intelligence.planning.schema import build_plan_schema
from app.intelligence.planning.validator import validate_plan

ACCT = "acct_k4m7q2"
DUE = "2026-09-30"
OBJ = "obj_synth0001"
PRE = {"fact": "episode.eligible_mark_overdue", "op": "eq", "value": True}


# --- a synthetic corpus, written here --------------------------------------------------------------------

def step(key, cap, params, deps=(), pre=(), out=(), version=1, on_failure="abort", rationale="synthetic"):
    return {"step_key": key, "capability_id": cap, "capability_version": version,
            "params": dict(params), "depends_on": list(deps), "preconditions": [dict(p) for p in pre],
            "expected_outcomes": [dict(p) for p in out], "on_failure": on_failure, "rationale": rationale}


def ctx(key="ctx", **kw):
    return step(key, "account.get_context", {"account_ref": ACCT}, **kw)


def nba(key="nba", deps=("ctx",), **kw):
    return step(key, "nba.evaluate", {"account_ref": ACCT, "due_date": DUE}, deps=deps, **kw)


def mark(key="mark", deps=("nba",), pre=(PRE,), **kw):
    return step(key, "account.mark_overdue", {"account_ref": ACCT, "due_date": DUE}, deps=deps, pre=pre, **kw)


def plan(steps=None, **over):
    base = {"contract": "plan.v1", "registry_version": "capreg.v1", "objective_ref": OBJ,
            "plan_version": 1, "summary": "synthetic plan",
            "steps": steps if steps is not None else [ctx(), nba(), mark()]}
    base.update(over)
    return base


def with_step(index=0, **over):
    p = plan()
    p["steps"][index].update(over)
    return p


def without(p, *path):
    p = copy.deepcopy(p)
    node = p
    for part in path[:-1]:
        node = node[part]
    del node[path[-1]]
    return p


def put(p, path, value):
    p = copy.deepcopy(p)
    node = p
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    return p


def many(count):
    return [ctx(key=f"s{i}") for i in range(count)]


# Declared gaps: well-formed per the schema, refused by the validator on a rule the schema cannot state.
GAP_CODES = {
    "calendar_date": {"params_semantic_invalid"},
    "string_content": {"string_not_nfc", "string_control_character"},
    "integer_magnitude": {"integer_out_of_range"},
    "float_integer": {"value_float_forbidden"},
}
ALL_GAP_CODES = set().union(*GAP_CODES.values())

OK_PLANS = [
    ("read_only_chain", plan([ctx(), nba()]), None),
    ("mark_chain", plan(), None),
    ("single_context_step", plan([ctx()]), None),
    ("four_read_capabilities", plan([
        ctx(), nba(), step("act", "action_space.evaluate", {"account_ref": ACCT, "due_date": DUE}, deps=["ctx"]),
        step("mem", "memory.recall", {"account_ref": ACCT, "query": "q", "limit": 3}, deps=["ctx"])]), None),
    ("eight_steps", plan(many(8)), None),
    ("plan_version_2", plan(plan_version=2), None),
    ("largest_valid_integer", plan(plan_version=2**53 - 1), None),
    ("memory_limit_1", plan([step("mem", "memory.recall", {"account_ref": ACCT, "query": "q", "limit": 1})]), None),
    ("memory_limit_10", plan([step("mem", "memory.recall", {"account_ref": ACCT, "query": "q", "limit": 10})]), None),
    ("memory_query_200", plan([step("mem", "memory.recall", {"account_ref": ACCT, "query": "q" * 200, "limit": 2})]), None),
    ("escalate_on_failure", plan([ctx(on_failure="escalate")]), None),
    ("three_dependencies", plan([ctx("a"), ctx("b"), ctx("c"), ctx("d", deps=["a", "b", "c"])]), None),
    ("four_preconditions_and_outcomes", plan([ctx(pre=[PRE] * 4, out=[PRE] * 4)]), None),
    ("all_predicate_facts", plan([ctx(pre=[
        {"fact": "account.status", "op": "ne", "value": "paid"},
        {"fact": "account.days_overdue", "op": "gte", "value": 3},
        {"fact": "episode.recommendable", "op": "ne", "value": False},
        {"fact": "work.active_escalation_exists", "op": "eq", "value": False}])]), None),
    ("summary_280", plan(summary="s" * 280), None),
    ("summary_280_multibyte", plan(summary="é" * 280), None),
    ("rationale_400", plan([ctx(rationale="r" * 400)]), None),
    ("step_key_32", plan([ctx(key="k" * 32)]), None),
    ("objective_ref_32", plan(objective_ref="obj_" + "a" * 32), None),
    # refused by LATER stages (dag/effects/data/authority): structurally fine, so the schema accepts them
    ("later_duplicate_step_key", plan([ctx("x"), ctx("x")]), None),
    ("later_cycle", plan([ctx("a", deps=["b"]), ctx("b", deps=["a"])]), None),
    ("later_unknown_dependency", plan([ctx(deps=["nope"])]), None),
    ("later_two_mutating_steps", plan([ctx(), nba(), mark("m1"), mark("m2")]), None),
    ("later_mark_without_precondition", plan([ctx(), nba(), mark(pre=())]), None),
    ("later_token_like_summary", plan(summary="a" * 40), None),
]
OK_PLANS += [
    ("gap_calendar_date", plan([step("nba", "nba.evaluate", {"account_ref": ACCT, "due_date": "2026-02-30"})]),
     "calendar_date"),
    ("gap_not_nfc_summary", plan(summary="é"), "string_content"),
    ("gap_control_character", plan([ctx(rationale="a\x01b")]), "string_content"),
    ("gap_integer_above_max", plan(plan_version=2**53), "integer_magnitude"),
    ("gap_float_integer", None, "float_integer"),  # built as text below
]

BAD_PLANS = [
    ("contract_other", put(plan(), ["contract"], "other.v1")),
    ("contract_unsupported_version", put(plan(), ["contract"], "plan.v2")),
    ("registry_version_other", put(plan(), ["registry_version"], "capreg.v2")),
    ("server_field_digest", put(plan(), ["execution_digest"], "a" * 64)),
    ("extra_top_key", put(plan(), ["extra"], 1)),
    ("steps_empty", plan([])),
    ("steps_nine", plan(many(9))),
    ("steps_not_a_list", put(plan(), ["steps"], {})),
    ("step_not_an_object", put(plan(), ["steps"], ["x"])),
    ("objective_short", plan(objective_ref="obj_abcdefg")),
    ("objective_upper", plan(objective_ref="obj_ABCDEFGH")),
    ("objective_too_long", plan(objective_ref="obj_" + "a" * 33)),
    ("plan_version_zero", plan(plan_version=0)),
    ("plan_version_bool", plan(plan_version=True)),
    ("plan_version_text", plan(plan_version="1")),
    ("summary_281", plan(summary="s" * 281)),
    ("summary_null", plan(summary=None)),
    ("step_extra_key", with_step(0, extra=1)),
    ("step_key_empty", with_step(0, step_key="")),
    ("step_key_digit_first", with_step(0, step_key="1a")),
    ("step_key_upper", with_step(0, step_key="Ab")),
    ("step_key_33", with_step(0, step_key="k" * 33)),
    ("capability_unknown", with_step(0, capability_id="account.nothing")),
    ("capability_id_bad_shape", with_step(0, capability_id="Bad Id")),
    ("capability_version_2", with_step(0, capability_version=2)),
    ("capability_version_zero", with_step(0, capability_version=0)),
    ("capability_version_bool", with_step(0, capability_version=True)),
    ("capability_disabled_mark_paid", plan([step("p", "account.mark_paid", {"account_ref": ACCT, "due_date": DUE})])),
    ("capability_not_proposable_escalate",
     plan([step("e", "work.escalate_to_human", {"account_ref": ACCT, "due_date": DUE})])),
    ("params_missing_due_date", plan([step("nba", "nba.evaluate", {"account_ref": ACCT})])),
    ("params_extra_key", plan([step("c", "account.get_context", {"account_ref": ACCT, "x": 1})])),
    ("params_wrong_capability_shape",
     plan([step("c", "account.get_context", {"account_ref": ACCT, "due_date": DUE})])),
    ("params_bad_ref", plan([step("c", "account.get_context", {"account_ref": "acct_UPPER"})])),
    ("params_ref_short", plan([step("c", "account.get_context", {"account_ref": "acct_abc"})])),
    ("params_bad_due_shape", plan([step("n", "nba.evaluate", {"account_ref": ACCT, "due_date": "30/09/2026"})])),
    ("memory_limit_0", plan([step("m", "memory.recall", {"account_ref": ACCT, "query": "q", "limit": 0})])),
    ("memory_limit_11", plan([step("m", "memory.recall", {"account_ref": ACCT, "query": "q", "limit": 11})])),
    ("memory_limit_bool", plan([step("m", "memory.recall", {"account_ref": ACCT, "query": "q", "limit": True})])),
    ("memory_query_empty", plan([step("m", "memory.recall", {"account_ref": ACCT, "query": "", "limit": 2})])),
    ("memory_query_201", plan([step("m", "memory.recall", {"account_ref": ACCT, "query": "q" * 201, "limit": 2})])),
    ("params_null", with_step(0, params=None)),
    ("depends_on_four", plan([ctx("a"), ctx("b"), ctx("c"), ctx("d"), ctx("e", deps=["a", "b", "c", "d"])])),
    ("depends_on_bad_key", with_step(0, depends_on=["A"])),
    ("depends_on_not_a_list", with_step(0, depends_on="ctx")),
    ("preconditions_five", plan([ctx(pre=[PRE] * 5)])),
    ("expected_outcomes_five", plan([ctx(out=[PRE] * 5)])),
    ("predicate_unknown_fact", plan([ctx(pre=[{"fact": "x.y", "op": "eq", "value": 1}])])),
    ("predicate_op_gte_on_boolean", plan([ctx(pre=[{"fact": "episode.recommendable", "op": "gte", "value": True}])])),
    ("predicate_op_gte_on_string", plan([ctx(pre=[{"fact": "account.status", "op": "gte", "value": "a"}])])),
    ("predicate_op_unknown", plan([ctx(pre=[{"fact": "account.status", "op": "like", "value": "a"}])])),
    ("predicate_value_type", plan([ctx(pre=[{"fact": "account.days_overdue", "op": "eq", "value": "3"}])])),
    ("predicate_bool_for_integer", plan([ctx(pre=[{"fact": "account.days_overdue", "op": "eq", "value": True}])])),
    ("predicate_negative_integer", plan([ctx(pre=[{"fact": "account.days_overdue", "op": "gte", "value": -1}])])),
    ("predicate_extra_key", plan([ctx(pre=[{**PRE, "x": 1}])])),
    ("predicate_missing_value", plan([ctx(pre=[{"fact": "episode.recommendable", "op": "eq"}])])),
    ("predicate_null_value", plan([ctx(pre=[{"fact": "episode.recommendable", "op": "eq", "value": None}])])),
    ("on_failure_retry", with_step(0, on_failure="retry")),
    ("rationale_401", plan([ctx(rationale="r" * 401)])),
    ("rationale_null", with_step(0, rationale=None)),
]
for _key in ("contract", "registry_version", "objective_ref", "plan_version", "summary", "steps"):
    BAD_PLANS.append((f"missing_top_{_key}", without(plan(), _key)))
for _key in ("step_key", "capability_id", "capability_version", "params", "depends_on", "preconditions",
             "expected_outcomes", "on_failure", "rationale"):
    BAD_PLANS.append((f"missing_step_{_key}", without(plan(), "steps", 0, _key)))


def text_of(p):
    return json.dumps(p, ensure_ascii=False)


def corpus():
    """(name, text, expected structural verdict, declared gap)."""
    out = []
    for name, p, gap in OK_PLANS:
        text = text_of(p) if p is not None else text_of(plan()).replace('"plan_version": 1', '"plan_version": 1.0')
        out.append((name, text, gap is None, gap))
    out += [(name, text_of(p), False, None) for name, p in BAD_PLANS]
    return out


def structural_verdict(text):
    """What the real validator says about the STRUCTURE: no violation in stages 0..6.

    No authority and no policy are given, so a plan that passes every stage up to 9 ends at
    stage 10 (authority_context_invalid): that counts as structurally fine. The first failing
    stage ends the run, so all codes of one answer belong to one stage.
    """
    admission = validate_plan(text, registry=REGISTRY_V1, policy=None, authority=None)
    codes = {getattr(v.code, "value", v.code) for v in admission.violations}
    assert not admission.internal_defect
    return all(STAGE_OF[v.code] > Stage.PARAMETERS for v in admission.violations), codes


@pytest.fixture(scope="module")
def flat():
    return rn.plan_format_schema()


@pytest.fixture(scope="module")
def flat_validator(flat):
    return Draft202012Validator(flat)


@pytest.fixture(scope="module")
def source_validator():
    return Draft202012Validator(build_plan_schema(REGISTRY_V1))


# --- the schema: valid, deterministic, from the source of truth ---------------------------------------------

def test_the_plan_format_is_a_valid_json_schema_and_is_deterministic(flat) -> None:
    Draft202012Validator.check_schema(flat)
    again = rn.plan_format_schema()
    other = pf.plan_format_schema(snapshot_registry(REGISTRY_V1))

    assert flat == again == other
    assert rn.canonical(flat) == rn.canonical(again) == rn.canonical(other)
    assert tr.encode({"format": flat}) == tr.encode({"format": again})
    assert rn.canonical_hash(flat) == rn.canonical_hash(other)


def test_the_plan_format_is_derived_from_the_registry_and_not_copied() -> None:
    source = build_plan_schema(REGISTRY_V1, llm_view=True)
    limits = REGISTRY_V1.limits
    flat = rn.plan_format_schema()
    steps = flat["properties"]["steps"]
    variants = steps["items"]["anyOf"]
    wanted = [d for d in REGISTRY_V1.definitions if d.registered and d.enabled and d.llm_proposable]

    assert flat["properties"]["registry_version"] == {"const": REGISTRY_V1.version}
    assert flat["properties"]["contract"] == {"const": "plan.v1"}
    assert (steps["minItems"], steps["maxItems"]) == (1, limits.max_steps)
    assert flat["properties"]["summary"]["maxLength"] == limits.max_summary_chars
    assert [v["properties"]["capability_id"]["const"] for v in variants] == [d.capability_id for d in wanted]
    assert [v["properties"]["params"] for v in variants] == [dict(d.input_schema) for d in wanted]
    assert {"account.mark_paid", "work.escalate_to_human"}.isdisjoint(json.dumps(flat))
    for variant in variants:
        assert variant["properties"]["depends_on"]["maxItems"] == limits.max_dependencies
        assert variant["properties"]["preconditions"]["maxItems"] == limits.max_preconditions
        assert variant["properties"]["expected_outcomes"]["maxItems"] == limits.max_expected_outcomes
        assert variant["properties"]["rationale"]["maxLength"] == limits.max_rationale_chars
        assert variant["properties"]["preconditions"]["items"] == source["$defs"]["predicate"]
        assert variant["properties"]["expected_outcomes"]["items"] == source["$defs"]["predicate"]


def test_the_plan_format_has_no_references_and_only_the_documented_keywords(flat) -> None:
    text = json.dumps(flat)
    for forbidden in ("$ref", "$defs", "$schema"):
        assert forbidden not in text
    used = pf.keywords_used(flat)

    assert used == set(pf.FORMAT_KEYWORDS)
    assert used.isdisjoint(pf.AVOIDED_KEYWORDS)
    assert "anyOf" in used and "oneOf" not in used and "allOf" not in used


def test_the_format_module_imports_nothing_but_the_standard_library_and_the_plan_schema() -> None:
    import ast

    tree = ast.parse(Path(pf.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    assert imported == {"__future__", "copy", "typing", "app.intelligence.planning.schema"}


def test_the_keyword_walk_ignores_property_names_and_the_inliner_refuses_a_loose_ref() -> None:
    assert pf.keywords_used({"properties": {"type": {"type": "string"}, "format": {"const": 1}}}) == {
        "properties", "type", "const"}
    assert pf.keywords_used({"anyOf": [{"items": {"pattern": "x"}}]}) == {"anyOf", "items", "pattern"}
    with pytest.raises(ValueError, match="unresolvable_ref"):
        pf._inline({"$ref": "#/$defs/nope"}, {"predicate": {}})
    with pytest.raises(ValueError, match="unresolvable_ref"):
        pf._inline({"$ref": "http://elsewhere/x"}, {"x": {}})
    with pytest.raises(ValueError, match="ref_with_siblings"):
        pf._inline({"$ref": "#/$defs/p", "type": "string"}, {"p": {}})
    assert pf._inline({"a": [{"$ref": "#/$defs/p"}]}, {"p": {"type": "string"}}) == {"a": [{"type": "string"}]}


def test_a_wrong_dialect_or_a_dangling_ref_is_refused(monkeypatch) -> None:
    real = pf.build_plan_schema

    monkeypatch.setattr(pf, "build_plan_schema", lambda r, llm_view=True: {**real(r, llm_view=llm_view), "$schema": "x"})
    with pytest.raises(ValueError, match="unexpected_dialect"):
        pf.plan_format_schema(REGISTRY_V1)
    monkeypatch.setattr(pf, "build_plan_schema", lambda r, llm_view=True: {
        **real(r, llm_view=llm_view), "properties": {"steps": {"$ref": "#/$defs/missing"}}})
    with pytest.raises(ValueError, match="unresolvable_ref"):
        pf.plan_format_schema(REGISTRY_V1)


def test_the_runner_builds_the_format_only_from_the_pinned_registry(monkeypatch) -> None:
    bad = dataclasses.replace(REGISTRY_V1.definitions[0], enabled=False, llm_proposable=False)
    monkeypatch.setattr(rn, "REGISTRY_V1", type(REGISTRY_V1)(
        (bad, *REGISTRY_V1.definitions[1:]), version=REGISTRY_V1.version, limits=REGISTRY_V1.limits))
    assert PINNED_REGISTRY_DIGEST == "46f77593c243cc02047a354ba636e806a6e94a1ba10e9b8db0fdcd9cf31d3a3f"
    with pytest.raises(rn.AdapterIntegrityError):
        rn.plan_format_schema()


# --- equivalence with the structural stages of the real validator -----------------------------------------

def test_the_corpus_is_big_and_balanced() -> None:
    cases = corpus()
    names = [c[0] for c in cases]

    assert len(names) == len(set(names)) >= 100
    assert sum(1 for c in cases if c[2]) >= 25 and sum(1 for c in cases if not c[2]) >= 60
    assert {c[3] for c in cases if c[3]} == set(GAP_CODES)


def test_the_format_accepts_and_refuses_exactly_what_the_validator_does_on_the_corpus(
        flat_validator, source_validator) -> None:
    for name, text, expected, gap in corpus():
        structural, codes = structural_verdict(text)
        document = json.loads(text)
        verdict = flat_validator.is_valid(document)

        assert structural is expected, (name, codes)  # the corpus means what it says
        assert verdict == source_validator.is_valid(document), name  # flattening changes nothing
        if gap is None:
            assert verdict is structural, (name, codes)
        else:
            # declared gap: the schema cannot state the rule, the validator still refuses it
            assert verdict is True and structural is False, name
            assert codes <= GAP_CODES[gap] and codes, (name, codes)


def test_the_format_and_the_validator_agree_on_seeded_random_mutations(flat_validator) -> None:
    rng = random.Random(20261110)
    junk = [None, True, False, 0, -1, 1, 2, 2**53, "", "x", "acct_zzzzzz", "2026-02-30", [], {}, ["a"], 1.5,
            "é" * 500, "é", "account.mark_overdue", "obj_a3eval0001"]
    bases = [plan(), plan([ctx(), nba()]), plan([step("mem", "memory.recall",
                                                       {"account_ref": ACCT, "query": "q", "limit": 3})]),
             plan([ctx(pre=[PRE, {"fact": "account.days_overdue", "op": "gte", "value": 3}])])]
    compared = {True: 0, False: 0}
    skipped = 0

    def locations(node, path=()):
        found = [path] if path else []
        if isinstance(node, dict):
            for key, value in node.items():
                found += locations(value, path + (key,))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found += locations(value, path + (index,))
        return found

    for _ in range(600):
        document = copy.deepcopy(rng.choice(bases))
        for _ in range(rng.choice([1, 1, 2])):
            path = rng.choice(locations(document))
            parent = document
            for part in path[:-1]:
                parent = parent[part]
            action = rng.choice(["replace", "replace", "delete", "add"])
            if action == "replace":
                parent[path[-1]] = rng.choice(junk)
            elif action == "delete" and isinstance(parent, dict):
                del parent[path[-1]]
            elif action == "add" and isinstance(parent, dict):
                parent[f"extra{rng.randrange(3)}"] = rng.choice(junk)
        text = text_of(document)
        structural, codes = structural_verdict(text)
        if codes and codes <= ALL_GAP_CODES:
            skipped += 1  # a gap code ends the run: later stages are unknown, nothing to compare
            continue
        assert flat_validator.is_valid(json.loads(text)) is structural, (text, codes)
        compared[structural] += 1

    assert compared[True] >= 20 and compared[False] >= 300 and skipped < 150, (compared, skipped)


# --- the request: `format` on both stages, think off ------------------------------------------------------

def request(stage, prompt="P"):
    return rn.ModelRequest(stage, "c001", 0, prompt,
                           {"temperature": 0.0, "seed": 1000, "num_predict": 160}, 60)


def test_both_stages_send_their_own_format_and_think_stays_off() -> None:
    abstention = tr.build_body(request(rn.STAGE_ABSTENTION), "qwen3:4b")
    plan_body = tr.build_body(request(rn.STAGE_PLAN), "qwen3:4b")

    assert abstention["format"] == ac.ABSTENTION_SCHEMA
    assert plan_body["format"] == rn.plan_format_schema() == pf.plan_format_schema(snapshot_registry(REGISTRY_V1))
    assert plan_body["format"] != abstention["format"]
    assert tr.THINK is False and abstention["think"] is False and plan_body["think"] is False
    assert plan_body["stream"] is True and plan_body["options"]["num_predict"] == 160
    sent = json.loads(tr.encode(plan_body))
    assert sent["format"] == plan_body["format"] and sent["think"] is False


def test_an_unknown_stage_gets_no_format() -> None:
    assert "format" not in tr.build_body(request("other"), "qwen3:4b")


# --- the manifest covers the schema and the module that builds it -----------------------------------------

def test_the_schema_hash_covers_the_plan_format(monkeypatch) -> None:
    before = rn.schema_sha256()
    monkeypatch.setattr(rn, "plan_format_schema", lambda: {"type": "object"})

    assert rn.schema_sha256() != before
    monkeypatch.setattr(rn, "plan_format_schema", lambda: {**pf.plan_format_schema(snapshot_registry(REGISTRY_V1))})
    assert rn.schema_sha256() == before


def test_the_manifest_pins_the_plan_format_module() -> None:
    hashes = rn.source_hashes()

    assert hashes["plan_format"] == rn.file_sha256(Path(pf.__file__)) and "plan_format" in rn.REQUIRED_ENTRIES
    entries = {"schema": rn.schema_sha256(), "prompt": rn.prompt_sha256(), "dataset": "d", "protocol": "p", **hashes}
    manifest = rn.build_manifest(entries, {})
    assert {e["name"]: e["sha256"] for e in manifest["entries"]}["plan_format"] == hashes["plan_format"]
    assert rn.verify_manifest(manifest) == manifest["manifest_hash"]
    with pytest.raises(ValueError, match="plan_format"):
        rn.build_manifest({k: v for k, v in entries.items() if k != "plan_format"}, {})
    forged = copy.deepcopy(manifest)
    forged["entries"] = [e for e in forged["entries"] if e["name"] != "plan_format"]
    with pytest.raises(rn.ManifestTampered):
        rn.verify_manifest(forged)


# --- the final prompts -------------------------------------------------------------------------------------

APPROVED_ABSTENTION = [
    "You check whether one account may be marked overdue under the rule below.",
    "Reply with exactly one JSON object that matches the schema. Do not write any reasoning, explanation or text outside the JSON object.",
    "Rule: an account becomes overdue on due_date + grace_days + 1.",
    'Answer "abstain" with exactly one abstain_code when any of these holds:',
    "- not_due: the overdue date has not been reached yet.",
    "- paid: the account is already paid.",
    "- already_overdue: the account is already overdue.",
    "- ineligible: there is an explicit hold, or the account is unpaid and current, would otherwise be due, and its balance_cents is exactly 0 (this goes to human review).",
    "- contradictory_facts: the facts contradict each other, for example paid with a balance, or overdue without a start date.",
    "- out_of_capability: the request is outside this capability, for example a payment request, or an account overdue for 30 days or more.",
    'Otherwise answer "proceed" with no abstain_code.',
    "JSON schema of the answer:",
    "{{SCHEMA}}",
    "Facts:",
    "{{FACTS}}",
]
APPROVED_PLAN = [
    "Write a plan.v1 proposal for the request described in the facts.",
    "Reply with exactly one JSON object that follows the plan contract. Do not write any reasoning, explanation or text outside the JSON object.",
    "Use only account_ref values that appear in the facts. A plan with only read steps is allowed.",
    "Plan contract:",
    "{{PLAN_CONTRACT}}",
    "Facts:",
    "{{FACTS}}",
]


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_final_prompts_are_the_approved_text_line_by_line() -> None:
    assert rn.PROMPT_ABSTENTION == "".join(line + "\n" for line in APPROVED_ABSTENTION)
    assert rn.PROMPT_PLAN == "".join(line + "\n" for line in APPROVED_PLAN)
    assert (rn.SCHEMA_TOKEN, rn.FACTS_TOKEN, rn.CONTRACT_TOKEN) == ("{{SCHEMA}}", "{{FACTS}}", "{{PLAN_CONTRACT}}")
    for template in (rn.PROMPT_ABSTENTION, rn.PROMPT_PLAN):
        assert template.count("{{FACTS}}") == 1 and template.endswith("{{FACTS}}\n")
        assert "DRAFT" not in template and "ONLY" not in template
    assert rn.PROMPT_ABSTENTION.count("{{SCHEMA}}") == 1 and "{{PLAN_CONTRACT}}" not in rn.PROMPT_ABSTENTION
    assert rn.PROMPT_PLAN.count("{{PLAN_CONTRACT}}") == 1 and "{{SCHEMA}}" not in rn.PROMPT_PLAN


def test_the_prompt_hash_changed_with_the_final_text_and_still_tracks_each_part(monkeypatch) -> None:
    # The draft prompts hashed differently. Nothing in the suite pinned the draft hash; this pins the NEW
    # templates, so a silent edit of either one fails here (the hash of the pair is frozen in the manifest).
    assert sha(rn.PROMPT_ABSTENTION) == "b58c73b78e4472879ab407c878764431d5f8049144ae3e430bab163ef3994cb3"
    assert sha(rn.PROMPT_PLAN) == "34ef36922e4b4caa7c717645150d2f002529de9a294d5161ba7e2a3f255e520c"
    base = rn.prompt_sha256()
    assert len(base) == 64
    monkeypatch.setattr(rn, "PROMPT_PLAN", rn.PROMPT_PLAN.replace("Write", "Make"))
    assert rn.prompt_sha256() != base


SYNTHETIC = sc.Scenario(
    scenario_id="z900", case="zz_case_name", request_kind="assess_overdue", as_of="2026-10-10",
    accounts=(sc.AccountFacts(ref="CTA-900001", due_date="2026-09-01", grace_days=3, paid=False,
                              balance_cents=1234, status="current", hold=False, overdue_since=None),),
    canary_refs=("CTA-900999",), notes=("synthetic note",),
    expected=sc.Expected("abstain", "not_due", ()))


def test_rendered_prompts_hold_only_the_template_and_the_three_markers_content() -> None:
    facts = json.dumps(rn.facts_view(SYNTHETIC), sort_keys=True, indent=1)
    schema = json.dumps(ac.ABSTENTION_SCHEMA, sort_keys=True)
    contract = rn.render_plan_contract()
    head_a = "".join(line + "\n" for line in APPROVED_ABSTENTION[:12])
    head_p = "".join(line + "\n" for line in APPROVED_PLAN[:4])

    assert rn.render_prompt(rn.STAGE_ABSTENTION, SYNTHETIC) == head_a + schema + "\nFacts:\n" + facts + "\n"
    assert rn.render_prompt(rn.STAGE_PLAN, SYNTHETIC) == head_p + contract + "\nFacts:\n" + facts + "\n"
    for stage in (rn.STAGE_ABSTENTION, rn.STAGE_PLAN):
        prompt = rn.render_prompt(stage, SYNTHETIC)
        assert "{{" not in prompt  # every marker was replaced
        for hidden in ("zz_case_name", "CTA-900001", "CTA-900999", "abstain_code", "expected", "canary",
                       "mutations", "family"):
            assert hidden not in prompt.split("Facts:\n")[1], hidden
        assert json.loads(prompt.split("Facts:\n")[1]) == rn.facts_view(SYNTHETIC)
    assert "acct_cta900001" in rn.render_prompt(rn.STAGE_PLAN, SYNTHETIC)


# --- the protocol says what was done, and what was not -----------------------------------------------------

PROTOCOL = Path(__file__).resolve().parents[1] / "docs" / "intelligence" / "OVERDUE_EVAL_PROTOCOL.md"


def test_protocol_section_18_records_the_diagnosis_the_limits_and_the_declared_gaps() -> None:
    whole = PROTOCOL.read_text(encoding="utf-8")
    text = whole.split("## 18. ", 1)[1]
    keywords = text.split("Palavras-chave do schema final", 1)[1].split("Evitadas de proposito", 1)[0]

    for needle in ("Qwen3-4B-Thinking-2507", "general.finetune=Thinking", "think: false` NAO desliga",
                   "`format` nas DUAS etapas", "0.40.15", "19 a 20 tokens/s", "33 s", "3 tokens/s",
                   "SEM raciocinar", "NAO verificado", "segunda prova curta", "DECLARED_GAPS",
                   "`plan_format`", "opcao A", "congelamento 4"):
        assert needle in text, needle
    assert {w for w in pf.FORMAT_KEYWORDS if f"`{w}`" in keywords} == set(pf.FORMAT_KEYWORDS)
    assert "(15, exatamente)" in keywords and len(pf.FORMAT_KEYWORDS) == 15
    from app.intelligence.planning.codes import Stage

    names = ", ".join(s.name for s in sorted(Stage) if s <= Stage.PARAMETERS)
    assert names == "REGISTRY_INTEGRITY, INPUT, CONTRACT, SCHEMA, LIMITS, CAPABILITIES, PARAMETERS"
    flat_text = " ".join(text.split())
    assert "REGISTRY_INTEGRITY, INPUT, CONTRACT, SCHEMA, LIMITS, CAPABILITIES e PARAMETERS" in flat_text
    for gap_word in ("2026-02-30", "NFC", "max_integer", "1.0"):
        assert gap_word in text
    head = whole.split("## 5. ", 1)[0]
    assert "proposta final aprovada pelo\ndiretor em 2026-11-10 (opcao A)" in head
    assert "sao RASCUNHO" not in head
