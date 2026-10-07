"""
SIM-1.5 -- gates estaticos do Evaluator offline: uma classe por expectativa,
adaptadores por kind (positivo/negativo), HARNESS_ERROR por omissao,
derivacao de proveniencia por exaustao, determinismo e zero rede.
"""

from __future__ import annotations

import ast
import json

import pytest

from sim.evaluator.evaluate import ABSTENTION
from sim.evaluator.evaluate import CORRECT
from sim.evaluator.evaluate import DIVERGENCE
from sim.evaluator.evaluate import FINDING
from sim.evaluator.evaluate import HARNESS_ERROR
from sim.evaluator.evaluate import KNOWN_LIMIT
from sim.evaluator.evaluate import NOT_REPRESENTABLE
from sim.evaluator.evaluate import PENDING
from sim.evaluator.evaluate import evaluate
from sim.tests.collector_support import load_oracle
from sim.tests.conftest import SIM_ROOT

CLOCK = {"d0": "2026-01-05", "utc_offset_minutes": -180}


def exp_of(kind, policy, *, day=3, subject=None, semantics="fact", **extra) -> dict:
    return {"exp_id": "E-1", "kind": kind, "day": day, "policy": policy, "semantics": semantics,
            "subject": subject or {"rec_ref": "R-1"}, "evidence_class": "PRODUCT_FACT", **extra}


def run(exp, reads=(), *, refs=None, driver=(), facts=None):
    """reads: [(route, path, status, body)] coletados para a expectativa."""
    plan_items, collected = [], []
    for number, (route, path, status, body) in enumerate(reads, start=1):
        item_id = f"I-{number:06d}"
        plan_items.append({"item_id": item_id, "route": route, "path": path, "satisfies": [exp["exp_id"]]})
        collected.append({"kind": "collected", "item_id": item_id, "route": route, "path": path, "status": status,
                          "body": body})
    plan = {"clock": CLOCK, "items": plan_items}
    oracle = {"oracle_version": "v2.1", "execution_scenario_id": "x", "expectations": [exp]}
    document = evaluate(oracle, plan, collected, list(driver), {"refs": refs or {}}, facts or {})
    return document["results"][0]["class"], document


ACCOUNT = ("account", "/accounts/1", 200)


def test_missing_collected_evidence_is_a_harness_error_and_invalidates_the_run():
    klass, document = run(exp_of("account_status", "atrasado"), [])
    assert klass == HARNESS_ERROR and document["run_valid"] is False
    plan_only = {"clock": CLOCK, "items": [{"item_id": "I-1", "route": "account", "path": "/accounts/1",
                                            "satisfies": ["E-1"]}]}
    oracle = {"oracle_version": "v2.1", "execution_scenario_id": "x", "expectations": [exp_of("account_status", "x")]}
    assert evaluate(oracle, plan_only, [], [], {"refs": {}}, {})["results"][0]["class"] == HARNESS_ERROR


@pytest.mark.parametrize("kind,policy,body,expected", [
    ("account_status", "atrasado", {"status": "atrasado"}, CORRECT),
    ("account_status", "atrasado", {"status": "aberto"}, DIVERGENCE),
    ("lifecycle_state", "overdue", {"receivable_lifecycle": {"state": "overdue"}}, CORRECT),
    ("lifecycle_state", "overdue", {"receivable_lifecycle": {"state": "open"}}, DIVERGENCE),
])
def test_account_reads(kind, policy, body, expected):
    assert run(exp_of(kind, policy), [(*ACCOUNT[:2], 200, body)])[0] == expected


def test_policy_ideal_gap_with_finding_is_a_product_finding():
    exp = exp_of("lifecycle_state", "paid", ideal="overdue", factors=["settlement_lag"], finding=["G-SIM-11"])
    assert run(exp, [("account", "/accounts/1", 200, {"receivable_lifecycle": {"state": "paid"}})])[0] == FINDING
    assert run(exp, [("account", "/accounts/1", 200, {"receivable_lifecycle": {"state": "open"}})])[0] == DIVERGENCE


