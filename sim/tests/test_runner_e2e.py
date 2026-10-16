"""
SIM-1.5E -- laco COMPLETO sem Docker: `NhRunner` + `RunOrchestrator` reais + Driver e Collector reais contra o produto
falso, com a logica real de `LiveServices` (leitura incremental, cadeias, balanco de massa, tripwire de autoridade,
logs por dia), pacote selado, avaliacao offline e claim. Mais: os comandos dos adaptadores `lab_ports` (conferidos
com um lab de duplos) e a escala sintetica do ledger/pacote/verificadores no volume do `nh-small`.
"""

from __future__ import annotations

import gzip
import json
import time
from types import SimpleNamespace

import pytest

from sim.collector.chainlog import ChainLog
from sim.driver.runner import virtual_utc
from sim.evaluator.evaluate import evaluate
from sim.live import scenarios as S
from sim.oracle.collection_plan import compile_plan
from sim.runner import daily
from sim.runner import harness_side
from sim.runner import interlock
from sim.runner import lab_ports
from sim.runner import package
from sim.runner import results as results_mod
from sim.runner import runner as runner_mod
from sim.runner.declare import GIB
from sim.runner.live import IncrementalChain
from sim.runner.live import LiveServices
from sim.runner.live import Ports
from sim.runner.host import RunLock
from sim.runner.ledger import RunLedger
from sim.runner.registry import Registry
from sim.stack.lab.run_orchestrator import Hooks
from sim.tests.collector_support import make_collector
from sim.tests.driver_support import make_driver
from sim.tests.fake_product import FakeProduct
from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.runner import rim as rim_mod
from sim.tests.runner_support import declared
from sim.tests.runner_support import observed
from sim.tests.runner_support import good_growth
from sim.tests.runner_support import good_preflight_facts
from sim.tests.runner_support import make_rim
from sim.tests.test_live_harness import OBSERVER_EMAIL

DAY = S.DAYS["c0"]
GOOD_PROVENANCE = {"provenance_exit": 0, "provenance_report": {"observations": {"observed_fact_with_provenance": 0,
                                                                                  "observed_fact_legacy_without_provenance": 0},
                                                               "integrity_failures": 0}}


def build_world(tmp_path, *, tamper=None):
    builder, oracle = S.scen_c0("inst", "c", DAY), S.oracle_c0("c", DAY)
    info = builder.write(tmp_path / "in", 7)
    plan = compile_plan(oracle, info["ops"], S.D0, -180, OBSERVER_EMAIL)
    fake = FakeProduct()
    driver, fake, store, evidence, _ = make_driver(tmp_path, fake, agenda_path=tmp_path / "in" / "public_agenda.json")
    collector, _, chain, _ = make_collector(tmp_path, fake, plan)
    return SimpleNamespace(oracle=oracle, info=info, plan=plan, fake=fake, driver=driver, store=store, collector=collector,
                           chain=chain, ops=info["ops"], evidence_path=tmp_path / "state" / "driver_evidence.jsonl")


def real_hooks(w) -> Hooks:
    d = w.driver

    def clock_set(day, at):
        w.fake.now = virtual_utc(d.d0, day, at, d.offset)

    return Hooks(
        clock_set=clock_set, driver_slots=lambda: [list(k) for k in d.slot_keys()], driver_run_slot=lambda day, at: d.run_slot(day, at),
        driver_export_refs=lambda: {"refs": w.store.all_refs(), "due_days": w.store.all_due()},
        driver_mark_harness_done=lambda seq, note: d.mark_harness_done(seq, note),
        collector_load_refs=lambda data: (w.collector.home / "refs.json").write_text(json.dumps(data), encoding="utf-8"),
        collector_trigger=lambda kind, day, at: w.collector.trigger(kind, day, at),
        collector_mark_barrier=lambda name: w.collector.mark_barrier(name), daily_barrier=lambda day: {},
        restart_backend=lambda: {}, restart_barrier=lambda: {}, floor_activation=lambda: {})


def file_lines(path, already):
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()][already:] if path.exists() else []


def fake_ports(w, run, **over) -> Ports:
    values = dict(
        new_driver_lines=lambda n: file_lines(w.evidence_path, n), new_collector_lines=lambda n: file_lines(w.chain.path, n),
        driver_status=lambda: {"counts": w.store.counts(), "in_flight": w.store.in_flight()},
        db_counts=lambda: {"accounts": len(w.fake.accounts), "approvals": len(w.fake.approvals), "work_items": len(w.fake.work_items)},
        containers=lambda: {n: {"running": True, "started_at": "t0", "networks": ["n"]} for n in ("backend", "driver")},
        run_id_files=lambda: [run.run_id, run.run_id],
        last_barrier=lambda: {"total_s": 175.5, "time_floor_satisfied": True, "stable_after_attempts": 1},
        skew_ms=lambda: 20, o1_ms=lambda: 30, free_bytes=lambda: 50 * GIB, virtual_now=lambda: "v", real_now=lambda: "r",
        log_slice=lambda service: f"{service} log".encode(), probe=lambda day, when: {"clean": True},
        preserve=lambda cls, target: {"ok": True}, finalize_extra=lambda target: finalize_extra(w, target),
        growth=lambda: {k: v for k, v in good_growth().items() if k != "log_bytes"})
    values.update(over)
    return Ports(**values)


