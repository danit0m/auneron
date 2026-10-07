"""
SIM-1.5 -- gates estaticos do Driver (dispatch, estado, reconciliacao,
concorrencia, recuperacao, isolamento). Sem Docker: o produto e um fake de
FORMATOS (`fake_product.py`).
"""

from __future__ import annotations

import ast
import collections
import json
from pathlib import Path

import pytest

from sim.driver.agenda import PUBLIC_OP_KEYS_V2 as DRIVER_KEYS
from sim.driver.agenda import load_agenda
from sim.driver.agenda import load_manifest
from sim.driver.dispatch import DECISION_VALUE
from sim.driver.dispatch import DISPATCH_TABLE
from sim.driver.dispatch import HARNESS_OPS
from sim.driver.dispatch import NO_BLIND_RETRY
from sim.driver.dispatch import PUBLIC_OPS
from sim.driver.dispatch import RETRY_SAFE
from sim.driver.dispatch import Context
from sim.driver.dispatch import build_request
from sim.driver.dispatch import effects
from sim.driver.errors import HarnessError
from sim.driver.errors import MissingRef
from sim.driver.evidence import EvidenceLog
from sim.driver.http import ALLOWED_BASE_URLS
from sim.driver.http import AllowlistViolation
from sim.driver.http import DriverHttp
from sim.driver.http import check_route
from sim.driver.params import load_params
from sim.driver.state import RECONCILED
from sim.driver.state import SENT
from sim.generator.agenda import PUBLIC_OP_KEYS_V2
from sim.generator.agenda import PUBLIC_OPS_V2
from sim.tests.collector_support import load_oracle
from sim.tests.conftest import SCENARIOS
from sim.tests.conftest import SIM_ROOT
from sim.tests.driver_support import SCENARIO
from sim.tests.driver_support import make_driver
from sim.tests.driver_support import run_slots
from sim.tests.driver_support import write_inputs
from sim.tests.fake_product import FakeProduct

REPO = SIM_ROOT.parent
AGENDA = SCENARIOS / SCENARIO / "public_agenda.json"


def first_op(name: str) -> dict:
    return next(op for op in json.loads(AGENDA.read_bytes())["ops"] if op["op"] == name)


def records(evidence) -> list:
    return EvidenceLog.read(evidence.path)


# --------------------------------------------------------------------------
# parametros congelados e tabela de despacho
# --------------------------------------------------------------------------
def test_frozen_driver_params():
    params = load_params()
    assert params["reconcile_create_receivable"] == {
        "reads": 3, "wait_s": 5, "page_limit": 200, "max_resend": 1, "max_pages": 1000}
    assert params["retry"] == {"max_attempts": 3, "backoff_s": 2}
    assert params["session"] == {"relogin_margin_minutes": 30}
    assert params["o1_max_skew_s"] == 1.0
    assert params["backend_url"] == "http://sim-backend:8000"
    assert params["d0"] == "2026-01-05" and params["utc_offset_minutes"] == -180
    assert ALLOWED_BASE_URLS == frozenset({"http://sim-backend:8000"})


def test_driver_constants_match_generator_contract():
    assert set(PUBLIC_OPS) == set(PUBLIC_OPS_V2)
    assert set(DRIVER_KEYS) == set(PUBLIC_OP_KEYS_V2)
    assert len(PUBLIC_OPS) == 12 and {row[0] for row in DISPATCH_TABLE} == set(PUBLIC_OPS)
    assert HARNESS_OPS == {"restart_backend"}
    assert not RETRY_SAFE & NO_BLIND_RETRY
    assert NO_BLIND_RETRY == {"create_receivable", "decide_mark_overdue", "decide_mark_paid"}


def test_decision_values_match_backend_schema():
    tree = ast.parse((REPO / "backend/app/schemas/approval.py").read_text(encoding="utf-8"))
    values = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ApprovalDecisionValue":
            values = {elt.value for elt in node.value.slice.elts}
    assert values == set(DECISION_VALUE.values()) == {"approved", "rejected"}


