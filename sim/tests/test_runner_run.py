"""
SIM-1.5E -- laco do run: reuso do `RunOrchestrator` (D14 floor, D90 restart), R0 fail-closed, classificacao de falhas,
abort com preservacao, preflight, interlock no laco, guardas diarias e de disco.
"""

from __future__ import annotations

import json

import pytest

from sim.runner import package
from sim.runner import preflight
from sim.runner import runner as runner_mod
from sim.runner.declare import GIB
from sim.runner.errors import InterlockError
from sim.runner.errors import PreflightFailed
from sim.runner.errors import ResumeRefused
from sim.runner.host import RunLock
from sim.runner.host import StopFlag
from sim.runner.ledger import RunLedger
from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.quiescence import HarnessError as BarrierError
from sim.tests.runner_support import Recorder
from sim.tests.runner_support import events
from sim.tests.runner_support import good_day_facts
from sim.tests.runner_support import make_runner
from sim.tests.runner_support import mutated
from sim.tests.runner_support import services_for
from sim.tests.runner_support import setup_run

SLOTS = [(0, "09:00"), (1, "09:00"), (2, "11:00"), (3, "10:00")]


def run_it(tmp_path, **kwargs):
    run = setup_run(tmp_path, **kwargs.pop("setup", {}))
    services = services_for(run, slots=SLOTS, harness_at=(2, "11:00"), **kwargs)
    return run, make_runner(run, services)


# ------------------------------------------------------------------------------------------------- caminho feliz
def test_full_run_completes_with_floor_and_restart_through_the_existing_orchestrator(tmp_path):
    run, nh = run_it(tmp_path)
    result = nh.run()
    assert result["state"] == "COMPLETED" and result["digest"]
    log = run.recorder.log
    # D14 (aqui D1): UMA ativacao do floor, ANTES do primeiro slot do dia, e a marca da barreira do floor
    assert log.count(("floor",)) == 1
    assert log.index(("floor",)) < log.index(("driver", 1, "09:00")) and log.index(("mark", "after_floor_barrier")) > log.index(("floor",))
    # D90 (aqui D2): restart -> barreira extraordinaria -> marca -> harness-done, nessa ordem, uma vez
    order = [("driver", 2, "11:00"), ("restart_backend",), ("restart_barrier",), ("mark", "after_restart_barrier"), ("harness_done", 99)]
    positions = [log.index(item) for item in order]
    assert positions == sorted(positions) and log.count(("restart_backend",)) == 1
    # end_of_run so no ultimo dia
    assert [i for i in log if i[:2] == ("collect", "end_of_run")] == [("collect", "end_of_run", 3, "23:59")]
    # ledger
    names = events(run)
    assert names[:7] == ["OPENED", "RIM_VERIFIED", "PREFLIGHT_PASSED", "D0_AUTHORIZED", "RUN_STARTED", "PROBE_DR1", "DAY_STARTED"]
    assert names.count("DAY_STARTED") == names.count("DAY_CLOSED") == 4
    assert names.count("FLOOR_ARMED") == 1 and names.count("RESTART_DONE") == 1
    assert names[-3:] == ["RUN_FINALIZING", "PACKAGE_SEALED", "COMPLETED"]
    assert RunLedger(run.run_dir).verify()["warning"] is None
    assert package.verify(run.run_dir, expected_digest=result["digest"])["ok"]
    assert run.registry.state_of(run.run_id) == "COMPLETED"


def test_each_day_runs_the_orchestrator_for_that_single_day_in_order(tmp_path):
    run, nh = run_it(tmp_path)
    nh.run()
    days = [item[1] for item in run.recorder.log if item[0] == "barrier"]
    assert days == [0, 1, 2, 3]
    clock_days = [item[1] for item in run.recorder.log if item[0] == "clock"]
    assert clock_days == sorted(clock_days)                                   # o relogio so avanca


def test_dr1_probes_run_before_d0_and_after_the_floor_restart_and_last_days(tmp_path):
    run, nh = run_it(tmp_path)
    nh.run()
    assert run.calls["probe"] == [(0, "pre_d0"), (1, "post_day"), (2, "post_day"), (3, "post_day")]