def finalize_extra(w, target):
    (target / "harness").mkdir(exist_ok=True)
    (target / "harness" / "refs.json").write_text(json.dumps({"refs": w.store.all_refs(), "due_days": w.store.all_due()}))
    (target / "harness" / "facts.json").write_text(json.dumps(GOOD_PROVENANCE))
    (target / "inputs").mkdir(exist_ok=True)
    (target / "inputs" / "oracle.json").write_bytes(canonical_bytes(w.oracle))
    (target / "inputs" / "collection_plan.json").write_bytes(canonical_bytes(w.plan))
    (target / "inputs" / "preregistration.json").write_bytes(canonical_bytes(harness_side.preregistration(w.oracle)))
    return {"violations": harness_side.verify_authority_offline(target, w.oracle)["violations"]}


def start(tmp_path, w, balance_mode="enforce", timeline=None, overrides=None, **port_over):
    last_day, floor_day, restart_day = timeline or (DAY + 1, DAY + 1, DAY + 1)
    d = declared(last_day=last_day, floor_day=floor_day, restart_day=restart_day, balance_mode=balance_mode)
    d["oracle_sha256"] = sha256_bytes(canonical_bytes(w.oracle))
    d["plan_sha256"] = sha256_bytes(canonical_bytes(w.plan))
    d["preregistration_sha256"] = sha256_bytes(canonical_bytes(harness_side.preregistration(w.oracle)))
    d.update(overrides or {})
    rim = rim_mod.build_rim("REHEARSAL", d, observed(d))
    registry = Registry(tmp_path / "runs")
    info, ledger = runner_mod.open_new_run(registry, rim, purpose="FIRST")
    runner_mod.run_preflight(ledger, registry, rim, good_preflight_facts(rim), projected_growth_bytes=GIB)
    interlock.authorize_rehearsal(info["run_dir"], info["run_id"], info["rim_sha256"], "REHEARSAL", "PO", "e2e")
    run = SimpleNamespace(rim=rim, registry=registry, info=info, ledger=ledger, run_dir=info["run_dir"], run_id=info["run_id"])
    ports = fake_ports(w, run, **port_over)
    live = LiveServices(
        run_dir=run.run_dir, run_id=run.run_id, plan=w.plan, agenda_ops=w.ops, timeline=rim["declared"]["timeline"], ports=ports,
        baseline={}, expected_containers={"backend": "t0", "driver": "t0"}, expected_networks={"backend": ["n"], "driver": ["n"]},
        barrier_hard_ms=600_000)
    services = live.services(real_hooks(w))
    nh = runner_mod.NhRunner(run_dir=run.run_dir, rim=rim, ledger=ledger, registry=registry, services=services,
                             lock=RunLock(run.run_dir, pid=lambda: 1, alive=lambda p: False))
    return run, live, nh


def test_end_to_end_run_with_real_driver_collector_orchestrator_completes_and_evaluates_offline(tmp_path):
    w = build_world(tmp_path)
    run, live, nh = start(tmp_path, w)
    result = nh.run()
    assert result["state"] == "COMPLETED", RunLedger(run.run_dir).events()[-1]
    assert package.verify(run.run_dir, expected_digest=result["digest"])["ok"]
    records = [json.loads((run.run_dir / "days" / f"D{d:03d}.json").read_bytes()) for d in range(DAY + 2)]
    daily.verify_day_chain(records)
    assert all(not r["violations"] for r in records)
    # evidencia incremental no pacote == a do Driver/Collector (cadeia inteira)
    assert (run.run_dir / "driver" / "driver_evidence.jsonl").read_bytes().count(b"\n") == live.driver_chain.count > 0
    assert (run.run_dir / "collector" / "collected.jsonl").read_bytes().count(b"\n") == live.collector_chain.count > 0
    assert gzip.decompress((run.run_dir / "logs" / "backend" / "D000.log.gz").read_bytes()) == b"backend log"
    assert (run.run_dir / "authority" / "verify_authority.json").exists()
    # avaliacao OFFLINE sobre o pacote: Evaluator real, sem HARNESS_ERROR; claim gerada do estado
    work = run.run_dir
    (work / "inputs").mkdir(exist_ok=True)
    report = harness_side.evaluate_package(run.run_dir, registry=run.registry)
    evaluation = report["evaluation"]
    assert evaluation["run_valid"] is True and evaluation["summary"].get("HARNESS_ERROR", 0) == 0
    ledger = RunLedger(run.run_dir)
    assert ledger.state == "COMPLETED" and "EVALUATED" in ledger.replay.seen
    claim = json.loads((run.run_dir / "claim.json").read_bytes())
    assert claim["level"] == "TECHNICAL_REHEARSAL"                          # ensaio: sem claim de negocio
    assert package.verify(run.run_dir)["ok"]                                  # o selo original continua valido
    assert results_mod.verify_results(run.run_dir)["ok"]                      # e os resultados tem digest final vinculado ao ledger
    direct = evaluate(w.oracle, w.plan, ChainLog.read(w.chain.path), [json.loads(l) for l in w.evidence_path.read_text().splitlines()],
                      {"refs": w.store.all_refs(), "due_days": w.store.all_due()}, {})
    assert direct["summary"] == evaluation["summary"]


