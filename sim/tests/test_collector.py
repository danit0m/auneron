"""
SIM-1.5 -- gates estaticos do Evidence Collector: GET-only estrutural,
allowlist, plano sem valores, `safe_at` executavel, cobertura, paginacao e
ausencia de efeito colateral nas leituras do Collector.
"""

from __future__ import annotations

import ast
import collections
import copy
import json

import pytest

from sim.collector.allowlist import COLLECTOR_ROUTES
from sim.collector.allowlist import CapabilityViolation
from sim.collector.allowlist import route_name
from sim.collector.chainlog import ChainLog
from sim.collector.client import GetOnlyClient
from sim.collector.collect import HarnessError
from sim.collector.collect import missing_items
from sim.collector.collect import select_items
from sim.collector.collect import verify_records
from sim.collector.plan_schema import FORBIDDEN_FIELDS
from sim.collector.plan_schema import PlanInvalid
from sim.collector.plan_schema import validate_plan
from sim.oracle.collection_plan import compile_plan
from sim.tests.collector_support import load_agenda_ops
from sim.tests.collector_support import load_oracle
from sim.tests.collector_support import make_collector
from sim.tests.collector_support import orchestrate
from sim.tests.conftest import SIM_ROOT
from sim.tests.driver_support import make_driver
from sim.tests.fake_product import FakeProduct


@pytest.fixture(scope="module")
def oracle():
    return load_oracle()


@pytest.fixture(scope="module")
def plan(oracle):
    return compile_plan(oracle, load_agenda_ops())


# --------------------------------------------------------------------------
# capability estrutural
# --------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "OPTIONS", "HEAD"])
def test_client_refuses_every_non_get_before_the_network(method):
    fake = FakeProduct()
    client = GetOnlyClient(fake, "http://sim-backend:8000", "k")
    with pytest.raises(CapabilityViolation, match="so executa GET"):
        client.request(method, "/accounts/1")
    assert fake.calls == []


def test_client_has_no_mutating_surface():
    client = GetOnlyClient(FakeProduct(), "http://sim-backend:8000", "k")
    public = {name for name in dir(client) if not name.startswith("_")}
    assert public == {"get", "request"}
    source = (SIM_ROOT / "collector" / "client.py").read_text(encoding="utf-8")
    for literal in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "'POST'", "'PUT'", "'PATCH'", "'DELETE'"):
        assert literal not in source, literal


@pytest.mark.parametrize("path", [
    "/recommendations/next-best-action/accounts/1/episodes/2026-01-05",   # o GET persiste snapshot
    "/brain/1", "/brain", "/orchestrator/health", "/accounts/1/execute-mark-paid",
    "/approvals/1/decision", "/accounts/?status=aberto", "/memories/1/history", "/dashboard/",
    "/accounts/detect-overdue", "/upload/", "/work-items/1/comments",
])
def test_collector_allowlist_refuses_everything_else(path):
    with pytest.raises(CapabilityViolation):
        route_name(path)


def test_collector_allowlist_accepts_the_planned_routes():
    samples = ["/accounts/1", "/accounts/?cliente=x&skip=0&limit=200", "/accounts/1/classification",
               "/brain/?account_id=1&limit=500&skip=0", "/memories?scope_type=account&account_id=1&limit=100",
               "/recommendations/mark-overdue/accounts/1/episodes/2026-01-05",
               "/recommendations/human-escalation/accounts/1/episodes/2026-01-05",
               "/approvals?limit=100&after_id=5", "/approvals/3", "/work-items/9",
               "/work-items/9/escalation-observations?limit=100", "/work-items?scope_type=account&account_id=1&limit=100",
               "/outcomes/accounts/1/episodes/2026-01-05", "/auth/me"]
    names = {name for name, _, _ in COLLECTOR_ROUTES}
    assert {route_name(path) for path in samples} <= names and len({route_name(p) for p in samples}) == len(samples)


def test_collector_refuses_other_hosts():
    for url in ("http://127.0.0.1:8100", "http://localhost:8000", "http://sim-postgres:5432",
                "http://host.docker.internal:8000"):
        with pytest.raises(CapabilityViolation):
            GetOnlyClient(FakeProduct(), url, "k")


# --------------------------------------------------------------------------
# plano de coleta sem valores
# --------------------------------------------------------------------------
def test_plan_is_valid_deterministic_and_value_free(oracle, plan):
    validate_plan(plan)
    again = compile_plan(oracle, load_agenda_ops())
    assert json.dumps(plan, sort_keys=True) == json.dumps(again, sort_keys=True)
    text = json.dumps(plan)
    for token in ("overdue_alert", "due_soon", "due_today", '"critical"', "approved", "verified", "succeeded",
                  "PAGAMENTO_REGULAR", "ATRASO_RECORRENTE", "INSUFFICIENT_DATA", "payment_promised", "pago"):
        assert token not in text, token
    assert "oracle" not in text.lower() and "world" not in text.lower()