@pytest.mark.parametrize("policy,body,expected", [
    ("ATRASO_RECORRENTE", {"status": "classified", "classification": {"label": "ATRASO_RECORRENTE"}}, CORRECT),
    ("PAGAMENTO_REGULAR", {"status": "classified", "classification": {"label": "ATRASO_RECORRENTE"}}, DIVERGENCE),
    ("INSUFFICIENT_DATA", {"status": "not_classified_yet", "classification": None}, CORRECT),
    ("ATRASO_RECORRENTE", {"status": "not_classified_yet", "classification": None}, DIVERGENCE),
])
def test_classification(policy, body, expected):
    exp = exp_of("classification", policy, subject={"cust_ref": "C-1", "email": "a@b"})
    assert run(exp, [("classification", "/accounts/1/classification", 200, body)])[0] == expected


def test_behavior_pattern_and_eligibility():
    exp = exp_of("behavior_pattern_present", True, subject={"anchor_rec_ref": "R-1", "email": "a@b"})
    assert run(exp, [("memories", "/memories", 200, {"items": [{"id": 1}]})])[0] == CORRECT
    assert run(exp, [("memories", "/memories", 200, {"items": []})])[0] == DIVERGENCE
    exp = exp_of("mark_overdue_eligible", True, at="11:00")
    assert run(exp, [("mark_overdue_eligibility", "/x", 200, {"system_recommendable": True})])[0] == CORRECT
    assert run(exp, [("mark_overdue_eligibility", "/x", 200, {"system_recommendable": False})])[0] == DIVERGENCE
    exp = exp_of("escalation_eligible", False, at="11:00")
    assert run(exp, [("human_escalation_eligibility", "/x", 200, {"status": "ineligible"})])[0] == CORRECT


REFS = {"R-1": {"account_id": 5}, "MP-1": {"kind": "mark_paid", "rec_ref": "R-1", "approval_id": 9},
        "ESC-1": {"kind": "escalation", "rec_ref": "R-1", "work_item_id": 3, "due": "2026-01-05"}}
SUBJECT_MP = {"kind": "mark_paid", "rec_ref": "R-1", "ref": "MP-1"}


def test_governed_request_decision_and_expiry():
    listing = {"items": [{"request_id": 9, "skill_key": "account.mark_paid", "target_account_id": 5, "status": "pending"}]}
    exp = exp_of("governed_request_exists", {"requester": "x"}, subject=SUBJECT_MP)
    assert run(exp, [("approvals", "/approvals", 200, listing)], refs=REFS)[0] == CORRECT
    wrong = {"items": [dict(listing["items"][0], skill_key="account.mark_overdue")]}
    assert run(exp, [("approvals", "/approvals", 200, wrong)], refs=REFS)[0] == DIVERGENCE
    exp = exp_of("governed_decision", {"decision": "approved", "decider": "x"}, subject=SUBJECT_MP)
    assert run(exp, [("approval", "/approvals/9", 200, {"request": {"status": "approved"}})], refs=REFS)[0] == CORRECT
    assert run(exp, [("approval", "/approvals/9", 200, {"request": {"status": "pending"}})], refs=REFS)[0] == DIVERGENCE
    exp = exp_of("effective_expiry", {"expires_at": {"day": 4, "at": "10:00"}}, subject=SUBJECT_MP)
    ok = {"request": {"status": "pending", "expires_at": "2026-01-09T13:00:45Z"}}        # 10:00:45 local (D4)
    late = {"request": {"status": "pending", "expires_at": "2026-01-09T13:05:00Z"}}
    assert run(exp, [("approval", "/approvals/9", 200, ok)], refs=REFS)[0] == CORRECT
    assert run(exp, [("approval", "/approvals/9", 200, late)], refs=REFS)[0] == DIVERGENCE