def test_the_incremental_chain_reader_accepts_only_continuous_lines():
    chain = IncrementalChain()
    first = {"n": 1, "prev": "0" * 64, "x": 1}
    from sim.runner.live import line_hash

    first["hash"] = line_hash(first)
    assert chain.consume([json.dumps(first)]) == [first] and chain.count == 1 and chain.ok
    forged = {"n": 2, "prev": "f" * 64, "x": 2, "hash": "0" * 64}
    assert chain.consume([json.dumps(forged)]) == [] and chain.ok is False
    assert chain.consume([json.dumps(first)]) == []                           # depois de quebrar, nada mais e aceito


def test_a_broken_collector_chain_invalidates_the_day(tmp_path):
    w = build_world(tmp_path)
    run, live, nh = start(tmp_path, w, new_collector_lines=lambda n: ['{"n": 1, "prev": "bad", "hash": "x"}'] if n == 0 else [])
    result = nh.run()
    assert (result["state"], result["class"]) == ("INVALID", "HARNESS_ERROR")


def test_the_mass_balance_catches_a_duplicated_effect_in_the_product(tmp_path):
    w = build_world(tmp_path)
    run, live, nh = start(tmp_path, w, db_counts=lambda: {"accounts": len(w.fake.accounts) + 1,
                                                          "approvals": len(w.fake.approvals), "work_items": len(w.fake.work_items)})
    result = nh.run()
    assert (result["state"], result["class"]) == ("INVALID", "MASS_BALANCE_MISMATCH")


def test_an_unexpected_backend_restart_aborts_but_the_planned_restart_day_is_allowed(tmp_path):
    w = build_world(tmp_path)
    flips = {"count": 0}

    def containers():
        flips["count"] += 1
        started = "t0" if flips["count"] == 1 else "t1"
        return {"backend": {"running": True, "started_at": started, "networks": ["n"]},
                "driver": {"running": True, "started_at": "t0", "networks": ["n"]}}

    run, live, nh = start(tmp_path, w, containers=containers)
    live.expected_containers = {"backend": "t0", "driver": "t0"}
    live.timeline = {**live.timeline, "restart_day": DAY + 1}
    result = nh.run()
    assert (result["state"], result["class"]) == ("ABORTED", "INFRA_FAILURE")   # no dia anterior ao restart previsto


def test_authority_tripwire_inside_the_loop_invalidates_on_the_day_it_happens(tmp_path):
    w = build_world(tmp_path)
    original = w.driver.run_slot

    def run_slot(day, at):
        result = original(day, at)
        return result

    run, live, nh = start(tmp_path, w)
    live.tripwire.steps["MP-X"] = {"request": "sim-gerente-fin", "decide": "sim-gerente-fin"}      # segregacao ja violada
    line = {"kind": "op_result", "seq": 1, "day": DAY, "at": "10:00", "op": "execute_mark_paid", "actor": "sim-gerente-fin",
            "ref": "MP-X", "http_status": 200, "outcome": "done"}
    assert live.tripwire.feed([line])[0]["violation"] == "SEGREGATION_BREACH"
    assert run_slot is not None


# ------------------------------------------------------------------------------------------------- adaptadores do lab
class FakeLab:
    def __init__(self):
        self.calls = []
        self.config = SimpleNamespace(**{})
        self.live_dir = None

    class Runner:
        def __init__(self, outer):
            self.outer = outer

        def run(self, cmd, **kwargs):
            self.outer.calls.append(cmd)
            return SimpleNamespace(code=0, out="", err="", text="")