def test_day_records_are_chained_and_the_package_contains_them(tmp_path):
    from sim.runner import daily

    run, nh = run_it(tmp_path)
    nh.run()
    records = [json.loads((run.run_dir / "days" / f"D{d:03d}.json").read_bytes()) for d in range(4)]
    daily.verify_day_chain(records)
    manifest = json.loads((run.run_dir / package.MANIFEST_NAME).read_bytes())
    assert {f"days/D{d:03d}.json" for d in range(4)} <= {f["path"] for f in manifest["files"]}
    records[1]["facts"]["time"]["o1_ms"] = 1
    with pytest.raises(daily.DayRecordError):
        daily.verify_day_chain(records)


# ------------------------------------------------------------------------------------------------- interlock no laco
def test_without_the_d0_authorization_nothing_runs_and_no_hook_is_called(tmp_path):
    run = setup_run(tmp_path, token=False)
    services = services_for(run, slots=SLOTS)
    nh = make_runner(run, services)
    with pytest.raises(InterlockError):
        nh.run()
    assert run.recorder.log == [] and RunLedger(run.run_dir).state == "PREFLIGHTED"
    assert not (run.run_dir / RunLock(run.run_dir).path.name).exists()          # trava liberada


def test_a_real_run_cannot_start_even_with_every_other_condition_met(tmp_path):
    from types import SimpleNamespace

    from sim.runner.registry import Registry
    from sim.tests.runner_support import good_preflight_facts
    from sim.tests.runner_support import make_rim

    rim = make_rim("RUN", growth="9" * 64)
    registry = Registry(tmp_path / "runs")
    info, ledger = runner_mod.open_new_run(registry, rim, purpose="FIRST")
    runner_mod.run_preflight(ledger, registry, rim, good_preflight_facts(rim), projected_growth_bytes=GIB)
    assert ledger.state == "PREFLIGHTED"
    run = SimpleNamespace(rim=rim, registry=registry, info=info, ledger=ledger, run_dir=info["run_dir"], run_id=info["run_id"],
                          recorder=None, alive=lambda pid: False)
    nh = make_runner(run, services_for(run, slots=SLOTS))
    with pytest.raises(InterlockError, match="ausente"):
        nh.run()                                                              # nenhum token de D0 existe nem e emitido por codigo
    assert run.recorder.log == [] and RunLedger(info["run_dir"]).state == "PREFLIGHTED"


# ------------------------------------------------------------------------------------------------- R0 e saidas sujas
def test_r0_a_crashed_runner_is_sealed_as_unclean_exit_and_never_resumed(tmp_path):
    run = setup_run(tmp_path)
    services = services_for(run, slots=SLOTS, fail={"barrier": KeyboardInterrupt()})        # BaseException: nao e capturada
    dead = RunLock(run.run_dir, pid=lambda: 4242, alive=run.alive)
    dead.release = lambda: None                                               # o processo morreu: o `finally` nunca roda
    with pytest.raises(KeyboardInterrupt):
        make_runner(run, services, lock=dead).run()
    assert RunLedger(run.run_dir).state == "RUNNING" and (run.run_dir / "RUN.LOCK").exists()   # morreu no meio
    log_before = list(run.recorder.log)
    # novo processo: lock orfao (pid morto)
    again = make_runner(run, services_for(run, slots=SLOTS))
    again.ledger = RunLedger(run.run_dir)
    with pytest.raises(ResumeRefused, match="sem resume"):
        again.run()
    final = RunLedger(run.run_dir)
    assert final.state == "ABORTED" and final.events()[-1]["payload"]["class"] == "UNCLEAN_EXIT"
    assert run.registry.state_of(run.run_id) == "ABORTED"
    assert run.recorder.log == log_before                                    # nenhum hook foi chamado de novo
    assert not (run.run_dir / "RUN.LOCK").exists()
    run.ledger = RunLedger(run.run_dir)
    with pytest.raises(ResumeRefused, match="em estado ABORTED"):
        make_runner(run, services_for(run, slots=SLOTS)).run()


def test_a_live_runner_holding_the_lock_is_never_sealed_or_displaced(tmp_path):
    run = setup_run(tmp_path, alive=lambda pid: True)
    services = services_for(run, slots=SLOTS, fail={"barrier": KeyboardInterrupt()})
    alive_lock = RunLock(run.run_dir, pid=lambda: 4242, alive=run.alive)
    alive_lock.release = lambda: None
    with pytest.raises(KeyboardInterrupt):
        make_runner(run, services, lock=alive_lock).run()
    with pytest.raises(ResumeRefused, match="runner vivo"):
        make_runner(run, services_for(run, slots=SLOTS)).run()
    assert RunLedger(run.run_dir).state == "RUNNING"