def test_every_agenda_op_builds_an_allowed_request(tmp_path):
    driver, fake, store, _, _ = make_driver(tmp_path)
    ctx = driver.ctx
    ops = [op for slot in driver.slots.values() for op in slot]
    sample = {}
    for op in ops:
        sample.setdefault(op["op"], op)
    run_slots(driver, fake, 0, 3)          # cria refs reais para montar requests dependentes
    for name, op in sample.items():
        if name in HARNESS_OPS:
            with pytest.raises(HarnessError):
                build_request(op, ctx)
            continue
        try:
            request = build_request(op, ctx)
        except MissingRef:
            continue                        # ref de dia posterior: coberto pelo run completo
        check_route(request.method, request.path)
        row = next(item for item in DISPATCH_TABLE if item[0] == name)
        assert request.method == row[1]


def test_idempotency_keys_only_where_the_product_reads_them(tmp_path):
    driver, fake, *_ = make_driver(tmp_path)
    run_slots(driver, fake)
    keyed = [path for _, path, headers in fake.calls if "Idempotency-Key" in headers]
    assert keyed and all("/approvals/skill-executions/" in p or p.endswith("/human-assessment") for p in keyed)
    expected = sum(1 for slot in driver.slots.values() for op in slot
                   if op["op"] in ("request_mark_paid", "record_assessment"))
    assert len(keyed) == expected
    for _, path, headers in fake.calls:
        assert headers["X-API-Key"] == "k" * 64


# --------------------------------------------------------------------------
# allowlist estrutural
# --------------------------------------------------------------------------
@pytest.mark.parametrize("method,path", [
    ("GET", "/brain/"), ("DELETE", "/brain/1"), ("PATCH", "/brain/1/resolve"), ("PATCH", "/brain/1/reopen"),
    ("DELETE", "/accounts/1"), ("POST", "/accounts/detect-overdue"), ("GET", "/orchestrator/health"),
    ("GET", "/accounts/?status=aberto"), ("POST", "/approvals"), ("GET", "/memories?scope_type=account"),
])
def test_driver_allowlist_refuses_everything_else(method, path):
    with pytest.raises(AllowlistViolation):
        check_route(method, path)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8100", "http://localhost:8000", "http://sim-postgres:5432",
                                 "https://sim-backend:8000", "http://host.docker.internal:8000"])
def test_driver_refuses_other_hosts(url):
    with pytest.raises(AllowlistViolation):
        DriverHttp(FakeProduct(), url, "k")


# --------------------------------------------------------------------------
# run completo, estado, evidencia, sessoes
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("full")
    driver, fake, store, evidence, sleep = make_driver(tmp_path)
    results = run_slots(driver, fake)
    return driver, fake, store, evidence, results


def test_full_agenda_runs_without_harness_error(full_run):
    driver, fake, store, evidence, results = full_run
    assert store.counts() == {"done": 4987}
    assert sum(r["executed"] for r in results) == 4986
    assert [r["harness_ops"] for r in results if r["harness_ops"]] == [[2478]]
    outcome = collections.Counter((r["op"], r["http_status"]) for r in records(evidence) if r["kind"] == "op_result")
    assert outcome[("create_receivable", 201)] == 880
    assert outcome[("decide_mark_paid", 403)] + outcome[("execute_mark_paid", 403)] == 3   # C-AUTH-2
    assert sum(1 for r in records(evidence) if r["kind"] == "op_result" and r["http_status"] is None) == 0


def test_full_run_state_and_evidence_chain(full_run):
    driver, fake, store, evidence, _ = full_run
    refs = store.all_refs()
    assert sum(1 for key in refs if key.startswith("R-")) == 880
    assert sum(1 for key in refs if key.startswith("MO-")) == 308
    assert all(ref["approval_id"] for key, ref in refs.items() if key.startswith(("MO-", "MP-")))
    ok, count, tail = EvidenceLog.verify(evidence.path)
    assert ok and count == len(records(evidence))