def test_every_adapter_command_goes_through_the_lab_prefix_guard(monkeypatch):
    seen = []

    def fake_dexec(lab, container, *args, **kwargs):
        assert container.startswith("auneron-sim-")
        seen.append((container, args))
        return SimpleNamespace(code=0, out="a\nb\n", err="", text="")

    monkeypatch.setattr(lab_ports.E, "dexec", fake_dexec)
    lab = SimpleNamespace(runner=SimpleNamespace(run=lambda cmd, **k: SimpleNamespace(code=0, out="", err="", text="")))
    ports = lab_ports.lab_ports(lab, "x", hooks_state={}, forbidden_hashes=[], oracle={}, inputs_dir=SIM_PATH, ttl_minutes=30)
    assert ports.new_driver_lines(3) == ["a", "b"] and ports.new_collector_lines(0) == ["a", "b"]
    assert ports.run_id_files() == ["a\nb", "a\nb"]
    commands = [" ".join(args) for _, args in seen]
    assert any("tail -n +4 /state/driver_evidence.jsonl" in c for c in commands)
    assert any("tail -n +1 /state/collected.jsonl" in c for c in commands)
    assert {c for c, _ in seen} == {"auneron-sim-driver", "auneron-sim-collector"}


SIM_PATH = __import__("pathlib").Path(__file__).resolve().parent


def test_lab_adapter_failure_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(lab_ports.E, "dexec", lambda lab, c, *a, **k: SimpleNamespace(code=1, out="", err="boom", text="boom"))
    lab = SimpleNamespace(runner=None)
    ports = lab_ports.lab_ports(lab, "x", hooks_state={}, forbidden_hashes=[], oracle={}, inputs_dir=SIM_PATH, ttl_minutes=30)
    with pytest.raises(Exception, match="leitura incremental falhou"):
        ports.new_driver_lines(0)


# ------------------------------------------------------------------------------------------------- escala sintetica (sem Docker)
def test_ledger_package_and_verifiers_handle_the_nh_volume_within_budget(tmp_path):
    from sim.runner import authority

    run_dir = tmp_path / "run"
    ledger = RunLedger(run_dir)
    ledger.open({"run_id": "RUN-x", "rim_sha256": "s", "kind": "REHEARSAL", "timeline": {"last_day": 179}})
    ledger.append("PREFLIGHT_PASSED", {})
    ledger.authorize_d0("t")
    ledger.append("RUN_STARTED", {})
    started = time.perf_counter()
    for day in range(180):
        ledger.append("DAY_STARTED", {"day": day})
        ledger.append("DAY_CLOSED", {"day": day, "hash": "h" * 64})
    assert ledger.verify()["events"] == 4 + 360
    # evidencia do tamanho do NH: 4.987 ops x 3 linhas e 9.162 itens coletados
    (run_dir / "driver").mkdir()
    (run_dir / "collector").mkdir()
    rows = []
    tripwire = authority.AuthorityTripwire()
    for seq in range(1, 4988):
        rows.append({"kind": "op_result", "seq": seq, "day": seq % 180, "at": "10:00", "op": "create_receivable",
                     "actor": "sim-faturamento", "ref": None, "http_status": 201, "outcome": "done"})
    assert tripwire.feed(rows) == []
    (run_dir / "driver" / "driver_evidence.jsonl").write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in rows * 3))
    (run_dir / "collector" / "collected.jsonl").write_bytes(b"x" * 60_000_000)
    for name in ("rim.json", "run_id.json"):
        (run_dir / name).write_text("{}")
    package.seal(run_dir, run_id="RUN-x", ledger_tail=ledger.tail)
    assert package.verify(run_dir)["ok"]
    assert time.perf_counter() - started < 60


def test_evaluator_handles_nh_sized_oracle_with_a_product_double_in_bounded_time():
    import json as _json
    from sim.tests.conftest import SIM_ROOT

    oracle = _json.loads((SIM_ROOT / "oracle" / "artifacts" / "nh-small-v2-s340001-oracle-v2.2" / "oracle.json").read_bytes())
    ops = _json.loads((SIM_ROOT / "scenarios" / "nh-small-v2-s340001" / "public_agenda.json").read_bytes())["ops"]
    plan = compile_plan(oracle, ops)
    started = time.perf_counter()
    result = evaluate(oracle, plan, [], [], {"refs": {}}, {})                  # sem evidencia: tudo vira HARNESS_ERROR/abstencao
    elapsed = time.perf_counter() - started
    assert len(result["results"]) == 13074 and result["run_valid"] is False and elapsed < 30


def test_the_incremental_chain_reader_checks_the_hash_independently_of_the_link_and_the_counter():
    from sim.runner.live import line_hash

    chain = IncrementalChain()
    record = {"n": 1, "prev": "0" * 64, "x": 1}
    record["hash"] = "0" * 64                                                 # encadeamento e contador corretos, hash errado
    assert chain.consume([json.dumps(record)]) == [] and chain.ok is False
    good = {"n": 1, "prev": "0" * 64, "x": 1}
    good["hash"] = line_hash(good)
    assert IncrementalChain().consume([json.dumps(good)]) == [good]