def test_a_run_not_yet_preflighted_is_refused_without_being_sealed(tmp_path):
    from types import SimpleNamespace

    from sim.runner.registry import Registry
    from sim.tests.runner_support import make_rim

    rim = make_rim()
    registry = Registry(tmp_path / "runs")
    info, ledger = runner_mod.open_new_run(registry, rim, purpose="FIRST")
    run = SimpleNamespace(rim=rim, registry=registry, ledger=ledger, run_dir=info["run_dir"], recorder=None, alive=lambda p: False)
    with pytest.raises(ResumeRefused, match="em estado CREATED"):
        make_runner(run, services_for(run, slots=SLOTS)).run()
    assert RunLedger(info["run_dir"]).state == "CREATED"


# ------------------------------------------------------------------------------------------------- falhas -> estado terminal
@pytest.mark.parametrize("hook,exc,klass,state", [
    ("clock", GuardViolation("retrocesso"), "CLOCK_VIOLATION", "INVALID"),
    ("barrier", BarrierError("teto"), "BARRIER_FAILURE", "INVALID"),
    ("floor", RuntimeError("falhou"), "FLOOR_FAILURE", "INVALID"),
    ("restart_backend", RuntimeError("falhou"), "RESTART_FAILURE", "INVALID"),
    ("restart_barrier", BarrierError("sem estabilidade"), "RESTART_FAILURE", "INVALID"),
    ("driver", BarrierError("exit 3"), "HARNESS_ERROR", "INVALID"),
    ("collect", ValueError("inesperado"), "UNCLASSIFIED_EXCEPTION", "INVALID"),
])
def test_hook_failures_are_classified_preserved_and_end_in_the_right_terminal_state(tmp_path, hook, exc, klass, state):
    run = setup_run(tmp_path)
    services = services_for(run, slots=SLOTS, harness_at=(2, "11:00"), fail={hook: exc})
    result = make_runner(run, services).run()
    assert (result["state"], result["class"]) == (state, klass)
    assert run.calls["preserve"] == [klass]                                   # evidencia preservada ANTES do terminal
    names = events(run)
    assert names[-3:] == ["ABORT_INTENT", state, "PARTIAL_PACKAGE_SEALED"]
    assert run.registry.state_of(run.run_id) == state
    assert package.verify(run.run_dir)["ok"] and json.loads((run.run_dir / package.MANIFEST_NAME).read_bytes())["partial"] is True
    with pytest.raises(ResumeRefused):
        make_runner(run, services_for(run, slots=SLOTS)).run()


def test_operator_stop_aborts_at_the_next_safe_point_and_preserves_evidence(tmp_path):
    run = setup_run(tmp_path)
    stop = StopFlag()
    calls = {"n": 0}

    def facts(day):
        calls["n"] += 1
        if calls["n"] == 2:
            stop.request("test")
        return good_day_facts()

    services = services_for(run, slots=SLOTS, day_facts=facts)
    result = make_runner(run, services, stop=stop).run()
    assert (result["state"], result["class"]) == ("ABORTED", "OPERATOR_STOP")
    assert RunLedger(run.run_dir).replay.last_closed == 1                     # os dias 0 e 1 fecharam; nada alem


def test_a_failing_preservation_is_recorded_not_hidden(tmp_path):
    run = setup_run(tmp_path)

    def broken(cls):
        raise OSError("disco")

    services = services_for(run, slots=SLOTS, harness_at=(2, "11:00"), fail={"driver": BarrierError("x")}, preserve=broken)
    make_runner(run, services).run()
    terminal = [r for r in RunLedger(run.run_dir).events() if r["event"] == "INVALID"][0]
    assert "OSError: disco" in terminal["payload"]["preserved"]["preservation_error"]


def test_a_clock_call_for_another_day_is_a_harness_error(tmp_path):
    run = setup_run(tmp_path)

    class Skipping:                                  # um orquestrador defeituoso que tenta pular para o dia seguinte
        def __init__(self, hooks):
            self.hooks = hooks

        def run(self, first_day, last_day):
            self.hooks.clock_set(first_day + 1, "09:00")

    services = services_for(run, slots=SLOTS)
    services.orchestrator_factory = Skipping
    result = make_runner(run, services).run()
    assert (result["state"], result["class"]) == ("INVALID", "HARNESS_ERROR") and run.recorder.log == []