def test_plan_covers_every_collectable_product_fact(oracle, plan):
    covered = {exp_id for item in plan["items"] for exp_id in item["satisfies"]}
    wanted = {e["exp_id"] for e in oracle["expectations"]
              if e["evidence_class"] == "PRODUCT_FACT" and e["safe_at"] != "not_collected"}
    assert covered == wanted
    for exp in oracle["expectations"]:
        if exp["evidence_class"] != "PRODUCT_FACT":
            assert exp["exp_id"] not in covered           # DRIVER evidence / derivation nunca sao coletados
    routes = {item["route"] for item in plan["items"]}
    assert "nba" not in " ".join(routes) and not any("next-best-action" in item["path"] for item in plan["items"])


def test_plan_safe_at_matches_the_oracle(oracle, plan):
    by_id = {e["exp_id"]: e for e in oracle["expectations"]}
    for item in plan["items"]:
        for exp_id in item["satisfies"]:
            exp = by_id[exp_id]
            assert exp["safe_at"] == item["safe_at"]
            if item["safe_at"] == "pre_slot":
                assert (exp["day"], exp["at"]) == (item["day"], item["at"])
            if item["safe_at"] == "after_daily_barrier":
                assert exp["day"] == item["day"]
            assert item["requires"] == exp.get("requires", [])


@pytest.mark.parametrize("mutate,expected", [
    (lambda p: p["items"][0].update(policy=True), "campo de verdade esperada"),
    (lambda p: p["items"][0].update(ideal="x"), "campo de verdade esperada"),
    (lambda p: p["items"][0]["bind"].update(expected=1), "campo de verdade esperada"),
    (lambda p: p["items"][0].update(extra=1), "chaves nao previstas"),
    (lambda p: p["items"][0].update(safe_at="in_slot"), "safe_at/paging"),
    (lambda p: p["items"][0].update(route="brain"), "fora da allowlist"),
    (lambda p: p["items"][0].update(path="/recommendations/next-best-action/accounts/{account_id}/episodes/{due}",
                                    route="human_escalation_eligibility"), "fora da allowlist"),
    (lambda p: p["items"][0].update(at=None), r"sem \(day, at\)"),
    (lambda p: p["items"][0].update(principal="sim-admin"), "principal"),
    (lambda p: p["items"].append(copy.deepcopy(p["items"][0])), "duplicado"),
    (lambda p: p.update(schema="x"), "schema"),
])
def test_plan_schema_refuses_values_and_unknown_shapes(plan, mutate, expected):
    broken = copy.deepcopy(plan)
    mutate(broken)
    with pytest.raises(PlanInvalid, match=expected):
        validate_plan(broken)
    assert {"policy", "ideal", "semantics", "factors", "case_ids"} <= FORBIDDEN_FIELDS


# --------------------------------------------------------------------------
# execucao: safe_at executavel, cobertura, paginacao, retry
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def orchestrated(tmp_path_factory, plan):
    tmp_path = tmp_path_factory.mktemp("orch")
    driver, fake, store, evidence, _ = make_driver(tmp_path)
    collector, session, chain, _ = make_collector(tmp_path, fake, plan)
    orchestrate(driver, fake, collector)
    return driver, fake, collector, chain, session


def test_every_plan_item_is_collected_at_its_own_moment(plan, orchestrated):
    driver, fake, collector, chain, session = orchestrated
    records = ChainLog.read(chain.path)
    assert missing_items(plan, records) == []
    assert verify_records(plan, records) == []
    assert ChainLog.verify(chain.path)[0] and len(records) == sum(1 for i in plan["items"] if i["channel"] == "http")


def test_collector_never_reads_the_nba_endpoint(orchestrated):
    driver, fake, *_ = orchestrated
    nba = [c for c in fake.calls if "/next-best-action/" in c[1]]
    consults = sum(1 for slot in driver.slots.values() for op in slot if op["op"] == "consult_nba")
    assert len(nba) == consults == 197                      # so as chamadas legitimas do Driver


def test_collector_issues_only_get_requests(orchestrated):
    driver, fake, collector, chain, session = orchestrated
    methods = collections.Counter(c[0] for c in fake.calls if c[2].get("X-API-Key") and
                                  "Idempotency-Key" not in c[2])
    assert session.logins >= 1
    gets_by_collector = [c for c in fake.calls if c[0] == "GET"]
    assert len(gets_by_collector) >= len(ChainLog.read(chain.path))
    assert set(methods) <= {"GET", "POST", "PUT"}           # POST/PUT sao do Driver; o Collector so faz GET