def test_evidence_chain_detects_tamper_and_reorder(tmp_path):
    driver, fake, store, evidence, _ = make_driver(tmp_path, ops_filter=lambda op: op["seq"] <= 5)
    run_slots(driver, fake)
    lines = evidence.path.read_bytes().splitlines()
    assert EvidenceLog.verify(evidence.path)[0]
    tampered = tmp_path / "t1.jsonl"
    tampered.write_bytes(b"\n".join([lines[0].replace(b"201", b"202")] + lines[1:]) + b"\n")
    assert not EvidenceLog.verify(tampered)[0]
    swapped = tmp_path / "t2.jsonl"
    swapped.write_bytes(b"\n".join([lines[1], lines[0], *lines[2:]]) + b"\n")
    assert not EvidenceLog.verify(swapped)[0]
    with pytest.raises(RuntimeError):
        EvidenceLog(swapped)


def test_due_date_change_evidence_counts_accepted_puts(full_run):
    _, _, _, evidence, _ = full_run
    accepted = collections.Counter(r["rec_ref"] for r in records(evidence)
                                   if r["kind"] == "op_result" and r["op"] == "change_due_date"
                                   and r.get("accepted_change") and r["http_status"] == 200)
    oracle = load_oracle()
    expected = {e["subject"]["rec_ref"]: e["policy"] for e in oracle["expectations"]
                if e["kind"] == "driver_due_date_change_accepted_count"}
    assert dict(accepted) == expected


def test_sessions_are_reused_and_relogin_on_401(tmp_path):
    driver, fake, store, evidence, _ = make_driver(tmp_path, ops_filter=lambda op: op["day"] == 0)
    run_slots(driver, fake, 0, 0)
    personas = {op["actor"] for slot in driver.slots.values() for op in slot if op["day"] == 0}
    assert fake.logins == len(personas)             # exatamente uma sessao por persona ativa
    before = fake.logins
    fake.expire_sessions()                          # 401 forcado
    ops = [op for op in driver.slots[(0, "09:00")]][:1]
    store.mark(ops[0]["seq"], "pending")
    driver.run_slot(0, "09:00")
    assert fake.logins == before + 1
    assert any(r.get("status") == 401 for r in records(evidence) if r["kind"] == "http")


# --------------------------------------------------------------------------
# reconciliacao de create_receivable (A-2)
# --------------------------------------------------------------------------
def create_only(tmp_path, **kwargs):
    return make_driver(tmp_path, ops_filter=lambda op: op["seq"] == 1, **kwargs)


def posts(fake) -> int:
    return sum(1 for method, path, _ in fake.calls if method == "POST" and path == "/accounts/")


def test_create_ambiguous_with_stable_absence_resends_once(tmp_path):
    driver, fake, store, evidence, sleep = create_only(tmp_path)
    fake.fail(r"^/accounts/$", "timeout", 1, "POST")
    driver.run_slot(0, "09:00")
    assert posts(fake) == 2 and len(fake.accounts) == 1
    assert sleep.calls == [5.0, 5.0]                       # 3 leituras, 5 s entre elas
    reads = [r for r in records(evidence) if r["kind"] == "reconcile_read"]
    assert [r["candidates"] for r in reads] == [[], [], []]
    assert store.op_status(1) == "done"


def test_create_lost_response_adopts_the_single_candidate(tmp_path):
    driver, fake, store, evidence, sleep = create_only(tmp_path)
    fake.fail(r"^/accounts/$", "lost", 1, "POST")
    driver.run_slot(0, "09:00")
    assert posts(fake) == 1 and len(fake.accounts) == 1
    assert store.op_status(1) == RECONCILED
    assert store.get_ref("R-00001") == {"account_id": 1}