# ------------------------------------------------------------------------------------------------- guardas diarias
def facts_for(path, value):
    if path.startswith("balance"):
        facts = good_day_facts()
        facts["balance"]["accounts"] = value
        return facts
    return good_day_facts(**{path: value})


@pytest.mark.parametrize("path,value,klass", [
    ("time__barrier_total_ms", 700_000, "BARRIER_FAILURE"),
    ("time__barrier_events_ok", False, "BARRIER_FAILURE"),
    ("time__clock_skew_ms", 5000, "CLOCK_VIOLATION"),
    ("time__o1_ms", 1500, "O1_THRESHOLD_REVIEW_REQUIRED"),
    ("driver__in_flight", 1, "HARNESS_ERROR"),
    ("driver__counts", {"done": 0, "failed": 1}, "HARNESS_ERROR"),
    ("driver__agenda_ops_through_day", 5, "MASS_BALANCE_MISMATCH"),
    ("collector__chain_ok", False, "HARNESS_ERROR"),
    ("collector__missing", 1, "HARNESS_ERROR"),
    ("balance__accounts", [3, 4], "MASS_BALANCE_MISMATCH"),
    ("infra__containers_ok", False, "INFRA_FAILURE"),
    ("infra__unexpected_restarts", ["backend"], "INFRA_FAILURE"),
    ("infra__topology_ok", False, "TOPOLOGY_VIOLATION"),
    ("infra__run_id_ok", False, "IDENTITY_DRIFT"),
    ("authority__tripwire", [{"violation": "SEGREGATION_BREACH"}], "AUTHORITY_OUTSIDE_C_AUTH_2"),
    ("disk__free_bytes", 1 * GIB, "DISK_LOW"),
])
def test_every_daily_check_ends_the_run_with_its_own_class_and_the_day_record_is_kept(tmp_path, path, value, klass):
    run = setup_run(tmp_path)
    services = services_for(run, slots=SLOTS, day_facts=lambda d: facts_for(path, value))
    result = make_runner(run, services).run()
    assert result["class"] == klass
    expected_state = "ABORTED" if klass in ("DISK_LOW", "INFRA_FAILURE", "O1_THRESHOLD_REVIEW_REQUIRED") else "INVALID"
    assert result["state"] == expected_state
    record = json.loads((run.run_dir / "days" / "D000.json").read_bytes())
    assert record["violations"] and RunLedger(run.run_dir).replay.last_closed == -1     # o dia violado NAO fecha


def test_missing_daily_facts_are_a_failure_not_a_pass(tmp_path):
    run = setup_run(tmp_path)

    def incomplete(day):
        facts = good_day_facts()
        del facts["infra"]
        return facts

    result = make_runner(run, services_for(run, slots=SLOTS, day_facts=incomplete)).run()
    assert result["class"] == "HARNESS_ERROR"


def test_disk_warning_is_logged_but_does_not_stop_the_run(tmp_path):
    run = setup_run(tmp_path)
    services = services_for(run, slots=SLOTS, day_facts=lambda d: good_day_facts(disk__free_bytes=8 * GIB))
    assert make_runner(run, services).run()["state"] == "COMPLETED"
    assert events(run).count("DISK_WARNING") == 4


def test_probe_that_is_not_clean_invalidates_with_the_isolation_class(tmp_path):
    run = setup_run(tmp_path)
    services = services_for(run, slots=SLOTS, probe=lambda day, when: {"clean": False})
    result = make_runner(run, services).run()
    assert (result["state"], result["class"]) == ("INVALID", "ORACLE_ISOLATION_VIOLATION")
    assert run.recorder.log == []                                            # antes de qualquer mutacao do D0


def test_finalize_violations_invalidate_the_run_before_completion(tmp_path):
    run = setup_run(tmp_path)
    services = services_for(run, slots=SLOTS, finalize=lambda: {"violations": [{"class": "UNEXPECTED_STATUS_UNEXPLAINED",
                                                                                 "detail": "3 status"}]})
    result = make_runner(run, services).run()
    assert (result["state"], result["class"]) == ("INVALID", "UNEXPECTED_STATUS_UNEXPLAINED")
    assert "COMPLETED" not in events(run)


def test_awake_failure_before_d0_refuses_to_start_without_mutating(tmp_path):
    run = setup_run(tmp_path)
    nh = make_runner(run, services_for(run, slots=SLOTS, awake=lambda: False))
    with pytest.raises(PreflightFailed, match="acordada"):
        nh.run()
    assert RunLedger(run.run_dir).state == "PREFLIGHTED" and run.recorder.log == []


