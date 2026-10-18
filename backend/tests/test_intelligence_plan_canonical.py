"""INTELLIGENCE-PLANNING -- strict parsing, canonicalization and digests."""

import copy
import json
import unicodedata

import pytest

from app.intelligence.planning import canonical as c
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.codes import RejectionCode

from test_intelligence_plan_validator import good_plan
from test_intelligence_plan_validator import step_of


pytestmark = pytest.mark.contract

RD = REGISTRY_V1.digest


def parse_code(raw):
    with pytest.raises(c.InputError) as caught:
        c.strict_parse(raw)

    return caught.value.code


def test_strict_parse_negative_vectors():
    assert parse_code(b'{"a":1,"a":2}') is RejectionCode.INPUT_DUPLICATE_KEY
    assert parse_code(b'{"a":{"b":1,"b":2}}') is RejectionCode.INPUT_DUPLICATE_KEY
    assert parse_code(b'{"a":NaN}') is RejectionCode.INPUT_NON_FINITE_NUMBER
    assert parse_code(b'{"a":-Infinity}') is RejectionCode.INPUT_NON_FINITE_NUMBER
    assert parse_code(b"{") is RejectionCode.INPUT_NOT_JSON
    assert parse_code(b"") is RejectionCode.INPUT_NOT_JSON
    assert parse_code(b"[1]") is RejectionCode.INPUT_NOT_OBJECT
    assert parse_code(b'"x"') is RejectionCode.INPUT_NOT_OBJECT
    assert parse_code(b"\xff") is RejectionCode.INPUT_NOT_UTF8
    assert parse_code(b'{"a":"\xc3"}') is RejectionCode.INPUT_NOT_UTF8
    assert parse_code(b" " * 32769) is RejectionCode.INPUT_TOO_LARGE
    assert parse_code(12345) is RejectionCode.INPUT_NOT_JSON
    assert parse_code("\ud800") is RejectionCode.INPUT_NOT_UTF8


def test_size_is_checked_before_any_decoding():
    # invalid UTF-8 larger than the limit is TOO_LARGE, not NOT_UTF8
    assert parse_code(b"\xff" * 40_000) is RejectionCode.INPUT_TOO_LARGE
    assert c.strict_parse(b" " * 32760 + b"{}") == {}


def test_strict_parse_accepts_str_bytes_and_bytearray():
    assert c.strict_parse('{"a":1}') == {"a": 1}
    assert c.strict_parse(b'{"a":1}') == {"a": 1}
    assert c.strict_parse(bytearray(b'{"a":1}')) == {"a": 1}


def test_deep_nesting_maps_to_a_typed_code_not_a_crash():
    raw = b'{"a":' + b"[" * 4000 + b"]" * 4000 + b"}"

    assert parse_code(raw) in {
        RejectionCode.INPUT_NESTING_TOO_DEEP,
        RejectionCode.INPUT_TOO_LARGE,
    }
    assert parse_code(b"[" * 5000) is RejectionCode.INPUT_NESTING_TOO_DEEP


def codes(value):
    violations, _ = c.scan_value(value)
    return sorted({v.code for v in violations})


def test_value_rules():
    assert codes({"a": None}) == ["value_null_forbidden"]
    assert codes({"a": 1.5}) == ["value_float_forbidden"]
    assert codes({"a": 2.0}) == ["value_float_forbidden"]
    assert codes({"a": 2**53 - 1}) == []
    assert codes({"a": 2**53}) == ["integer_out_of_range"]
    assert codes({"a": -(2**53)}) == ["integer_out_of_range"]
    assert codes({"a": True, "b": False}) == []
    assert codes({"a": "x\x00"}) == ["string_control_character"]
    assert codes({"a": "x\x7f"}) == ["string_control_character"]
    assert codes({"a": "x\n"}) == ["string_control_character"]
    assert codes({"a\x01": 1}) == ["string_control_character"]
    assert codes({"a": "é"}) == ["string_not_nfc"]
    assert codes({"é": 1}) == ["string_not_nfc"]
    assert codes({"a": "x\ud800"}) == ["string_encoding_invalid"]
    assert codes({"a": ["é", {"b": "ação"}]}) == []
    assert codes({1: "x"}) == ["schema_violation"]
    assert codes({"a": object()}) == ["schema_violation"]