def test_create_with_two_candidates_is_harness_error(tmp_path):
    driver, fake, store, _, _ = create_only(tmp_path)
    op = driver.slots[(0, "09:00")][0]
    for _ in range(2):
        fake.accounts[len(fake.accounts) + 1] = {
            "id": len(fake.accounts) + 1, "cliente": op["cliente"], "email": op["email"], "whatsapp": "x",
            "valor": float(op["valor"]), "vencimento": "2025-12-29", "status": "aberto"}
    fake.next_id["account"] = 3
    fake.fail(r"^/accounts/$", "timeout", 1, "POST")
    with pytest.raises(HarnessError, match="2 candidatos"):
        driver.run_slot(0, "09:00")
    assert posts(fake) == 1                                 # nunca reenviou


def test_create_unstable_reads_are_harness_error(tmp_path):
    driver, fake, *_ = create_only(tmp_path)
    op = driver.slots[(0, "09:00")][0]
    hit = {"id": 99, "cliente": op["cliente"], "email": op["email"], "valor": float(op["valor"]),
           "vencimento": "2025-12-29"}
    fake.list_script = [[], [hit], []]
    fake.fail(r"^/accounts/$", "timeout", 1, "POST")
    with pytest.raises(HarnessError, match="instavel"):
        driver.run_slot(0, "09:00")
    assert posts(fake) == 1


def test_create_second_ambiguity_after_resend_is_harness_error(tmp_path):
    driver, fake, *_ = create_only(tmp_path)
    fake.fail(r"^/accounts/$", "timeout", 2, "POST")
    with pytest.raises(HarnessError, match="segundo resultado ambiguo"):
        driver.run_slot(0, "09:00")
    assert posts(fake) == 2


def test_create_scan_error_is_harness_error(tmp_path):
    driver, fake, *_ = create_only(tmp_path)
    fake.fail(r"^/accounts/$", "timeout", 1, "POST")
    fake.fail(r"^/accounts/$", "500", 1, "GET")
    with pytest.raises(HarnessError, match="inconclusiva"):
        driver.run_slot(0, "09:00")


def test_create_scan_is_complete_and_paginated(tmp_path):
    override = {"reconcile_create_receivable": {"reads": 3, "wait_s": 5, "page_limit": 2, "max_resend": 1,
                                                "max_pages": 1000}}
    driver, fake, store, evidence, _ = create_only(tmp_path, params_override=override)
    op = driver.slots[(0, "09:00")][0]
    for number in range(5):                                 # casam o filtro `cliente`, mas nao os campos
        fake.accounts[number + 1] = {"id": number + 1, "cliente": op["cliente"], "email": "outro@x.com",
                                     "whatsapp": "x", "valor": 1.0, "vencimento": "2025-12-01", "status": "aberto"}
    fake.next_id["account"] = 6
    fake.fail(r"^/accounts/$", "lost", 1, "POST")
    driver.run_slot(0, "09:00")
    reads = [r for r in records(evidence) if r["kind"] == "reconcile_read"]
    assert all(r["pages"] >= 3 for r in reads) and all(r["candidates"] == [6] for r in reads)
    assert store.get_ref("R-00001") == {"account_id": 6}


def test_mapped_accounts_are_never_candidates(tmp_path):
    driver, fake, store, _, _ = create_only(tmp_path)
    op = driver.slots[(0, "09:00")][0]
    fake.accounts[1] = {"id": 1, "cliente": op["cliente"], "email": op["email"], "whatsapp": "x",
                        "valor": float(op["valor"]), "vencimento": "2025-12-29", "status": "aberto"}
    fake.next_id["account"] = 2
    store.set_ref("R-OUTRO", {"account_id": 1})             # a conta 1 ja pertence a outra ref
    fake.fail(r"^/accounts/$", "timeout", 1, "POST")
    driver.run_slot(0, "09:00")
    assert posts(fake) == 2 and store.get_ref("R-00001") == {"account_id": 2}


# --------------------------------------------------------------------------
# decisoes ambiguas, recuperacao de crash, ordem, refs ausentes
# --------------------------------------------------------------------------
def chain_for_first_title(op):
    return op["day"] == 0 and (op.get("rec_ref") == "R-00001" or op.get("ref") in ("MO-000001", "ESC-000002"))


