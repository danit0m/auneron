"""
sim.scenario.v2 -- gates novos T-11..T-18 (SIM-1.3 Design Freeze V1, 13,
mais T-18 exigido pelo PO).
"""

from __future__ import annotations

import json

from sim.generator.canonical import sha256_bytes
from sim.generator.config import load_inputs
from sim.oracle import rules_v2

from sim.tests.conftest import SCENARIOS

MANAGERS = {"sim-gerente-fin", "sim-coordenador-fin"}
ANALYSTS = {"sim-faturamento", "sim-receber", "sim-cobranca-1", "sim-cobranca-2", "sim-cobranca-3"}
AUTHORITY_OPS = {
    "request_mark_paid", "decide_mark_paid", "execute_mark_paid",
    "materialize_mark_overdue", "decide_mark_overdue", "execute_mark_overdue",
    "materialize_escalation", "consult_nba",
}
ANALYST_OPS = {"create_receivable", "change_due_date", "record_assessment"}

V1_FROZEN = {
    "nh-small-s340001": {
        "manifest.json": "48e9e8bb100416310c2660e69dcd3bd3895f6a228d0f3e5dda7dfa27f32de869",
        "oracle.json": "5b43d70835283ae440f085dc106fa20e7259c4575e7c3bff8f8760fc24124443",
        "public_agenda.json": "af08fe31b52964a86056f60557a79c151804b5a1bf98d3eb8c633d416c4f2c41",
        "world.json": "5f8ea9e0acf660d96af1089c5a38e3ee3e7b5c5885b316861fd7bb4568815c0e",
    },
    "nh-standard-s340001": {
        "manifest.json": "361d48a1be55c2c6e6543f5c9c2f5a6b462e90e8f20800530aee204dd943ed22",
        "oracle.json": "490ef7678f41c3d416842773a193811d2a5368c84b305ba73a51d4df3f3da580",
        "public_agenda.json": "834db27c318e4b228b45f4f281807a014bba725265e6df296416f952662c123b",
        "world.json": "f09216f6bf1b5bff2c01fd9bc224b65dbfac849c4ff95012db6f91d3f3f87336",
    },
}


def _chains(agenda: dict) -> dict:
    """Reconstroi, so pela agenda publica, quem pediu/decidiu/executou cada ref."""
    chains: dict = {}
    for op in agenda["ops"]:
        name = op["op"]
        if name in ("request_mark_paid", "materialize_mark_overdue"):
            chains[op["ref"]] = {"requester": op["actor"], "deciders": [], "executors": [],
                                 "kind": "mark_paid" if name == "request_mark_paid" else "mark_overdue"}
        elif name in ("decide_mark_paid", "decide_mark_overdue"):
            chains[op["ref"]]["deciders"].append(op["actor"])
        elif name in ("execute_mark_paid", "execute_mark_overdue"):
            chains[op["ref"]]["executors"].append(op["actor"])
    return chains


def _declared_violations(oracle: dict) -> set:
    return {
        (e["subject"]["ref"], e["policy"]["attempt"], e["policy"]["actor"])
        for e in oracle["expectations"] if e["kind"] == "authority_violation_rejected"
    }


# T-11 -- RBAC real das personas -------------------------------------------
def test_t11_persona_rbac(scenario_v2):
    for op in scenario_v2.agenda["ops"]:
        if op["actor"] == "harness":
            assert op["op"] == "restart_backend"
            continue
        if op["op"] in AUTHORITY_OPS:
            assert op["actor"] in MANAGERS, op
        else:
            assert op["op"] in ANALYST_OPS, op
        if op["actor"] in ANALYSTS:
            assert op["op"] in ANALYST_OPS, op
    assert all(op["actor"] != "sim-admin" and op["actor"] != "sim-controller"
               for op in scenario_v2.agenda["ops"])