def test_violations_are_sorted_and_carry_paths_not_values():
    violations, _ = c.scan_value({"b": None, "a": [1.5, None]})

    assert violations == sorted(violations)
    assert all("None" not in v.detail and "1.5" not in v.detail for v in violations)


def test_canonical_bytes_vectors():
    assert c.canonical_bytes({"b": 1, "a": [True, "é"]}) == '{"a":[true,"é"],"b":1}'.encode()
    assert c.canonical_bytes({"a": 1, "b": 2}) == c.canonical_bytes({"b": 2, "a": 1})
    assert c.canonical_bytes({}) == b"{}"
    # no ASCII escaping, so the bytes are exactly what the approval flow hashes
    assert "é".encode() in c.canonical_bytes({"x": "é"})
    assert b"\\u" not in c.canonical_bytes({"x": "é"})

    for bad in ({"a": 1.5}, {"a": None}, {"a": "é"}, {"a": float("nan")}):
        with pytest.raises(c.CanonicalError):
            c.canonical_bytes(bad)


def test_canonical_form_is_nfc_stable():
    composed = unicodedata.normalize("NFC", "ação")
    decomposed = unicodedata.normalize("NFD", "ação")

    assert composed != decomposed
    with pytest.raises(c.CanonicalError):
        c.canonical_bytes({"s": decomposed})
    assert c.canonical_bytes({"s": composed})


def test_digest_domain_separation():
    value = {"a": 1}
    domains = [
        c.DOMAIN_EXECUTION,
        c.DOMAIN_PRESENTATION,
        c.DOMAIN_REGISTRY,
        c.DOMAIN_CONTEXT,
        c.DOMAIN_IDEMPOTENCY,
    ]
    digests = {c.digest(d, value) for d in domains}

    assert len(digests) == len(domains)
    assert all(d.endswith(b"\n") for d in domains)
    assert len({d for d in domains}) == len(domains)


def test_seal_shape_and_determinism():
    sealed = c.seal(good_plan(), RD)

    assert set(sealed) == {"registry_digest", "execution_digest", "presentation_digest"}
    assert sealed == c.seal(copy.deepcopy(good_plan()), RD)
    assert len({sealed["execution_digest"], sealed["presentation_digest"]}) == 2
    assert all(len(v) == 64 for v in sealed.values())


# -- the 12 digest-invariance rows ------------------------------------


def change(fn):
    plan = good_plan()
    fn(plan)
    return plan


NOT_EXECUTION = {
    "summary": lambda p: p.__setitem__("summary", "Outro resumo."),
    "rationale": lambda p: step_of(p, "ctx").__setitem__("rationale", "outra"),
    "step_order": lambda p: p["steps"].reverse(),
}

EXECUTION = {
    "param": lambda p: step_of(p, "ctx")["params"].__setitem__("account_ref", "acct_aaaaaa"),
    "due_date": lambda p: step_of(p, "mark")["params"].__setitem__("due_date", "2026-10-01"),
    "capability": lambda p: step_of(p, "ctx").__setitem__("capability_id", "memory.recall"),
    "capability_version": lambda p: step_of(p, "ctx").__setitem__("capability_version", 2),
    "on_failure": lambda p: step_of(p, "ctx").__setitem__("on_failure", "escalate"),
    "precondition": lambda p: step_of(p, "mark").__setitem__("preconditions", []),
    "dependency": lambda p: step_of(p, "mark").__setitem__("depends_on", ["ctx"]),
    "expected_outcome": lambda p: step_of(p, "nba").__setitem__("expected_outcomes", []),
    "plan_version": lambda p: p.__setitem__("plan_version", 2),
    "objective_ref": lambda p: p.__setitem__("objective_ref", "obj_zzzzzzzz"),
    "step_key": lambda p: step_of(p, "ctx").__setitem__("step_key", "ctx2"),
}