def run_until_decision(tmp_path, **kwargs):
    return make_driver(tmp_path, ops_filter=chain_for_first_title, **kwargs)


def test_decide_lost_response_is_reconciled_without_resend(tmp_path):
    driver, fake, store, evidence, _ = run_until_decision(tmp_path)
    fake.fail(r"/approvals/\d+/decision", "lost", 1, "POST")
    run_slots(driver, fake, 0, 0)
    decisions = [c for c in fake.calls if c[1].endswith("/decision")]
    assert len(decisions) == 1
    assert next(r for r in records(evidence) if r["kind"] == "op_result" and r["op"] == "decide_mark_overdue")[
        "outcome"] == "reconciled"


def test_decide_nothing_happened_resends_once_then_fails_closed(tmp_path):
    driver, fake, store, evidence, _ = run_until_decision(tmp_path)
    fake.fail(r"/approvals/\d+/decision", "timeout", 1, "POST")
    run_slots(driver, fake, 0, 0)
    assert len([c for c in fake.calls if c[1].endswith("/decision")]) == 2

    tmp2 = tmp_path / "again"
    tmp2.mkdir()
    driver2, fake2, *_ = run_until_decision(tmp2)
    fake2.fail(r"/approvals/\d+/decision", "timeout", 2, "POST")
    with pytest.raises(HarnessError, match="segundo resultado ambiguo"):
        run_slots(driver2, fake2, 0, 0)


def test_decide_with_unexpected_status_is_harness_error(tmp_path):
    driver, fake, store, *_ = run_until_decision(tmp_path)
    fake.fail(r"/approvals/\d+/decision", "lost", 1, "POST")
    original = fake._route

    def tamper(method, path, query, data, headers):
        response = original(method, path, query, data, headers)
        if path.endswith("/decision") and method == "POST":
            fake.approvals[1]["status"] = "rejected"       # outra decisao aconteceu
        return response

    fake._route = tamper
    with pytest.raises(HarnessError, match="decisao ambigua"):
        run_slots(driver, fake, 0, 0)


def test_in_flight_create_is_reconciled_after_a_crash(tmp_path):
    driver, fake, store, evidence, _ = create_only(tmp_path)
    op = driver.slots[(0, "09:00")][0]
    fake.accounts[1] = {"id": 1, "cliente": op["cliente"], "email": op["email"], "whatsapp": op["whatsapp"],
                        "valor": float(op["valor"]), "vencimento": "2025-12-29", "status": "aberto"}
    fake.next_id["account"] = 2
    store.mark(1, SENT)                                    # crash: enviado, sem resultado registrado
    assert store.in_flight() == [1]
    driver.run_slot(0, "09:00")
    assert posts(fake) == 0 and store.op_status(1) == RECONCILED and store.in_flight() == []


def test_state_survives_a_new_process_and_never_resends_done_ops(tmp_path):
    home = tmp_path / "shared"
    first, fake, store1, ev1, _ = make_driver(tmp_path, home=home)
    run_slots(first, fake, 0, 4)
    calls, counts, refs = len(fake.calls), store1.counts(), store1.all_refs()
    store1.close()
    second, _, store2, ev2, _ = make_driver(tmp_path, fake, home=home)
    assert store2.counts()["done"] == counts["done"] and store2.all_refs() == refs
    run_slots(second, fake, 0, 4)                          # nada pendente: nenhuma chamada nova
    assert len(fake.calls) == calls
    run_slots(second, fake, 5, 9)
    assert len(fake.calls) > calls
    assert EvidenceLog.verify(ev2.path)[0]


def test_slots_must_run_in_order(tmp_path):
    driver, fake, *_ = make_driver(tmp_path)
    later = [key for key in driver.slot_keys() if key[0] >= 5][0]
    with pytest.raises(HarnessError, match="fora de ordem"):
        driver.run_slot(*later)


def test_missing_reference_is_a_harness_error(tmp_path):
    driver, fake, *_ = make_driver(tmp_path, ops_filter=lambda op: op["op"] == "change_due_date")
    day, at = driver.slot_keys()[0]
    with pytest.raises(MissingRef):
        driver.run_slot(day, at)