def test_escalation_work_item_by_id_and_by_work_key():
    exp = exp_of("escalation_work_item_exists", True, subject={"rec_ref": "R-1", "vencimento_day": 0})
    assert run(exp, [("work_item", "/work-items/3", 200, {"id": 3})])[0] == CORRECT
    assert run(exp, [("work_item", "/work-items/3", 404, {"detail": "x"})])[0] == DIVERGENCE
    listing = {"items": [{"work_key": "human_escalation:v1:5:2026-01-05"}]}
    assert run(exp, [("work_items", "/work-items", 200, listing)], refs=REFS)[0] == CORRECT
    assert run(exp, [("work_items", "/work-items", 200, {"items": []})], refs=REFS)[0] == DIVERGENCE


def test_must_not_exist_and_no_duplicate_effect():
    exp = exp_of("must_not_exist", ["escalation_work_item"], subject={"rec_ref": "R-1", "vencimento_day": 0})
    assert run(exp, [("work_items", "/work-items", 200, {"items": []})], refs=REFS)[0] == CORRECT
    present = {"items": [{"work_key": "human_escalation:v1:5:2026-01-05"}]}
    assert run(exp, [("work_items", "/work-items", 200, present)], refs=REFS)[0] == DIVERGENCE
    exp = exp_of("no_duplicate_effect", {"escalation_work_items_per_episode_max": 1,
                                         "live_approval_requests_per_episode_max": 1})
    ok_work = {"items": [{"work_key": "human_escalation:v1:5:2026-01-05"}]}
    live = {"items": [{"target_account_id": 5, "status": "pending", "skill_key": "account.mark_paid"}]}
    two = {"items": live["items"] * 2}
    reads = lambda approvals: [("work_items", "/work-items", 200, ok_work), ("approvals", "/approvals", 200, approvals)]  # noqa: E731
    assert run(exp, reads(live), refs=REFS)[0] == CORRECT
    assert run(exp, reads(two), refs=REFS)[0] == DIVERGENCE


def test_observation_counts_and_abstention():
    observations = {"items": [{"id": 1, "observation_type": "human_assessment"},
                              {"id": 2, "observation_type": "human_assessment"},
                              {"id": 3, "observation_type": "observed_fact", "linked_account_event_id": 7}]}
    read = [("observations", "/work-items/3/escalation-observations", 200, observations)]
    assert run(exp_of("human_assessment_count", 2), read, refs=REFS)[0] == CORRECT
    assert run(exp_of("human_assessment_count", 3), read, refs=REFS)[0] == DIVERGENCE
    assert run(exp_of("observed_fact_count", 1, semantics="correlation_only"), read, refs=REFS)[0] == CORRECT
    assert run(exp_of("observed_fact_count", 0, semantics="abstain"), [("observations", "/work-items/3/x", 200,
               {"items": []})], refs=REFS)[0] == ABSTENTION
    assert run(exp_of("observed_fact_count", 0, semantics="abstain"), read, refs=REFS)[0] == DIVERGENCE
    assert run(exp_of("observed_fact_links_event", {"linked_event": "x"}, semantics="correlation_only"), read,
               refs=REFS)[0] == CORRECT


def test_outcome_reads():
    exp = exp_of("business_effect_verified", "verified", subject=SUBJECT_MP)
    assert run(exp, [("outcome", "/o", 200, {"effect_verification": {"result": "verified"}})])[0] == CORRECT
    assert run(exp, [("outcome", "/o", 200, {"effect_verification": {"result": "pending"}})])[0] == DIVERGENCE
    exp = exp_of("account_event_count", {"status_changed_to_pago": 1})
    body = {"payment": {"evidence": [{"source": "account_event", "id": 4}, {"source": "approval_request", "id": 1}]}}
    assert run(exp, [("outcome", "/o", 200, body)])[0] == CORRECT
    assert run(exp, [("outcome", "/o", 200, {"payment": {"evidence": []}})])[0] == DIVERGENCE