def test_pre_slot_is_collected_before_and_post_slot_after_the_slot_ops(tmp_path, plan):
    driver, fake, *_ = make_driver(tmp_path)
    collector, *_ = make_collector(tmp_path, fake, plan)
    orchestrate(driver, fake, collector, 0, 0)
    order = [(c[0], c[1].split("?")[0]) for c in fake.calls]
    eligibility = [i for i, (m, p) in enumerate(order) if m == "GET" and "/recommendations/human-escalation/" in p]
    materialize = [i for i, (m, p) in enumerate(order) if m == "POST" and "/human-escalation/" in p]
    assert eligibility and materialize
    first_slot_elig = [i for i in eligibility if i < materialize[0]]
    assert first_slot_elig                                       # leitura ANTES da materializacao do slot


def test_out_of_moment_or_unknown_collection_is_invalid(plan, orchestrated):
    _, _, _, chain, _ = orchestrated
    records = copy.deepcopy(ChainLog.read(chain.path))
    victim = next(r for r in records if r["trigger"]["kind"] == "after_daily_barrier")
    victim["trigger"]["day"] += 1
    ghost = dict(records[0], item_id="I-999999")
    duplicate = copy.deepcopy(records[1])
    problems = verify_records(plan, [victim, ghost, duplicate, records[1]])
    assert any("fora do seu momento seguro" in p for p in problems)
    assert any("desconhecido" in p for p in problems) and any("duas vezes" in p for p in problems)


def test_selection_follows_safe_at_and_barrier_requirements(tmp_path, plan):
    driver, fake, *_ = make_driver(tmp_path)
    collector, *_ = make_collector(tmp_path, fake, plan)
    assert {i["safe_at"] for i in select_items(plan, "after_daily_barrier", 15, "18:00")} == {"after_daily_barrier"}
    assert all(i["day"] == 15 for i in select_items(plan, "after_daily_barrier", 15, "18:00"))
    assert all((i["day"], i["at"]) == (0, "11:00") for i in select_items(plan, "pre_slot", 0, "11:00"))
    gated = next(i for i in plan["items"] if i["requires"] and i["safe_at"] == "after_daily_barrier")
    orchestrate_days = gated["day"]
    (collector.home / "refs.json").write_text(json.dumps({"refs": {}, "due_days": {}}), encoding="utf-8")
    with pytest.raises(HarnessError, match="exige barreira"):
        collector.trigger("after_daily_barrier", orchestrate_days, "18:00")
    with pytest.raises(HarnessError, match="gatilho desconhecido"):
        collector.trigger("whenever", 0, "09:00")


def test_missing_refs_file_and_refs_are_harness_errors(tmp_path, plan):
    driver, fake, *_ = make_driver(tmp_path)
    collector, *_ = make_collector(tmp_path, fake, plan)
    with pytest.raises(HarnessError, match="refs.json ausente"):
        collector.trigger("after_daily_barrier", 0, "18:00")
    (collector.home / "refs.json").write_text(json.dumps({"refs": {}, "due_days": {}}), encoding="utf-8")
    with pytest.raises(HarnessError, match="sem conta"):
        collector.trigger("after_daily_barrier", 0, "18:00")


def test_retrigger_is_idempotent(tmp_path, plan):
    driver, fake, *_ = make_driver(tmp_path)
    collector, _, chain, _ = make_collector(tmp_path, fake, plan)
    orchestrate(driver, fake, collector, 0, 2)
    count = len(ChainLog.read(chain.path))
    export = collector.trigger("after_daily_barrier", 1, "18:00")
    assert export["collected"] == 0 and len(ChainLog.read(chain.path)) == count


def paging_plan(route, path, paging, query, bind=None):
    item = {"item_id": "I-000001", "route": route, "path": path, "bind": bind or {}, "query": query,
            "paging": paging, "safe_at": "after_daily_barrier", "day": 0, "at": None, "requires": [],
            "principal": "sim-harness-observer", "satisfies": ["E-1"], "channel": "http"}
    return {"schema": "sim.collection_plan.v1", "execution_scenario_id": "x",
            "clock": {"d0": "2026-01-05", "utc_offset_minutes": -180},
            "observer_email": "sim-harness-observer@nova-horizonte.example.com", "items": [item]}


def collect_one(tmp_path, fake, plan):
    collector, _, chain, sleeps = make_collector(tmp_path, fake, plan)
    (collector.home / "refs.json").write_text(json.dumps({"refs": {"R-1": {"account_id": 1}}, "due_days": {}}),
                                              encoding="utf-8")
    collector.trigger("after_daily_barrier", 0, "18:00")
    return ChainLog.read(chain.path)[0], sleeps