def test_restart_backend_is_a_harness_op_and_blocks_later_slots_until_done(tmp_path):
    driver, fake, store, *_ = make_driver(tmp_path, ops_filter=lambda op: op["op"] == "restart_backend" or op["seq"] == 1)
    driver.run_slot(0, "09:00")
    day, at = [key for key in driver.slot_keys() if key[0] == 90][0]
    result = driver.run_slot(day, at)
    assert result["harness_ops"] == [2478] and store.op_status(2478) == "pending"
    driver.mark_harness_done(2478, "restart")
    assert store.op_status(2478) == "done"


def test_version_id_mismatch_is_fail_closed(tmp_path):
    driver, fake, store, *_ = make_driver(tmp_path)
    op = first_op("request_mark_paid")
    body = {"request": {"request_id": 1, "skill_version_id": 99, "skill_key": "account.mark_paid"}}
    with pytest.raises(HarnessError, match="version_id"):
        effects(op, 201, body, driver.ctx)
    body["request"].update(skill_version_id=7, skill_key="account.mark_overdue")
    with pytest.raises(HarnessError, match="version_id"):
        effects(op, 201, body, driver.ctx)


# --------------------------------------------------------------------------
# concorrencia real
# --------------------------------------------------------------------------
def test_concurrent_groups_run_on_real_threads_with_a_single_effect(full_run):
    _, _, _, evidence, _ = full_run
    log = records(evidence)
    groups = [r for r in log if r["kind"] == "concurrency"]
    assert len(groups) == 4 and {g["mode"] for g in groups} == {"concurrent", "same_instant"}
    for group in groups:
        starts, ends = group["t_start"], group["t_end"]
        assert max(starts) - min(starts) < 1.0                    # liberadas juntas pela barreira
    concurrent = [g for g in groups if g["mode"] == "concurrent"]
    for group in concurrent:
        results = [r for r in log if r["kind"] == "op_result" and r["seq"] in group["seqs"]]
        assert sorted(r["duplicate"] for r in results) == [False, True]      # um efeito, um duplicate


# --------------------------------------------------------------------------
# agenda / manifesto do Driver e isolamento
# --------------------------------------------------------------------------
def test_agenda_loader_is_fail_closed(tmp_path):
    agenda, manifest_path = write_inputs(tmp_path)
    manifest = load_manifest(manifest_path)
    assert len(load_agenda(agenda, manifest)) == 4987
    bad = dict(manifest, public_agenda_sha256="0" * 64)
    with pytest.raises(HarnessError, match="nao confere"):
        load_agenda(agenda, bad)
    for broken in (dict(manifest, mark_paid_version_id=None), dict(manifest, mark_paid_version_id=0),
                   dict(manifest, oracle_sha256="x"), dict(manifest, note="mentions the Oracle")):
        path = tmp_path / "m.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(HarnessError):
            load_manifest(path)