# --------------------------------------------------------------------------
# knowledge_transition projetado
# --------------------------------------------------------------------------
def kt_exp(rows, allowance=None, day=5):
    return exp_of("knowledge_transition", {"observation_model": "end_of_day", "rows": rows,
                                           "severities": [r["severity"] for r in rows],
                                           "intraday_allowance": allowance or {}, "closure": "x"}, day=day)


def brain(*items):
    return ("brain", "/brain/", 200, {"items": [{"id": n, "knowledge_type": "receivable_lifecycle",
                                                  "severity": severity, "created_at": created}
                                                 for n, (severity, created) in enumerate(items, start=1)]})


def test_knowledge_exact_sequence_is_correct_and_open_paid_never_asserted():
    rows = [{"day": 1, "state": "due_soon", "severity": "info"}, {"day": 3, "state": "overdue", "severity": "high"}]
    read = brain(("high", "2026-01-08T12:00:00Z"), ("info", "2026-01-06T12:00:00Z"))   # API devolve DESC
    assert run(kt_exp(rows), [read])[0] == CORRECT
    other = brain(("info", "2026-01-06T12:00:00Z"))
    assert run(kt_exp(rows), [other])[0] == DIVERGENCE
    assert run(kt_exp([]), [("brain", "/brain/", 200, {"items": []})])[0] == CORRECT      # nenhum alerta, nenhuma linha
    other_type = ("brain", "/brain/", 200, {"items": [{"id": 1, "knowledge_type": "insight", "severity": "info",
                                                        "created_at": "2026-01-06T12:00:00Z"}]})
    assert run(kt_exp([]), [other_type])[0] == CORRECT


def test_knowledge_intraday_extras_are_a_known_limit_only_inside_the_allowance():
    rows = [{"day": 2, "state": "overdue", "severity": "high"}]
    transient = brain(("medium", "2026-01-07T12:00:00Z"), ("high", "2026-01-07T21:00:00Z"))       # mesmo dia local 2
    allowed = {"2": ["medium"]}
    klass, document = run(kt_exp(rows, allowed), [transient])
    assert klass == KNOWN_LIMIT and "intraday_sampling" in document["results"][0]["reason"]
    assert run(kt_exp(rows, {"2": ["info"]}), [transient])[0] == DIVERGENCE       # severidade fora da tolerancia
    assert run(kt_exp(rows, {}), [transient])[0] == DIVERGENCE                    # dia sem tolerancia
    assert run(kt_exp(rows, allowed), [brain(("medium", "2026-01-07T12:00:00Z"))])[0] == DIVERGENCE   # faltou a esperada


def test_knowledge_created_at_maps_to_the_business_day_not_the_utc_day():
    rows = [{"day": 0, "state": "due_soon", "severity": "info"}]
    late = brain(("info", "2026-01-06T01:30:00Z"))                 # 22:30 local do dia 0 (UTC-3)
    assert run(kt_exp(rows, day=0), [late])[0] == CORRECT
    assert run(kt_exp([{"day": 1, "state": "due_soon", "severity": "info"}], day=1), [late])[0] == DIVERGENCE


# --------------------------------------------------------------------------
# evidencia do Driver
# --------------------------------------------------------------------------
def http_row(op, status, response=None, **fields):
    return {"kind": "http", "seq": fields.pop("seq", 1), "op": op, "status": status, "response": response,
            "day": fields.pop("day", 3), "at": fields.pop("at", "11:00"), **fields}