def test_pagination_is_followed_to_the_end(tmp_path):
    fake = FakeProduct()
    for number in range(250):
        fake.approvals[number + 1] = {"request_id": number + 1, "kind": "mark_paid", "requester": "x",
                                      "status": "pending", "decider": None, "account_id": 1,
                                      "expected_status": "aberto", "consumed": False}
    record, _ = collect_one(tmp_path, fake, paging_plan("approvals", "/approvals", "after_id", {"limit": "100"}))
    assert len(record["body"]["items"]) == 250 and record["pages"] == 3
    fake2 = FakeProduct()
    fake2.brain[1] = [{"id": n, "severity": "info"} for n in range(1200)]
    record, _ = collect_one(tmp_path / "b", fake2, paging_plan(
        "brain", "/brain/", "skip", {"account_id": "{account_id}", "limit": "500"}, {"account_id": {"rec_ref": "R-1"}}))
    assert len(record["body"]["items"]) == 1200 and record["pages"] == 3


def test_truncated_work_list_is_harness_error(tmp_path):
    fake = FakeProduct()
    for number in range(100):
        fake.work_items[number + 1] = {"id": number + 1, "account_id": 1}
    with pytest.raises(HarnessError, match="truncada"):
        collect_one(tmp_path, fake, paging_plan(
            "work_items", "/work-items", "work_list",
            {"scope_type": "account", "account_id": "{account_id}", "limit": "100"}, {"account_id": {"rec_ref": "R-1"}}))


def test_read_retries_then_fails_closed(tmp_path):
    plan = paging_plan("account", "/accounts/{account_id}", "none", {}, {"account_id": {"rec_ref": "R-1"}})
    fake = FakeProduct()
    fake.accounts[1] = {"id": 1}
    fake.fail(r"^/accounts/1$", "500", 2, "GET")
    record, sleeps = collect_one(tmp_path, fake, plan)
    assert record["status"] == 200 and sleeps == [2, 2]
    fake2 = FakeProduct()
    fake2.accounts[1] = {"id": 1}
    fake2.fail(r"^/accounts/1$", "500", 3, "GET")
    with pytest.raises(HarnessError, match="3 tentativas"):
        collect_one(tmp_path / "x", fake2, plan)


def test_observer_session_relogs_on_401(tmp_path):
    plan = paging_plan("account", "/accounts/{account_id}", "none", {}, {"account_id": {"rec_ref": "R-1"}})
    fake = FakeProduct()
    fake.accounts[1] = {"id": 1}
    collector, session, chain, _ = make_collector(tmp_path, fake, plan)
    (collector.home / "refs.json").write_text(json.dumps({"refs": {"R-1": {"account_id": 1}}, "due_days": {}}),
                                              encoding="utf-8")
    session.login()
    fake.expire_sessions()
    collector.trigger("after_daily_barrier", 0, "18:00")
    assert session.logins == 2 and ChainLog.read(chain.path)[0]["status"] == 200


# --------------------------------------------------------------------------
# isolamento do pacote
# --------------------------------------------------------------------------
def test_collector_package_is_self_contained_and_oracle_free():
    stdlib = set(__import__("sys").stdlib_module_names)
    for path in (SIM_ROOT / "collector").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in ("oracle.json", "world.json", "sim.oracle", "sim.generator", "sim.driver", "sim.evaluator",
                      "sim.stack", "psycopg", "sqlalchemy", "docker", "next-best-action/accounts"):
            if token == "next-best-action/accounts":
                continue
            assert token not in text.replace("`", ""), (path.name, token)
        for node in ast.walk(ast.parse(text)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            for name in names:
                assert name.split(".")[0] in stdlib or name.startswith("sim.collector"), (path.name, name)


def test_collected_evidence_flows_into_the_evaluator(orchestrated, oracle, plan):
    """Fiacao completa Driver -> Collector -> Evaluator contra o FAKE: o fake nao tem a verdade do
    produto (muitas DIVERGENCE sao esperadas), mas NENHUMA expectativa pode virar HARNESS_ERROR."""
    from sim.driver.evidence import EvidenceLog
    from sim.evaluator.evaluate import HARNESS_ERROR
    from sim.evaluator.evaluate import evaluate

    driver, fake, collector, chain, _ = orchestrated
    refs = json.loads((collector.home / "refs.json").read_text(encoding="utf-8"))
    facts = {"provenance_report": {"observations": {"observed_fact_with_provenance": 0,
                                                    "observed_fact_legacy_without_provenance": 0},
                                   "integrity_failures": []}, "provenance_exit": 0}
    document = evaluate(oracle, plan, ChainLog.read(chain.path), EvidenceLog.read(driver.evidence.path), refs, facts)
    summary = document["summary"]
    assert HARNESS_ERROR not in summary and document["run_valid"], summary
    assert sum(summary.values()) == len(oracle["expectations"]) == 13074