# T-12 -- nenhum PUT status, nenhum F1 --------------------------------------
def test_t12_no_put_status_no_f1(scenario_v2):
    names = {op["op"] for op in scenario_v2.agenda["ops"]}
    assert "settle_receivable" not in names
    chains = _chains(scenario_v2.agenda)
    for op in scenario_v2.agenda["ops"]:
        if op["op"].startswith(("decide_", "execute_")):
            assert op["ref"] in chains, op  # so decide/executa o que a agenda pediu
    kinds = {e["kind"] for e in scenario_v2.oracle["expectations"]}
    assert not kinds & {"approval_request_exists", "approval_decided", "approval_expired"}
    assert scenario_v2.oracle["out_of_scope"]["F1"].startswith("OUT")


# T-13 -- expiracao efetiva por expires_at -----------------------------------
def test_t13_expiry_vectors():
    created = (10, 13 * 60 + 30)
    expires = rules_v2.expiry_instant(created)
    assert expires == (11, 13 * 60 + 30)
    assert not rules_v2.effectively_expired(expires, (11, 13 * 60 + 29))
    assert rules_v2.effectively_expired(expires, (11, 13 * 60 + 30))
    assert rules_v2.effectively_expired(expires, (12, 0))
    assert rules_v2.expiry_instant((10, 23 * 60)) == (11, 23 * 60)


def test_t13_effective_expiry_expectations(scenario_v2):
    decided = {e["subject"]["ref"] for e in scenario_v2.oracle["expectations"]
               if e["kind"] == "governed_decision"}
    expiries = [e for e in scenario_v2.oracle["expectations"] if e["kind"] == "effective_expiry"]
    assert expiries
    for item in expiries:
        assert item["subject"]["ref"] not in decided
        assert item["policy"]["status_may_remain"] == "pending"
        assert item["day"] == item["policy"]["expires_at"]["day"]


# T-14 -- mark_overdue humano expirado/rejeitado: sem retry -------------------
def test_t14_no_retry_after_terminal_mark_overdue(scenario_v2):
    expectations = scenario_v2.oracle["expectations"]
    terminal = set()
    for item in expectations:
        if item["subject"].get("kind") != "mark_overdue":
            continue
        if item["kind"] == "effective_expiry" or (
            item["kind"] == "governed_decision" and item["policy"]["decision"] == "rejected"
        ):
            terminal.add(item["subject"]["ref"])
    assert terminal
    episodes: dict = {}
    for op in scenario_v2.agenda["ops"]:
        if op["op"] == "materialize_mark_overdue":
            episodes.setdefault((op["rec_ref"], op["vencimento_day"]), []).append(op["ref"])
    for refs in episodes.values():
        assert len(refs) == 1  # nunca uma 2a aprovacao para o mesmo episodio
    for ref in terminal:
        assert not any(op["op"] == "execute_mark_overdue" and op["ref"] == ref
                       for op in scenario_v2.agenda["ops"])


# T-15 -- corrida C-RACE-1 modelada ----------------------------------------------
def test_t15_race_modeled(scenario_v2):
    oracle = scenario_v2.oracle
    instances = oracle["case_instances"]["C-RACE-1"]
    assert instances
    for rec_ref in instances:
        items = [e for e in oracle["expectations"] if e["subject"].get("rec_ref") == rec_ref]
        mp = [e for e in items if e["subject"].get("kind") == "mark_paid"]
        failed = [e for e in mp if e["kind"] == "governed_execution" and e["policy"]["result"] == "failed"]
        ok = [e for e in mp if e["kind"] == "governed_execution" and e["policy"]["result"] == "succeeded"]
        mo_ok = [e for e in items if e["subject"].get("kind") == "mark_overdue"
                 and e["kind"] == "governed_execution" and e["policy"]["result"] == "succeeded"]
        assert failed and failed[0]["policy"]["reason"] == "expected_status_mismatch"
        assert mo_ok and ok
        requests = [e for e in mp if e["kind"] == "governed_request_exists"]
        assert [r["policy"]["expected_status"] for r in requests][:2] == ["aberto", "atrasado"]
        assert (failed[0]["day"], failed[0].get("at")) < (ok[0]["day"], ok[0].get("at")) or failed[0]["day"] < ok[0]["day"]