def test_driver_evidence_kinds():
    decision = {"decision": {"decision_type": "single_action", "selected_actions": ["account.mark_overdue"]},
                "applied_rules": ["R0"]}
    row = http_row("consult_nba", 200, decision, rec_ref="R-1")
    exp = exp_of("nba_decision", {"decision_type": "single_action", "selected_actions": ["account.mark_overdue"],
                                  "trigger_rules": []}, at="11:00", evidence_class="DRIVER_EXECUTION_EVIDENCE")
    assert run(exp, driver=[row])[0] == CORRECT
    assert run(exp_of("nba_applied_rules", ["R0"], at="11:00", evidence_class="DRIVER_EXECUTION_EVIDENCE"),
               driver=[row])[0] == CORRECT
    assert run(exp_of("nba_applied_rules", ["R1"], at="11:00", evidence_class="DRIVER_EXECUTION_EVIDENCE"),
               driver=[row])[0] == DIVERGENCE
    assert run(exp, driver=[])[0] == HARNESS_ERROR                                  # sem resposta: erro do instrumento

    ok = http_row("execute_mark_paid", 200, {"invocation_status": "succeeded"}, ref="MP-1", at="12:00")
    execution = exp_of("governed_execution", {"executor": "x", "result": "succeeded"}, at="12:00", subject=SUBJECT_MP,
                       evidence_class="DRIVER_EXECUTION_EVIDENCE")
    assert run(execution, driver=[ok])[0] == CORRECT
    assert run(execution, driver=[http_row("execute_mark_paid", 409, {}, ref="MP-1", at="12:00")])[0] == DIVERGENCE
    failed = exp_of("governed_execution", {"executor": "x", "result": "failed", "reason": "expected_status_mismatch"},
                    at="12:00", subject=SUBJECT_MP, evidence_class="DRIVER_EXECUTION_EVIDENCE")
    assert run(failed, driver=[http_row("execute_mark_paid", 409, {}, ref="MP-1", at="12:00")])[0] == CORRECT

    violation = exp_of("authority_violation_rejected", {"actor": "g", "attempt": "decider_is_requester",
                                                        "effect": "none"}, at="11:30", subject=SUBJECT_MP,
                       evidence_class="DRIVER_EXECUTION_EVIDENCE")
    denied = http_row("decide_mark_paid", 403, {}, ref="MP-1", at="11:30", actor="g")
    assert run(violation, driver=[denied])[0] == CORRECT
    assert run(violation, driver=[dict(denied, status=200)])[0] == DIVERGENCE

    status = exp_of("http_status", 409, at="11:00", evidence_class="DRIVER_EXECUTION_EVIDENCE")
    assert run(status, driver=[http_row("materialize_escalation", 409, {}, rec_ref="R-1")])[0] == CORRECT


def test_due_date_changes_are_driver_evidence_not_a_product_count():
    exp = exp_of("driver_due_date_change_accepted_count", 2, day=179, evidence_class="DRIVER_EXECUTION_EVIDENCE")
    results = [{"kind": "op_result", "op": "change_due_date", "rec_ref": "R-1", "http_status": 200,
                "accepted_change": True} for _ in range(2)]
    results.append({"kind": "op_result", "op": "change_due_date", "rec_ref": "R-1", "http_status": 200,
                    "accepted_change": False})
    klass, document = run(exp, driver=results)
    assert klass == CORRECT and "nao contagem do produto" in document["results"][0]["reason"]
    assert run(exp, driver=results[:1])[0] == DIVERGENCE


# --------------------------------------------------------------------------
# proveniencia: agregado = PRODUCT FACT; por sujeito = derivacao por exaustao
# --------------------------------------------------------------------------
def report(legacy=0, with_provenance=1, failures=()):
    return {"provenance_report": {"observations": {"observed_fact_with_provenance": with_provenance,
                                                   "observed_fact_legacy_without_provenance": legacy},
                                  "integrity_failures": list(failures)}, "provenance_exit": 0}


def provenance_world(facts):
    refs = REFS
    observations = ("observations", "/work-items/3/escalation-observations", 200,
                    {"items": [{"id": 1, "observation_type": "observed_fact"}]})
    aggregate = exp_of("provenance_aggregate", {"legacy_without_provenance": 0, "integrity_failures": 0,
                                                "with_provenance_equals_collected_observed_facts": True},
                       subject={"scope": "run"}, day=179)
    present = exp_of("provenance_present", {"producer_pass_id": True}, subject={"rec_ref": "R-1", "vencimento_day": 0},
                     evidence_class="EVALUATOR_DERIVATION")
    plan = {"clock": CLOCK, "items": [{"item_id": "I-1", "route": "observations", "satisfies": ["E-2"]}]}
    present["exp_id"] = "E-2"
    oracle = {"oracle_version": "v2.1", "execution_scenario_id": "x", "expectations": [aggregate, present]}
    collected = [{"kind": "collected", "item_id": "I-1", "route": observations[0], "path": observations[1],
                  "status": 200, "body": observations[3]}]
    document = evaluate(oracle, plan, collected, [], {"refs": refs}, facts)
    return {r["kind"]: r["class"] for r in document["results"]}