# ------------------------------------------------------------------------------------------------- preflight
def test_preflight_passes_with_good_facts_and_ends_in_preflighted(tmp_path):
    run = setup_run(tmp_path)
    assert run.ledger.state == "PREFLIGHTED"
    assert events(run) == ["OPENED", "RIM_VERIFIED", "PREFLIGHT_PASSED"]


@pytest.mark.parametrize("path", [
    "P1.head", "P1.tree_clean", "P1.index_empty", "P1.static_tests_ok", "P2.backend_tree", "P3.oracle_sha256",
    "P3.reproduced_in_memory", "P4.residues", "P4.volumes_new", "P5.base_digest", "P5.build_identity_state",
    "P5.build_dirty", "P6.compose_guard_ok", "P6.backend_networks", "P6.collector_networks", "P7.business_rows",
    "P8.bindings", "P8.version_from_created_output", "P9.app_env", "P9.worker_enabled", "P9.floor_state", "P10.virtual_now_ms",
    "P11.dr1_clean", "P11.plan_sha256", "P12.run_id_file_ok", "P12.free_bytes", "P13.awake_acquired", "P13.foreign_docker_activity",
])
def test_each_preflight_property_failing_aborts_the_run_with_zero_business_cost(tmp_path, path):
    bad = {"P1.head": "x", "P1.tree_clean": False, "P1.index_empty": False, "P1.static_tests_ok": False,
           "P2.backend_tree": "x", "P3.oracle_sha256": "0" * 64, "P3.reproduced_in_memory": False, "P4.residues": 2,
           "P4.volumes_new": False, "P5.base_digest": "outro", "P5.build_identity_state": "invalid", "P5.build_dirty": True,
           "P6.compose_guard_ok": False, "P6.backend_networks": ["auneron_sim_internal"], "P6.collector_networks": ["x"],
           "P7.business_rows": 3, "P8.bindings": 1, "P8.version_from_created_output": False, "P9.app_env": "development",
           "P9.worker_enabled": True, "P9.floor_state": "armed", "P10.virtual_now_ms": 3_500_000, "P11.dr1_clean": False,
           "P11.plan_sha256": "0" * 64, "P12.run_id_file_ok": False, "P12.free_bytes": 1 * GIB, "P13.awake_acquired": False,
           "P13.foreign_docker_activity": 1}[path]
    check, key = path.split(".")
    with pytest.raises(PreflightFailed):
        setup_run(tmp_path, facts_mutator=lambda facts: facts[check].__setitem__(key, bad))
    from sim.runner.registry import Registry

    registry = Registry(tmp_path / "runs")
    (run_id, run), = registry.runs().items()
    assert run["terminal"]["state"] == "ABORTED" and run["terminal"]["class"] == "PREFLIGHT_FAILED"


def test_preflight_with_missing_facts_fails_closed(tmp_path):
    with pytest.raises(PreflightFailed, match="fatos ausentes"):
        setup_run(tmp_path, facts_mutator=lambda facts: facts.pop("P7"))


def test_a_run_preflight_without_a_growth_model_is_refused_by_the_disk_guard(tmp_path):
    from sim.runner import disk

    policy = setup_run(tmp_path).rim["declared"]["disk_policy"]
    assert disk.judge_preflight(100 * GIB, policy, None, "RUN").level == "FAIL"
    assert disk.judge_preflight(100 * GIB, policy, None, "REHEARSAL").ok
    assert disk.judge_preflight(20 * GIB, policy, 20 * GIB, "RUN").level == "FAIL"        # 1,5 x 20 + 8 > 20
    assert disk.judge_preflight(60 * GIB, policy, 20 * GIB, "RUN").ok
    assert preflight.REQUIRED["P12"]


def test_mutated_helper_returns_independent_copies():
    base = {"a": {"b": 1}}
    assert mutated(base, "a.b", 2) == {"a": {"b": 2}} and base == {"a": {"b": 1}}


def test_a_package_that_fails_verification_right_after_sealing_invalidates_the_run(tmp_path, monkeypatch):
    run = setup_run(tmp_path)
    monkeypatch.setattr(package, "verify", lambda *a, **k: {"ok": False, "problems": ["adulterado"], "digest": None})
    result = make_runner(run, services_for(run, slots=SLOTS)).run()
    assert (result["state"], result["class"]) == ("INVALID", "PACKAGE_VERIFY_FAILED")
    assert "COMPLETED" not in events(run)