def test_driver_package_is_self_contained_and_oracle_free():
    allowed = {"sim.driver"}
    for path in (SIM_ROOT / "driver").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in ("oracle.json", "world.json", "sim.oracle", "sim.generator", "sim.collector",
                      "sim.evaluator", "sim.stack", "psycopg", "sqlalchemy", "docker"):
            assert token not in text.replace("`", ""), (path.name, token)
        for node in ast.walk(ast.parse(text)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            for name in names:
                root = name.split(".")[0]
                assert root in set(__import__("sys").stdlib_module_names) or name.startswith("sim.driver"), (
                    path.name, name)


# --------------------------------------------------------------------------
# reconciliacao POR OPERACAO (tabela completa) e recuperacao de 5xx/timeouts
# --------------------------------------------------------------------------
def _template_regex(template: str) -> str:
    import re

    return "^" + re.sub(r"\\{[a-z_]+\\}", "[^/]+", re.escape(template)) + "$"


def _chain_for(op_name: str):
    ops = json.loads(AGENDA.read_bytes())["ops"]
    target = next(op for op in ops if op["op"] == op_name)
    rec = target.get("rec_ref") or next(o["rec_ref"] for o in ops if o.get("ref") == target.get("ref") and o.get("rec_ref"))
    refs = {o["ref"] for o in ops if o.get("rec_ref") == rec and "ref" in o}
    return rec, refs, lambda op: op.get("rec_ref") == rec or op.get("ref") in refs


def test_reconciliation_table_covers_every_op_and_matches_the_retry_policy():
    from sim.driver.dispatch import RECONCILIATION

    assert set(RECONCILIATION) == set(PUBLIC_OPS)
    for op in RETRY_SAFE:
        assert RECONCILIATION[op].startswith(("idempotent_retry", "retry_then_read"))
    for op in NO_BLIND_RETRY:
        assert not RECONCILIATION[op].startswith("idempotent_retry")
    assert RECONCILIATION["restart_backend"] == "harness_op"


@pytest.mark.parametrize("op_name", sorted(RETRY_SAFE))
def test_each_retry_safe_op_survives_one_timeout_and_fails_closed_after_three(tmp_path, op_name):
    _, _, keep = _chain_for(op_name)
    row = next(item for item in DISPATCH_TABLE if item[0] == op_name)
    pattern, method = _template_regex(row[2]), row[1]

    baseline, fake0, *_ = make_driver(tmp_path / "base", ops_filter=keep)
    run_slots(baseline, fake0)
    normal = sum(1 for m, path, _ in fake0.calls if m == method and __import__("re").search(pattern, path.split("?")[0]))
    assert normal >= 1

    flaky, fake1, store1, evidence1, sleep1 = make_driver(tmp_path / "flaky", ops_filter=keep)
    fake1.fail(pattern.strip("^$"), "timeout", 1, method)
    run_slots(flaky, fake1)
    seen = sum(1 for m, path, _ in fake1.calls if m == method and __import__("re").search(pattern, path.split("?")[0]))
    assert seen == normal + 1 and store1.counts().get("failed", 0) == 0
    assert 2.0 in sleep1.calls                                     # backoff real entre as tentativas

    dead, fake2, *_ = make_driver(tmp_path / "dead", ops_filter=keep)
    # change_due_date ainda le a conta e reenvia UMA vez depois das 3 tentativas (E-2): precisa de 4 falhas
    fake2.fail(pattern.strip("^$"), "timeout", 4 if op_name == "change_due_date" else 3, method)
    with pytest.raises(HarnessError):
        run_slots(dead, fake2)


def test_five_hundred_is_ambiguous_and_never_a_blind_retry_for_create(tmp_path):
    driver, fake, store, *_ = make_driver(tmp_path, ops_filter=lambda op: op["seq"] == 1)
    fake.fail(r"^/accounts/$", "500", 1, "POST")
    driver.run_slot(0, "09:00")
    assert posts(fake) == 2 and store.op_status(1) == "done"      # 500 -> reconciliou -> ausencia estavel -> 1 resend


def test_product_under_test_is_unchanged_since_the_proven_images():
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git indisponivel")
    probe = subprocess.run(["git", "cat-file", "-e", "d100ce23462989dd1047ceabf0a30dc7e5b0d62d"], cwd=REPO,
                           capture_output=True)
    if probe.returncode != 0:
        pytest.skip("commit d100ce2 ausente neste clone")
    for tree in ("backend", "frontend"):
        old = subprocess.run(["git", "rev-parse", f"d100ce23462989dd1047ceabf0a30dc7e5b0d62d:{tree}"], cwd=REPO,
                             capture_output=True, text=True).stdout.strip()
        new = subprocess.run(["git", "rev-parse", f"HEAD:{tree}"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        assert old and old == new, f"{tree}/ mudou entre d100ce2 e HEAD"
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no", "--", "backend", "frontend"],
                           cwd=REPO, capture_output=True, text=True).stdout.strip()
    assert dirty == ""