def test_provenance_aggregate_and_exhaustion_derivation():
    good = provenance_world(report(0, 1))
    assert good == {"provenance_aggregate": CORRECT, "provenance_present": CORRECT}
    legacy = provenance_world(report(2, 1))
    assert legacy["provenance_aggregate"] == DIVERGENCE and legacy["provenance_present"] == KNOWN_LIMIT
    mismatch = provenance_world(report(0, 5))                       # CLI conta mais do que o Collector leu
    assert mismatch["provenance_present"] == KNOWN_LIMIT and mismatch["provenance_aggregate"] == DIVERGENCE
    absent = provenance_world({})
    assert absent["provenance_aggregate"] == HARNESS_ERROR and absent["provenance_present"] == KNOWN_LIMIT
    broken = report(0, 1, failures=["x"])
    broken["provenance_exit"] = 2
    assert provenance_world(broken)["provenance_aggregate"] == FINDING
    assert CORRECT not in {v for k, v in legacy.items() if k == "provenance_present"}      # jamais promovido


# --------------------------------------------------------------------------
# semanticas especiais, oracle inteiro, determinismo e isolamento
# --------------------------------------------------------------------------
def test_special_semantics_and_superseded():
    assert run(exp_of("account_status", "x", semantics="calibration_pending"), [(*ACCOUNT[:2], 200, {})])[0] == PENDING
    assert run(exp_of("classification", "x", semantics="known_limit", subject={"cust_ref": "c", "email": "e"}),
               [("classification", "/c", 200, {"status": "classified"})])[0] == KNOWN_LIMIT
    assert run(exp_of("not_representable", "partial_payment", semantics="not_representable"))[0] == NOT_REPRESENTABLE
    assert run(exp_of("account_status", "x", superseded_same_day=True))[0] == KNOWN_LIMIT


def test_every_oracle_kind_has_an_evaluator_and_empty_evidence_is_never_correct():
    oracle = load_oracle()
    plan = {"clock": CLOCK, "items": []}
    document = evaluate(oracle, plan, [], [], {"refs": {}}, {})
    assert not any("kind sem avaliador" in r["reason"] for r in document["results"])
    assert len(document["results"]) == len(oracle["expectations"])
    assert document["run_valid"] is False
    assert CORRECT not in {r["class"] for r in document["results"]}
    classes = {r["class"] for r in document["results"]}
    assert HARNESS_ERROR in classes and NOT_REPRESENTABLE in classes and KNOWN_LIMIT in classes


def test_evaluation_is_deterministic_and_digest_sensitive():
    exp = exp_of("account_status", "atrasado")
    read = [(*ACCOUNT[:2], 200, {"status": "atrasado"})]
    a = run(exp, read)[1]
    b = run(exp, read)[1]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    c = run(exp, [(*ACCOUNT[:2], 200, {"status": "aberto"})])[1]
    assert a["evaluation_sha256"] != c["evaluation_sha256"]
    assert a["oracle_version"] == "v2.1" and "t_real" not in json.dumps(a)


def test_evaluator_has_zero_network_and_never_imports_clients():
    banned = {"socket", "urllib", "http", "requests", "ssl", "asyncio", "subprocess", "sim.driver", "sim.collector"}
    for path in (SIM_ROOT / "evaluator").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            for name in names:
                assert name not in banned and not any(name.startswith(b + ".") for b in banned), (path.name, name)
