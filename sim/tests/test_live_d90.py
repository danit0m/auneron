"""
SIM-1.5C: gates ESTATICOS do D90 (restart + Collector) no harness live versionado, sem Docker.

Cada regressao exigida pelo PO (F-D90-1/2) tem um teste que a detecta; os mutantes correspondentes ficam no runner de
controles negativos do 1.5C.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from sim.collector.collect import Collector
from sim.collector.plan_schema import COLLECTION_TRIGGERS
from sim.collector.plan_schema import PlanInvalid
from sim.collector.plan_schema import validate_plan
from sim.collector.collect import HarnessError as CollectorHarnessError
from sim.driver.agenda import load_agenda
from sim.driver.agenda import load_manifest
from sim.evaluator.evaluate import evaluate
from sim.live import flow
from sim.live import judges as J
from sim.live import scenarios as S
from sim.oracle.collection_plan import compile_plan
from sim.stack.lab.config import load_config
from sim.stack.lab.quiescence import HarnessError
from sim.stack.lab.run_orchestrator import RunOrchestrator
from sim.stack.lab.runner import Result
from sim.tests.collector_support import make_collector
from sim.tests.driver_support import make_driver
from sim.tests.fake_product import FakeProduct

CONFIG = load_config()
BOTH = {"auneron_sim_internal": {}, "auneron_sim_driver": {}}
OBSERVER_EMAIL = CONFIG.email("sim-harness-observer")


def identity(**override) -> dict:
    base = {"build_git_sha": CONFIG.commit, "build_git_dirty": "false", "build_source_digest": "d" * 64,
            "build_source_digest_algorithm": "sd1", "build_identity_state": "valid", "environment": "production",
            "maintenance_enabled": True, "evidence_floor": None, "evidence_floor_state": "unset"}
    base.update(override)
    return base


def inspect_json(started_at: str, networks=BOTH, running: bool = True) -> str:
    return json.dumps([{"NetworkSettings": {"Networks": networks}, "State": {"StartedAt": started_at, "Running": running}}])


class RestartRunner:
    """Docker falso do backend: `inspect` devolve a fila de estados; `restart` devolve o exit configurado.
    Os `docker exec` dos CLIs (Driver/Collector) sao respondidos por `cli_reply`."""

    def __init__(self, inspects, restart_code=0, cli_reply=None, log=None):
        self.inspects, self.restart_code, self.cli_reply = list(inspects), restart_code, cli_reply
        self.calls, self.log = [], log if log is not None else []

    def run(self, args, input_text=None, env=None, timeout=None, cwd=None):
        self.calls.append(list(args))
        if args[:2] == ["docker", "inspect"]:
            return Result(0, self.inspects.pop(0), "")
        if args[:2] == ["docker", "restart"]:
            self.log.append("docker restart")
            return Result(self.restart_code, "", "" if self.restart_code == 0 else "boom")
        if self.cli_reply is not None and args[:2] == ["docker", "exec"]:
            return self.cli_reply(args, input_text)
        return Result(0, "{}\n", "")


class RestartLab(SimpleNamespace):
    def __init__(self, *, before="2026-10-07T10:00:00Z", after="2026-10-07T10:05:00Z", after_networks=BOTH,
                 before_networks=BOTH, running=True, ready=True, identity_before=None, identity_after="same",
                 restart_code=0, barrier_error=None, cli_reply=None):
        self.log = []
        runner = RestartRunner([inspect_json(before, before_networks), inspect_json(after, after_networks, running)],
                               restart_code, cli_reply, self.log)
        before_id = identity() if identity_before is None else identity_before
        after_id = before_id if identity_after == "same" else identity_after
        super().__init__(runner=runner, config=CONFIG, barrier_error=barrier_error, ready=ready, barrier_calls=[],
                         records={"before": before_id, "after": after_id})
        self.clock = SimpleNamespace(set=lambda target: self.log.append(f"clock {target}"),
                                     virtual=lambda day, at: (day, at))

    def startup_record(self, since):
        return self.records["before" if since == 1 else "after"]

    def wait_ready(self):
        return {"ready": self.ready}

    def events(self):
        return ["e1"]

    def run_barrier(self, since, events, params=None):
        self.log.append("barrier")
        self.barrier_calls.append((since, list(events)))
        if self.barrier_error:
            raise HarnessError(self.barrier_error)
        return {"ok": True}


# ------------------------------------------------------------------------------- F-D90-1: restart real e fail-closed
def test_restart_success_runs_docker_restart_and_verifies_every_property():
    lab = RestartLab()
    record: dict = {}
    flow.restart_backend_checked(lab, record)
    assert record["completed"] is True and record["barrier_done"] is False
    assert record["docker_restart_code"] == 0 and lab.log == ["docker restart"]            # NAO e no-op
    assert record["before"]["networks"] == record["after"]["networks"] == ["auneron_sim_driver", "auneron_sim_internal"]
    assert record["after"]["started_at"] > record["before"]["started_at"]
    assert [c for c in lab.runner.calls if c[:2] == ["docker", "restart"]] == [["docker", "restart", "auneron-sim-backend"]]


@pytest.mark.parametrize("label,kwargs", [
    ("docker restart falha", {"restart_code": 1}),
    ("StartedAt nao mudou (restart sem efeito)", {"after": "2026-10-07T10:00:00Z"}),
    ("StartedAt retrocedeu", {"after": "2026-10-07T09:00:00Z"}),
    ("backend nao esta rodando", {"running": False}),
    ("nao voltou ready", {"ready": False}),
    ("voltou so na rede interna", {"after_networks": {"auneron_sim_internal": {}}}),
    ("voltou so na rede do Driver", {"after_networks": {"auneron_sim_driver": {}}}),
    ("voltou com rede extra", {"after_networks": {**BOTH, "bridge": {}}}),
    ("ja estava fora da topologia", {"before_networks": {"auneron_sim_internal": {}}}),
    ("SHA do build mudou", {"identity_after": identity(build_git_sha="0" * 40)}),
    ("digest da fonte mudou", {"identity_after": identity(build_source_digest="e" * 64)}),
    ("build ficou dirty", {"identity_after": identity(build_git_dirty="true")}),
    ("identidade invalida apos o restart", {"identity_after": identity(build_identity_state="invalid")}),
    ("estado do floor mudou", {"identity_after": identity(evidence_floor_state="armed")}),
    ("ambiente mudou", {"identity_after": identity(environment="development")}),
    ("sem application_started apos o restart", {"identity_after": None}),
    ("identidade invalida antes do restart", {"identity_before": identity(build_identity_state="invalid")}),
    ("build igual antes e depois, mas fora do commit alvo", {"identity_before": identity(build_git_sha="0" * 40)}),
])
def test_every_restart_failure_is_fail_closed_and_never_completes(label, kwargs):
    lab = RestartLab(**kwargs)
    record: dict = {}
    with pytest.raises(HarnessError):
        flow.restart_backend_checked(lab, record)
    assert record.get("completed") is False, label
    assert not record.get("barrier_done")


def test_the_pre_restart_control_survives_the_record_reset():
    record = {"pre_restart_control": {"exit_code": 3}}
    flow.restart_backend_checked(RestartLab(), record)
    assert record["pre_restart_control"] == {"exit_code": 3}


def test_extraordinary_barrier_only_runs_after_a_completed_restart():
    lab = RestartLab()
    for record in ({}, {"completed": False}):
        with pytest.raises(HarnessError):
            flow.restart_barrier_checked(lab, record)
    assert lab.barrier_calls == []
    record: dict = {}
    flow.restart_backend_checked(lab, record)
    flow.restart_barrier_checked(lab, record)
    assert lab.barrier_calls == [(record["since"], ["e1"])] and record["barrier_done"] is True
    assert lab.log == ["docker restart", "barrier"]                         # barreira DEPOIS do restart


def test_a_failed_barrier_leaves_the_restart_unfinished():
    lab = RestartLab(barrier_error="barreira estourou o teto")
    record: dict = {}
    flow.restart_backend_checked(lab, record)
    with pytest.raises(HarnessError):
        flow.restart_barrier_checked(lab, record)
    assert record["completed"] is True and record["barrier_done"] is False


# ------------------------------------------------------------------------------- hooks: ordem e "done" so com restart + barreira
def cli_reply_factory(log, slots=((90, "11:00"),)):
    def reply(args, stdin):
        text = " ".join(args)
        for key, payload in (("sim.driver.cli slots", [list(s) for s in slots]),
                             ("sim.driver.cli run-slot", {"executed": 1, "harness_ops": [5], "counts": {}}),
                             ("sim.driver.cli export-refs", {"refs": {}, "due_days": {}}),
                             ("sim.collector.cli load-refs", {"refs": 0}),
                             ("sim.collector.cli trigger", {"trigger": "x", "collected": 0, "already_collected": 0}),
                             ("sim.collector.cli mark-barrier", {"completed_barriers": []}),
                             ("sim.driver.cli mark-harness-done", {"seq": 5, "marked": True})):
            if key in text:
                log.append(key.split(".cli ")[1] if ".cli " in key else key)
                if "mark-barrier" in key:
                    log[-1] = f"mark-barrier {args[args.index('--name') + 1]}"
                if "run-slot" in key:
                    log[-1] = "run-slot"
                return Result(0, json.dumps(payload) + "\n", "")
        return Result(0, "{}\n", "")
    return reply


def orchestrated_lab(**kwargs):
    log: list = []
    lab = RestartLab(**kwargs)
    lab.log = log
    lab.runner.log = log
    lab.runner.cli_reply = cli_reply_factory(log)
    return lab, log


def test_orchestrated_order_restart_then_barrier_then_mark_then_harness_done():
    lab, log = orchestrated_lab()
    hooks, state = flow.build_hooks(lab)
    RunOrchestrator(hooks, floor_day=99, last_day=90).run(first_day=90, last_day=90)
    first = {name: log.index(name) for name in ("run-slot", "docker restart", "mark-barrier after_restart_barrier",
                                                "mark-harness-done")}
    barrier_after_restart = next(i for i, x in enumerate(log) if x == "barrier" and i > first["docker restart"])
    assert first["run-slot"] < first["docker restart"] < barrier_after_restart < first["mark-barrier after_restart_barrier"] \
        < first["mark-harness-done"]
    assert state["restart"]["completed"] and state["restart"]["barrier_done"]
    names = [e["event"] for e in state["timeline"]]
    assert names.index("restart_backend:end") < names.index("restart_barrier:start") < names.index("restart_barrier:end")


@pytest.mark.parametrize("kwargs", [{"restart_code": 1}, {"after": "2026-10-07T10:00:00Z"}, {"ready": False},
                                    {"after_networks": {"auneron_sim_internal": {}}},
                                    {"identity_after": identity(build_source_digest="e" * 64)},
                                    {"barrier_error": "barreira estourou o teto"}])
def test_a_failed_restart_or_barrier_never_marks_the_harness_op_done_nor_the_barrier(kwargs):
    lab, log = orchestrated_lab(**kwargs)
    hooks, state = flow.build_hooks(lab)
    with pytest.raises(HarnessError):
        RunOrchestrator(hooks, floor_day=99, last_day=90).run(first_day=90, last_day=90)
    assert "mark-harness-done" not in log
    assert "mark-barrier after_restart_barrier" not in log


def test_mark_done_and_mark_barrier_guards_refuse_without_a_finished_restart():
    lab, log = orchestrated_lab()
    hooks, state = flow.build_hooks(lab)
    with pytest.raises(HarnessError):
        hooks.driver_mark_harness_done(5, "restart_backend")
    with pytest.raises(HarnessError):
        hooks.collector_mark_barrier("after_restart_barrier")
    assert log == []
    hooks.restart_backend()
    with pytest.raises(HarnessError):                                    # restart ok, barreira ainda nao
        hooks.driver_mark_harness_done(5, "restart_backend")
    hooks.restart_barrier()
    hooks.collector_mark_barrier("after_restart_barrier")
    hooks.driver_mark_harness_done(5, "restart_backend")
    assert log[-2:] == ["mark-barrier after_restart_barrier", "mark-harness-done"]
    hooks.driver_mark_harness_done(6, "other_note")                       # outras notas nao sofrem a guarda
    assert log[-1] == "mark-harness-done"
    hooks.collector_mark_barrier("after_floor_barrier")


def test_before_restart_control_runs_before_the_docker_restart_and_is_recorded():
    lab, log = orchestrated_lab()
    seen = []

    def control():
        seen.append(list(log))
        return {"exit_code": 3}

    hooks, state = flow.build_hooks(lab, before_restart=control)
    hooks.restart_backend()
    assert "docker restart" not in seen[0] and state["restart"]["pre_restart_control"] == {"exit_code": 3}


# ------------------------------------------------------------------------------- F-D90-2: safe_at=after_restart_barrier recusado
def d90_plan():
    builder, oracle = S.scen_d90("inst"), S.oracle_d90()
    return compile_plan(oracle, builder.ops(), S.D0, -180, OBSERVER_EMAIL), builder, oracle


def test_plan_validator_rejects_after_restart_barrier_as_a_collection_moment_but_keeps_the_barrier():
    plan, _, _ = d90_plan()
    validate_plan(plan)
    assert "after_restart_barrier" in COLLECTION_TRIGGERS                       # a barreira/gatilho continua existindo
    broken = copy.deepcopy(plan)
    next(i for i in broken["items"] if i["safe_at"] == "after_daily_barrier")["safe_at"] = "after_restart_barrier"
    with pytest.raises(PlanInvalid, match="after_restart_barrier"):
        validate_plan(broken)


def test_compiler_refuses_an_oracle_expectation_with_after_restart_barrier_safe_at():
    _, builder, oracle = d90_plan()
    broken = copy.deepcopy(oracle)
    broken["expectations"][2]["safe_at"] = "after_restart_barrier"
    with pytest.raises(PlanInvalid):
        compile_plan(broken, builder.ops(), S.D0, -180, OBSERVER_EMAIL)


def test_requires_after_restart_barrier_with_a_supported_safe_at_is_accepted_and_enforced(tmp_path):
    plan, _, _ = d90_plan()
    needing = [i for i in plan["items"] if "after_restart_barrier" in i["requires"]]
    assert needing and all(i["safe_at"] == "after_daily_barrier" for i in needing)
    fake = FakeProduct()
    collector, _, _, _ = make_collector(tmp_path, fake, plan)
    (collector.home / "refs.json").write_text(json.dumps({"refs": {}, "due_days": {}}), encoding="utf-8")
    with pytest.raises(CollectorHarnessError, match="exige barreira"):
        collector.trigger("after_daily_barrier", S.RESTART_DAY, "18:00")
    collector.mark_barrier("after_restart_barrier")
    with pytest.raises(CollectorHarnessError, match="refs|conta|ref"):                # passou da guarda; falta so a ref
        collector.trigger("after_daily_barrier", S.RESTART_DAY, "18:00")


# ------------------------------------------------------------------------------- cenario D90
def test_d90_scenario_is_a_valid_agenda_with_the_restart_op_and_all_collection_moments(tmp_path):
    plan, builder, oracle = d90_plan()
    builder.write(tmp_path, 7)
    ops = load_agenda(tmp_path / "public_agenda.json", load_manifest(tmp_path / "driver_manifest.json"))
    restart = [o for o in ops if o["op"] == "restart_backend"]
    assert len(restart) == 1 and (restart[0]["day"], restart[0]["at"], restart[0]["actor"]) == (90, "11:00", "harness")
    by_safe = {}
    for item in plan["items"]:
        by_safe.setdefault(item["safe_at"], []).append(item)
    assert set(by_safe) == {"pre_slot", "post_slot", "after_daily_barrier", "end_of_run"}
    assert {(i["day"], i["at"]) for i in by_safe["pre_slot"] + by_safe["post_slot"]} == {(90, "11:00")}
    assert all(e["safe_at"] != "after_restart_barrier" for e in oracle["expectations"])
    approve = next(o for o in ops if o["op"] == "decide_mark_paid")
    execute = next(o for o in ops if o["op"] == "execute_mark_paid")
    assert (approve["at"], restart[0]["at"], execute["at"]) == ("10:30", "11:00", "12:00")      # aprova antes, executa depois
    created_after = [o for o in ops if o["op"] == "create_receivable" and o["at"] > "11:00"]
    assert len(created_after) == 1


def test_d90_days_order_after_the_other_gates_in_the_same_clock():
    assert S.DAYS["d90"] == S.RESTART_DAY == 90 > max(v for k, v in S.DAYS.items() if k != "d90")


def test_d90_scenario_runs_through_driver_collector_and_evaluator_without_an_instrument_failure(tmp_path):
    plan, builder, oracle = d90_plan()
    info = builder.write(tmp_path / "in", 7)
    fake = FakeProduct()
    driver, fake, store, _, _ = make_driver(tmp_path, fake, agenda_path=tmp_path / "in" / "public_agenda.json")
    collector, _, chain, _ = make_collector(tmp_path, fake, plan)
    from sim.driver.runner import virtual_utc

    def export():
        (collector.home / "refs.json").write_text(json.dumps({"refs": store.all_refs(), "due_days": store.all_due()}),
                                                  encoding="utf-8")

    for day, at in driver.slot_keys():
        fake.now = virtual_utc(driver.d0, day, at, driver.offset)
        export()
        collector.trigger("pre_slot", day, at)
        result = driver.run_slot(day, at)
        for seq in result["harness_ops"]:
            collector.mark_barrier("after_restart_barrier")
            driver.mark_harness_done(seq, "restart_backend")
        export()
        collector.trigger("post_slot", day, at)
    fake.now = virtual_utc(driver.d0, 90, "18:00", driver.offset)
    export()
    collector.trigger("after_daily_barrier", 90, "18:00")
    collector.trigger("end_of_run", 90, "23:59")
    from sim.collector.chainlog import ChainLog
    from sim.collector.collect import missing_items
    from sim.collector.collect import verify_records

    records = ChainLog.read(chain.path)
    lines = [json.loads(l) for l in (tmp_path / "state" / "driver_evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    evaluation = evaluate(oracle, plan, records, lines, {"refs": store.all_refs(), "due_days": store.all_due()}, {})
    assert missing_items(plan, records) == [] and verify_records(plan, records) == []
    assert evaluation["summary"].get("HARNESS_ERROR", 0) == 0
    assert set(store.counts()) <= {"done", "reconciled"} and len(info["ops"]) == sum(store.counts().values())


# ------------------------------------------------------------------------------- juiz do D90
def good_report() -> dict:
    t0 = 1000.0
    return {
        "plan_safe_at": ["after_daily_barrier", "end_of_run", "post_slot", "pre_slot"],
        "restart": {"completed": True, "barrier_done": True, "docker_restart_code": 0, "since": t0,
                    "before": {"networks": J.FROZEN_NETWORKS, "started_at": "2026-10-07T10:00:00Z"},
                    "after": {"networks": J.FROZEN_NETWORKS, "started_at": "2026-10-07T10:05:00Z"}, "ready": {"ready": True},
                    "identity_before": identity(), "identity_after": identity(),
                    "pre_restart_control": {"exit_code": 3, "payload": {"harness_error": "I-1 exige barreira(s) [...]"},
                                            "collected_before": 4, "collected_after": 4}},
        "times": {"restart_end": t0 + 6, "barrier_begin": t0 + 6, "barrier_end": t0 + 180, "mark_barrier": t0 + 181,
                  "mark_done": t0 + 182},
        "restart_slot": {"day": 90, "at": "11:00"},
        "records": [
            {"item_id": "I-1", "trigger": "pre_slot", "safe_at": "pre_slot", "day": 90, "at": "11:00", "requires": [], "t_real": t0 - 2},
            {"item_id": "I-2", "trigger": "post_slot", "safe_at": "post_slot", "day": 90, "at": "11:00", "requires": [], "t_real": t0 + 190},
            {"item_id": "I-3", "trigger": "after_daily_barrier", "safe_at": "after_daily_barrier", "day": 90, "at": None,
             "requires": ["after_restart_barrier"], "t_real": t0 + 900}],
        "missing": [], "problems": [], "retrigger": {"collected": 0, "already_collected": 1, "expected": 1},
        "driver": {"in_flight": [], "counts": {"done": 12, "reconciled": 0}, "execute_statuses": [200]},
        "evaluation": {"summary": {"CORRECT": 13}, "run_valid": True, "instrument_failures": 0},
    }


def test_d90_judge_accepts_the_good_report_and_refuses_each_regression():
    assert J.judge_d90(good_report()) == []
    mutations = {
        "restart no-op (nao concluiu)": lambda d: d["restart"].__setitem__("completed", False),
        "restart sem StartedAt novo": lambda d: d["restart"]["after"].__setitem__("started_at", "2026-10-07T10:00:00Z"),
        "docker restart rc != 0": lambda d: d["restart"].__setitem__("docker_restart_code", 1),
        "nao voltou ready": lambda d: d["restart"].__setitem__("ready", {"ready": False}),
        "topologia errada depois": lambda d: d["restart"]["after"].__setitem__("networks", ["auneron_sim_internal"]),
        "topologia errada antes": lambda d: d["restart"]["before"].__setitem__("networks", ["auneron_sim_internal"]),
        "identidade mudou": lambda d: d["restart"]["identity_after"].__setitem__("build_source_digest", "x"),
        "identidade invalida": lambda d: [d["restart"]["identity_after"].__setitem__("build_identity_state", "invalid"),
                                          d["restart"]["identity_before"].__setitem__("build_identity_state", "invalid")],
        "barreira antes de o restart terminar": lambda d: d["times"].__setitem__("barrier_begin", d["times"]["restart_end"] - 1),
        "barreira nao executada": lambda d: d["times"].__setitem__("barrier_end", None),
        "barreira nao concluida": lambda d: d["restart"].__setitem__("barrier_done", False),
        "harness-done antes da barreira": lambda d: d["times"].__setitem__("mark_done", d["times"]["barrier_end"] - 1),
        "marca da barreira antes do fim dela": lambda d: d["times"].__setitem__("mark_barrier", d["times"]["barrier_end"] - 1),
        "coleta antecipada aceita": lambda d: d["restart"]["pre_restart_control"].__setitem__("exit_code", 0),
        "coleta antecipada alterou o estado": lambda d: d["restart"]["pre_restart_control"].__setitem__("collected_after", 9),
        "item requires coletado antes da barreira": lambda d: d["records"][2].__setitem__("t_real", d["times"]["barrier_end"] - 1),
        "nenhum item requires": lambda d: d["records"][2].__setitem__("requires", []),
        "post_slot antes da barreira": lambda d: d["records"][1].__setitem__("t_real", d["times"]["barrier_end"] - 1),
        "pre_slot depois do restart": lambda d: d["records"][0].__setitem__("t_real", d["restart"]["since"] + 1),
        "item faltando": lambda d: d.__setitem__("missing", ["I-9"]),
        "coleta fora de ponto": lambda d: d.__setitem__("problems", ["I-3 fora do ponto"]),
        "coleta duplicada": lambda d: d["records"].append(dict(d["records"][2])),
        "re-disparo coleta de novo": lambda d: d["retrigger"].__setitem__("collected", 3),
        "driver com op em voo": lambda d: d["driver"].__setitem__("in_flight", [4]),
        "driver com op falha": lambda d: d["driver"]["counts"].__setitem__("failed", 1),
        "execucao pos-restart falhou": lambda d: d["driver"].__setitem__("execute_statuses", [409]),
        "execucao pos-restart ausente": lambda d: d["driver"].__setitem__("execute_statuses", []),
        "avaliacao com divergencia": lambda d: d["evaluation"]["summary"].__setitem__("DIVERGENCE", 1),
        "falha de instrumento": lambda d: d["evaluation"].__setitem__("instrument_failures", 1),
        "plano com safe_at=after_restart_barrier": lambda d: d["plan_safe_at"].append("after_restart_barrier"),
    }
    for label, mutate in mutations.items():
        broken = copy.deepcopy(good_report())
        mutate(broken)
        assert J.judge_d90(broken), label


def test_d90_report_builder_feeds_the_judge_from_a_run():
    plan, _, _ = d90_plan()
    items = [i for i in plan["items"] if i.get("channel", "http") == "http"]
    t0 = 1000.0
    timeline = [{"t": t0 + 5, "event": "restart_backend:end"}, {"t": t0 + 6, "event": "restart_barrier:start", "args": []},
                {"t": t0 + 100, "event": "restart_barrier:end"},
                {"t": t0 + 101, "event": "collector_mark_barrier:start", "args": ["after_floor_barrier"]},
                {"t": t0 + 102, "event": "collector_mark_barrier:start", "args": ["after_restart_barrier"]},
                {"t": t0 + 103, "event": "driver_mark_harness_done:start", "args": ["5", "restart_backend"]}]
    run = {"state": {"timeline": timeline, "restart": {"completed": True}}, "missing": [], "problems": [],
           "driver_lines": [{"kind": "http", "op": "execute_mark_paid", "status": 200}, {"kind": "op_result", "op": "x"}],
           "collected": [{"item_id": items[0]["item_id"], "trigger": {"kind": items[0]["safe_at"]}, "t_real": t0 + 1}],
           "evaluation": {"summary": {"CORRECT": 1}, "run_valid": True}, "distinction": {"instrument_failures": 0}}
    report = J.build_d90_report(plan=plan, run=run, second_trigger={"collected": 0, "already_collected": 5}, driver_status={
        "in_flight": [], "counts": {"done": 1}}, day=90, at="11:00")
    assert report["times"] == {"restart_end": t0 + 5, "barrier_begin": t0 + 6, "barrier_end": t0 + 100,
                               "mark_barrier": t0 + 102, "mark_done": t0 + 103}
    assert report["driver"]["execute_statuses"] == [200] and "after_restart_barrier" not in report["plan_safe_at"]
    assert report["retrigger"]["expected"] == len([i for i in items if i["safe_at"] == "after_daily_barrier"])


def test_cli_exposes_the_d90_phase_and_the_phase_uses_the_versioned_path_only():
    from sim.live import cli
    from sim.live import gates

    assert "d90" in cli.PHASES and callable(cli.PHASES["d90"])
    source = open(gates.__file__, encoding="utf-8").read()
    body = source[source.index("def phase_d90"):source.index("def phase_reset")]
    assert "flow.run_pipeline(" in body and "before_restart=early_control" in body and "scratchpad" not in body