def test_presentation_text_and_ordering_never_change_the_execution_digest():
    base = c.seal(good_plan(), RD)

    for name, fn in NOT_EXECUTION.items():
        sealed = c.seal(change(fn), RD)
        assert sealed["execution_digest"] == base["execution_digest"], name

    assert c.seal(change(NOT_EXECUTION["summary"]), RD)["presentation_digest"] != base["presentation_digest"]
    assert c.seal(change(NOT_EXECUTION["rationale"]), RD)["presentation_digest"] != base["presentation_digest"]
    assert c.seal(change(NOT_EXECUTION["step_order"]), RD)["presentation_digest"] == base["presentation_digest"]


def test_every_executable_field_changes_the_execution_digest():
    base = c.seal(good_plan(), RD)["execution_digest"]
    seen = {base}

    for name, fn in EXECUTION.items():
        value = c.seal(change(fn), RD)["execution_digest"]
        assert value != base, name
        seen.add(value)

    assert c.seal(good_plan(), "f" * 64)["execution_digest"] != base  # registry bound
    assert len(seen) == len(EXECUTION) + 1


def test_unordered_collections_are_order_independent():
    plan = good_plan()
    step_of(plan, "mark")["depends_on"] = ["nba", "ctx"]
    step_of(plan, "mark")["preconditions"] = [
        {"fact": "account.status", "op": "eq", "value": "open"},
        {"fact": "episode.eligible_mark_overdue", "op": "eq", "value": True},
    ]
    flipped = copy.deepcopy(plan)
    step_of(flipped, "mark")["depends_on"].reverse()
    step_of(flipped, "mark")["preconditions"].reverse()

    assert c.execution_digest(plan, RD) == c.execution_digest(flipped, RD)


def test_verify_seal_detects_tampering():
    sealed = dict(good_plan(), **c.seal(good_plan(), RD))

    assert c.verify_seal(sealed, RD) == []

    tampered = copy.deepcopy(sealed)
    step_of(tampered, "mark")["params"]["due_date"] = "2026-10-01"
    problems = c.verify_seal(tampered, RD)
    assert {v.code for v in problems} == {"digest_mismatch"}
    assert {v.path for v in problems} == {"$.execution_digest", "$.presentation_digest"}

    reworded = dict(sealed, summary="Texto alterado.")
    assert [v.path for v in c.verify_seal(reworded, RD)] == ["$.presentation_digest"]

    assert {v.code for v in c.verify_seal(sealed, "0" * 64)} == {"digest_mismatch"}
    assert [v.code for v in c.verify_seal({"steps": 1}, RD)] == ["digest_mismatch"]
    assert [v.code for v in c.verify_seal({}, RD)] == ["digest_mismatch"]


def test_a_model_cannot_pre_seal_a_plan():
    for name in c.SERVER_ONLY_FIELDS:
        assert name in {"registry_digest", "execution_digest", "presentation_digest"}


def test_idempotency_key_is_deterministic_and_content_bound():
    ex = c.execution_digest(good_plan(), RD)
    key = c.derive_idempotency_key("obj_k3m9x2ab", 1, ex)

    assert key == c.derive_idempotency_key("obj_k3m9x2ab", 1, ex)
    assert key != c.derive_idempotency_key("obj_k3m9x2ac", 1, ex)
    assert key != c.derive_idempotency_key("obj_k3m9x2ab", 2, ex)
    assert key != c.derive_idempotency_key("obj_k3m9x2ab", 1, "0" * 64)
    assert key != ex


def test_canonical_json_matches_the_approval_flow_algorithm():
    sample = {"b": [1, {"z": "é", "a": True}], "a": "x"}
    reference = json.dumps(
        sample, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")

    assert c.canonical_bytes(sample) == reference