# T-16 -- v1 byte-identico e intocado ---------------------------------------------
def test_t16_v1_frozen_bytes_and_inputs(generated):
    for scenario_id, expected in V1_FROZEN.items():
        for name, digest in expected.items():
            assert sha256_bytes((SCENARIOS / scenario_id / name).read_bytes()) == digest
    for variant in ("nh-small", "nh-standard"):
        files = generated[variant].files
        expected = V1_FROZEN[f"{variant}-s340001"]
        assert {n: sha256_bytes(b) for n, b in files.items()} == expected
    manifest = json.loads((SCENARIOS / "nh-standard-s340001" / "manifest.json").read_bytes())
    assert manifest["inputs"] == dict(sorted(load_inputs().hashes.items()))


def test_t16_v2_never_overwrites_v1():
    v2_dirs = sorted(p.name for p in SCENARIOS.iterdir() if "-v2-" in p.name)
    assert v2_dirs == ["nh-small-v2-s340001", "nh-standard-v2-s340001"]
    assert set(V1_FROZEN) <= {p.name for p in SCENARIOS.iterdir()}


# T-17 -- reservados e BEV --------------------------------------------------------------
def test_t17_reserved_and_bev(scenario_v2):
    expectations = scenario_v2.oracle["expectations"]
    assert not any(e["kind"] == "work_outcome_evaluated" for e in expectations)
    succeeded = {
        e["subject"]["ref"] for e in expectations
        if e["kind"] == "governed_execution" and e["policy"]["result"] == "succeeded"
    }
    bev = [e for e in expectations if e["kind"] == "business_effect_verified"]
    assert bev
    for item in bev:
        assert item["subject"]["kind"] in ("mark_paid", "mark_overdue")
        assert item["subject"]["ref"] in succeeded
        assert item["policy"] == "verified"
    assert len({e["subject"]["ref"] for e in bev}) == len(succeeded)


# T-18 -- integridade da coreografia de autoridade --------------------------------
def test_t18_authority_choreography_integrity(scenario_v2):
    oracle = scenario_v2.oracle
    chains = _chains(scenario_v2.agenda)
    declared = _declared_violations(oracle)
    seen_violations = set()
    standard = reserve = 0
    for ref, chain in chains.items():
        requester = chain["requester"]
        assert requester in MANAGERS
        legit_deciders = []
        for decider in chain["deciders"]:
            assert decider in MANAGERS
            if decider == requester:
                seen_violations.add((ref, "decider_is_requester", decider))
            else:
                legit_deciders.append(decider)
        assert len(set(legit_deciders)) <= 1
        decider = legit_deciders[0] if legit_deciders else None
        for executor in chain["executors"]:
            assert executor in MANAGERS
            if decider is not None and executor == decider:
                seen_violations.add((ref, "executor_is_decider", executor))
        if decider is not None and chain["executors"]:
            legit_executors = {e for e in chain["executors"] if e != decider}
            assert legit_executors <= {requester}
            if requester == "sim-gerente-fin" and decider == "sim-coordenador-fin":
                standard += 1
            elif requester == "sim-coordenador-fin" and decider == "sim-gerente-fin":
                reserve += 1
            else:
                raise AssertionError(f"cadeia fora do congelado: {ref} {chain}")
    # a UNICA excecao invalida e o C-AUTH-2, declarado no Oracle como recusa sem efeito
    assert seen_violations == declared
    assert declared and all(attempt in ("decider_is_requester", "executor_is_decider")
                            for _, attempt, _ in declared)
    for item in oracle["expectations"]:
        if item["kind"] == "authority_violation_rejected":
            assert item["policy"]["effect"] == "none"
            assert "C-AUTH-2" in item.get("case_ids", [])
    assert standard > 0 and reserve > 0
