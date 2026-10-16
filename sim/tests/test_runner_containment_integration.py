"""
SIM-1.5G.4 -- integracao da contencao: `kill_task`, `reconcile`, P15, sonda (`collect2`) com vigia, monitor/juiz com processos do run,
NH (`start_containment`, `run_and_record_exit`) e CLI (`recover`, `sched-reconcile`). Cenario central: o Scheduler encerra SO o `cmd`
(achado F-1 do L1-C) e o Python segue vivo. TUDO com duplos; os unicos processos reais sao filhos do proprio teste.
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from sim.runner import cli
from sim.runner import containment
from sim.runner import detach
from sim.runner import detach_probe
from sim.runner import nh_phases
from sim.runner import preflight
from sim.runner import sched
from sim.runner import termination
from sim.runner import watch
from sim.runner.errors import RunnerError
from sim.tests.sched_support import FakeHost
from sim.tests.sched_support import FakeOS
from sim.tests.test_runner_containment import MARKER
from sim.tests.test_runner_sched import RUN
from sim.tests.test_runner_sched import Clock
from sim.tests.test_runner_sched import codes
from sim.tests.test_runner_sched import good_facts
from sim.tests.test_runner_sched import good_monitor
from sim.tests.test_runner_sched import launch
from sim.tests.test_runner_sched import probe_ports
from sim.tests.test_runner_sched import probe_proc
from sim.tests.test_runner_sched import probe_world
from sim.tests.test_runner_sched import reconcile
from sim.tests.test_runner_sched import run_monitor_and_finish
from sim.tests.test_runner_sched import started

T = termination.TASK_TERMINATED
ALIVE2 = {"alive": 2, "unknown": 0, "suspects": 0}
NONE = {"alive": 0, "unknown": 0, "suspects": 0}


# ------------------------------------------------------------------------------------------------------ reconcile (F-2/G4-07)
def c2_like(tmp_path, *, last=T, exit_json=False):
    """O limite do Scheduler encerrou so o cmd: tarefa Ready, motor/heartbeat ausentes, SEM exit.json."""
    host, proc, audit = started(tmp_path)
    host.alive_pids.clear()
    host.tasks[RUN].update(state=sched.STATE_READY, instances=[], last=last)
    if exit_json:
        (proc / detach.EXIT).write_text("{}")
    return host, proc, audit


def test_python_alive_after_the_engine_died_is_an_orphan_and_nothing_is_removed(tmp_path):
    host, proc, audit = c2_like(tmp_path)
    report = reconcile(tmp_path, host, proc, None, owned=dict(ALIVE2), apply=True)
    assert {"ORPHAN_RUNNING", "UNEXPECTED_EXIT", "REMOVAL_BLOCKED"} <= set(codes(report)) and report["actions"] == [] and RUN in host.tasks
    assert "ORPHAN_TASK" not in codes(report)                              # com processos vivos o run NAO esta 'terminado', so a tarefa esta orfa
    assert report["process_alive"] is True and report["ok"] is False
    assert next(f for f in report["findings"] if f["code"] == "ORPHAN_RUNNING")["action"] == "recover"


def test_an_exit_json_does_not_make_removal_safe_while_owned_processes_live(tmp_path):
    host, proc, audit = c2_like(tmp_path, exit_json=True)
    report = reconcile(tmp_path, host, proc, None, owned=dict(ALIVE2), apply=True)
    assert report["actions"] == [] and RUN in host.tasks and "ORPHAN_RUNNING" in codes(report)


def test_a_scheduler_end_without_exit_json_is_removable_only_with_zero_owned_alive_and_no_unknown(tmp_path):
    host, proc, audit = c2_like(tmp_path)
    report = reconcile(tmp_path, host, proc, None, owned=dict(NONE), apply=True)                 # F-2: 0x41306 + 0 vivos => remove
    assert report["actions"][0]["removed"] is True and RUN not in host.tasks
    for owned, label in (({"alive": 0, "unknown": 1, "suspects": 0}, "unknown"), ({"alive": 0, "unknown": 0, "suspects": 1}, "p3"), (None, "nao medido")):
        host2, proc2, _ = c2_like(tmp_path / label)
        blocked = reconcile(tmp_path / label, host2, proc2, None, owned=owned, apply=True)
        assert blocked["actions"] == [] and RUN in host2.tasks, label
        if owned and (owned["unknown"] or owned["suspects"]):
            assert "IDENTITY_UNKNOWN" in codes(blocked)
        assert "ORPHAN_TASK" not in codes(blocked), label                  # sem prova positiva (ou com UNKNOWN) o run nao e dado como terminado


def test_without_a_positive_scheduler_end_a_missing_exit_json_is_never_inferred_as_finished(tmp_path):
    host, proc, audit = c2_like(tmp_path, last=0)                                                  # Ready/0, sem exit.json, sem FORCED_END
    report = reconcile(tmp_path, host, proc, None, owned=dict(NONE), apply=True)
    assert report["actions"] == [] and RUN in host.tasks and "UNEXPECTED_EXIT" in codes(report)
    audit.append("FORCED_END", {"by": "operator"})                                                  # agora ha evidencia positiva propria
    host2, proc2, audit2 = c2_like(tmp_path / "f", last=1)
    audit2.append("FORCED_END", {"by": "operator"})
    forced = reconcile(tmp_path / "f", host2, proc2, None, owned=dict(NONE), apply=True)
    assert forced["actions"][0]["removed"] is True


def test_a_task_that_disappeared_with_owned_processes_alive_is_supervision_lost(tmp_path):
    host, proc, audit = c2_like(tmp_path)
    host.tasks.pop(RUN)
    report = reconcile(tmp_path, host, proc, None, owned=dict(ALIVE2))
    assert "SUPERVISION_LOST" in codes(report) and report["process_alive"] is True and report["ok"] is False
    quiet = reconcile(tmp_path, host, proc, None, owned=dict(NONE))
    assert "SUPERVISION_LOST" not in codes(quiet)


def test_reconcile_without_the_new_input_keeps_its_previous_behaviour(tmp_path):
    host, proc, audit = started(tmp_path)
    from sim.tests.test_runner_sched import finish_run

    finish_run(host, proc)
    report = reconcile(tmp_path, host, proc, "COMPLETED", apply=True)
    assert report["actions"][0]["removed"] is True and "ORPHAN_RUNNING" not in codes(report)


# ------------------------------------------------------------------------------------------------------ P15 (G4-08)
def test_p15_counts_owned_processes_even_when_the_task_is_still_registered(tmp_path):
    host, proc, audit = started(tmp_path)
    host.alive_pids.clear()
    facts = sched.scan_unreconciled(tmp_path, host, alive=host.alive, owned_of=lambda p: dict(ALIVE2))
    assert facts["owned_alive"] == 2 and facts["orphan_processes"] == 1 and facts["tasks"] == [RUN]
    assert preflight.judge_p15({**facts, "unreconciled_runs": 0, "folder_tasks": 0, "orphan_processes": 0})                    # owned_alive sozinho reprova
    clean = sched.scan_unreconciled(tmp_path / "c", FakeHost(tmp_path / "c"), alive=lambda pid: False, owned_of=lambda p: dict(NONE))
    assert (clean["owned_alive"], clean["owned_unknown"]) == (0, 0)
    unknown = sched.scan_unreconciled(tmp_path, host, alive=host.alive, owned_of=lambda p: {"alive": 0, "unknown": 1, "suspects": 2})
    assert unknown["owned_unknown"] == 3
    missing = sched.scan_unreconciled(tmp_path, host, alive=host.alive)
    assert missing["owned_alive"] is None and missing["owned_unknown"] is None
    assert preflight.judge_p15({**missing, "folder_tasks": 0, "unreconciled_runs": 0, "orphan_processes": 0})                  # fato ausente reprova


# ------------------------------------------------------------------------------------------------------ launch (marcador, motor)
def test_launch_records_the_schedule_pid_the_owner_marker_and_the_engine_identity(tmp_path):
    host = FakeHost(tmp_path)
    fake = FakeOS().add(900, created=500, ppid=555, name="cmd.exe")
    doc = launch(tmp_path, host, query=fake.query)
    saved = json.loads((detach.proc_dir(tmp_path, RUN) / detach.LAUNCH).read_text())
    assert saved["schedule_pid"] == 555 and saved["owner_marker"] == "-m sim.runner.cli run --run-id X" and doc["engine_pids"] == [900]
    beat = [r for r in sched.Audit(detach.proc_dir(tmp_path, RUN), tmp_path / sched.ANCHOR_DIR, RUN).records() if r["event"] == "HEARTBEAT_SEEN"][0]
    assert beat["payload"]["engine_identity"] == [{"pid": 900, "created": 500, "iso": watch.filetime_iso(500)}]


# ------------------------------------------------------------------------------------------------------ kill_task (G4-06)
def c2_world(tmp_path):
    """Scheduler encerra so o cmd; launcher e interpretador seguem vivos. Ledger via `facts.json:lineage` (como no C2 real)."""
    host, proc, audit = started(tmp_path)
    fake = FakeOS()
    fake.add(555, created=50, ppid=1, name="svchost.exe").add(900, created=500, ppid=555, name="cmd.exe")
    fake.add(902, created=510, ppid=900, name="python.exe").add(901, created=520, ppid=902, name="python.exe")
    (proc / "facts.json").write_text(json.dumps({"lineage": {"self": {"pid": 901, "created": 520, "name": "python.exe"},
                                                             "parent": {"pid": 902, "created": 510, "name": "python.exe"},
                                                             "grandparent": {"pid": 900, "created": 500, "name": "cmd.exe"}}}), encoding="utf-8")
    marker = containment.owner_marker(proc)
    rows = [{"pid": 902, "ppid": 900, "name": "python.exe", "cmd": f'"C:\\venv\\python.exe"  {marker}'},
            {"pid": 901, "ppid": 902, "name": "python.exe", "cmd": f'"C:\\Python311\\python.exe" {marker}'}]
    fake.end(900)                                                            # o cmd morreu (limite/Stop-ScheduledTask)
    return host, proc, fake, rows


def test_kill_task_now_handles_the_python_that_survives_the_scheduler_end(tmp_path):
    host, proc, fake, rows = c2_world(tmp_path)
    snapshots = []
    host.hook = lambda h, label, name: snapshots.append((label, (proc / containment.RECOVERY_DIR).exists())) if label == "end" else None
    result = detach.kill_task(proc, confirm=True, run=host, ops=fake, table_fn=lambda: rows, sleep=lambda s: None, grace_s=0)
    assert len(list((proc / containment.RECOVERY_DIR).iterdir())) == 1                  # um unico snapshot (feito antes do fim forcado)
    assert snapshots == [("end", True)]                                        # a evidencia foi preservada ANTES do Stop-ScheduledTask
    assert result["instances_after"] == 0 and result["owned_alive_after"] == 0 and result["blocked"] == []   # cmd = 0, mas o run so termina com 0 vivos
    assert [pid for pid, _ in fake.terminated] == [901, 902]                    # folha -> raiz, por handle
    events = [r["event"] for r in sched.Audit(proc, tmp_path / sched.ANCHOR_DIR, RUN).records()]
    assert events.index("FORCED_END") < events.index("RECOVERY_STARTED") and events[-1] == "RECOVERY_DONE" and events.count("ORPHAN_TERMINATED") == 2
    assert json.loads((proc / detach.KILLED).read_text())["owned_alive_after"] == 0


def test_kill_task_reports_survivors_instead_of_claiming_zero_instances(tmp_path):
    host, proc, fake, rows = c2_world(tmp_path)
    fake.refuse_terminate.add(901)
    result = detach.kill_task(proc, confirm=True, run=host, ops=fake, table_fn=lambda: rows, sleep=lambda s: None, grace_s=0)
    assert result["instances_after"] == 0 and result["owned_alive_after"] >= 1 and result["blocked"]


def test_kill_task_without_preserved_evidence_does_nothing(tmp_path, monkeypatch):
    host, proc, fake, rows = c2_world(tmp_path)
    monkeypatch.setattr(containment, "snapshot_proc", lambda *a, **k: (_ for _ in ()).throw(OSError("sem espaco")))
    with pytest.raises(detach.DetachError, match="evidencia nao preservada"):
        detach.kill_task(proc, confirm=True, run=host, ops=fake, table_fn=lambda: rows)
    assert "end" not in host.labels() and fake.terminated == []


def test_kill_task_never_touches_a_process_whose_identity_is_unknown(tmp_path):
    host, proc, fake, rows = c2_world(tmp_path)
    fake.deny(901)
    result = detach.kill_task(proc, confirm=True, run=host, ops=fake, table_fn=lambda: rows, sleep=lambda s: None, grace_s=0)
    assert fake.terminated == [] and result["blocked"] and not (proc / detach.STOP).exists()


# ------------------------------------------------------------------------------------------------------ sonda com vigia (B)
def probe_os_world(*, verified=True):
    fake = FakeOS()
    fake.add(555, created=50, ppid=1, name="svchost.exe").add(900, created=500, ppid=555, name="cmd.exe" if verified else "other.exe")
    fake.add(902, created=510, ppid=900, name="python.exe").add(os.getpid(), created=520, ppid=902, name="python.exe")
    return fake


def containment_ports(fake, proc, **over):
    marker = containment.owner_marker(proc)
    ports = probe_ports(ops=fake, query=fake.query, cmdline_of={900: f"cmd.exe /c {marker}"}.get, guard_interval_s=0.01, hard_exit=lambda code: None,
                        lineage=lambda: {})
    ports.update(over)
    return ports


def scheduler_probe_proc(tmp_path, *, scheduler=True):
    proc = probe_proc(tmp_path)
    marker = MARKER.format(proc=proc)
    doc = {"python": "C:\\venv\\Scripts\\python.exe", "pid": 1, "t_launch": 1, "owner_marker": marker, "schedule_pid": 555}
    if scheduler:
        doc["mechanism"] = "task_scheduler"
    (proc / detach.LAUNCH).write_text(json.dumps(doc), encoding="utf-8")
    return proc


def test_the_probe_ends_itself_when_the_engine_is_lost_and_records_engine_lost(tmp_path):
    fake, clock = probe_os_world(), Clock()
    proc = scheduler_probe_proc(tmp_path)

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.t == 1012:
            fake.end(900)                                                  # o Scheduler encerrou so o cmd
            time.sleep(0.3)                                                # a thread da vigia observa o motor (2 leituras, 10 ms)

    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=containment_ports(fake, proc), sleep=sleep, clock=clock)
    exit_doc = json.loads((proc / detach.EXIT).read_text())
    assert exit_doc["code"] == 6 and exit_doc["reason"] == "ENGINE_LOST" and facts["exit_code"] == 6 == detach_probe.process_exit_code(facts)
    assert facts["engine_lost"] is True and facts["exit_reason"] == "ENGINE_LOST" and clock.t < 1030              # saiu logo, nao esperou o cap
    evidence = json.loads((proc / containment.ENGINE_LOST_JSON).read_text())
    assert evidence["engine"] == {"pid": 900, "created": 500} and evidence["confirmations"] == 2
    assert termination.classify_termination(exit_doc, None, T, ["STARTED"], owned_alive=0)["class"] == "INCOMPLETE_EVIDENCE"      # o cmd morreu: sem exit_code.txt
    assert termination.classify_termination(exit_doc, 6, 6, owned_alive=0)["class"] == "ENGINE_LOST"
    identity = containment.read_identity(proc)
    assert identity["engine_verified"] is True and identity["chain"][0]["pid"] == os.getpid()


def test_a_probe_with_a_healthy_engine_finishes_normally_and_cancels_the_hard_exit(tmp_path):
    fake, clock = probe_os_world(), Clock()
    proc = scheduler_probe_proc(tmp_path)
    exits = []

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.t >= 1030:
            (proc / detach.STOP).write_text("go")

    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=containment_ports(fake, proc, hard_exit=exits.append), sleep=sleep, clock=clock)
    assert facts["exit_code"] == 0 and facts["engine_lost"] is False and not (proc / containment.ENGINE_LOST_JSON).exists() and exits == []
    heartbeat = [json.loads(line) for line in (proc / detach_probe.FACTS_LOG).read_text().splitlines() if '"heartbeat"' in line][0]
    assert heartbeat["containment"] == {"applicable": True, "engine_verified": True, "reason": None, "guard": True}


def test_the_hard_exit_fires_if_the_probe_does_not_stop_within_the_grace(tmp_path, monkeypatch):
    fake, clock = probe_os_world(), Clock()
    proc = scheduler_probe_proc(tmp_path)
    exits = []
    monkeypatch.setattr(containment, "PROBE_GRACE_S", 0.05)

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.t == 1012:
            fake.end(900)
            time.sleep(0.4)                                                # o laco principal "demora": a graca de 50 ms expira antes

    detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=containment_ports(fake, proc, hard_exit=exits.append), sleep=sleep, clock=clock)
    assert exits == [6]
    assert json.loads((proc / detach.EXIT).read_text())["reason"] == "ENGINE_LOST"


def test_an_engine_that_cannot_be_verified_does_not_stop_a_probe_but_is_recorded(tmp_path):
    fake, clock = probe_os_world(verified=False), Clock()
    proc = scheduler_probe_proc(tmp_path)
    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=20, ports=containment_ports(fake, proc), sleep=clock.sleep, clock=clock)
    assert facts["exit_code"] == 4 and facts["exit_reason"] == "TIMEOUT"
    identity = containment.read_identity(proc)
    assert identity["engine_verified"] is False and identity["reason"]
    heartbeat = [json.loads(line) for line in (proc / detach_probe.FACTS_LOG).read_text().splitlines() if '"heartbeat"' in line][0]
    assert heartbeat["containment"]["guard"] is False and heartbeat["containment"]["engine_verified"] is False


def test_outside_the_scheduler_the_probe_writes_no_ledger_and_starts_no_guard(tmp_path):
    fake, clock = probe_os_world(), Clock()
    proc = scheduler_probe_proc(tmp_path, scheduler=False)
    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=10, ports=containment_ports(fake, proc), sleep=clock.sleep, clock=clock)
    assert facts["exit_code"] == 4 and not (proc / containment.IDENTITY_JSON).exists()
    heartbeat = [json.loads(line) for line in (proc / detach_probe.FACTS_LOG).read_text().splitlines() if '"heartbeat"' in line][0]
    assert heartbeat["containment"]["applicable"] is False


def test_an_unwritable_identity_ledger_is_loud_and_leaves_an_exception_record(tmp_path, monkeypatch):
    fake, clock = probe_os_world(), Clock()
    proc = scheduler_probe_proc(tmp_path)
    real = detach._atomic_write

    def flaky(path, doc):
        if path.name == containment.IDENTITY_JSON:
            raise OSError("disco cheio")
        return real(path, doc)

    monkeypatch.setattr(detach, "_atomic_write", flaky)
    with pytest.raises(OSError, match="disco cheio"):
        detach_probe.collect2(proc, interactive_session=1, cap_s=20, ports=containment_ports(fake, proc), sleep=clock.sleep, clock=clock)
    assert json.loads((proc / detach.EXIT).read_text())["reason"] == "EXCEPTION"


# ------------------------------------------------------------------------------------------------------ monitor/juiz com processos do run
def orphan_rows(proc):
    marker = containment.owner_marker(proc)
    return [{"pid": 902, "ppid": 900, "name": "python.exe", "cmd": f'"C:\\venv\\python.exe"  {marker}'},
            {"pid": 901, "ppid": 902, "name": "python.exe", "cmd": f'"C:\\Python311\\python.exe" {marker}'}]


def test_the_scheduler_ending_only_the_cmd_is_classified_as_orphan_running_and_fails_the_probe(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)

    def terminated(p):
        host.tasks[name].update(state=sched.STATE_READY, last=T)               # limite do Scheduler: Ready / 0x41306, sem arquivos

    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc, probe_dies=True, tweak=terminated, orphans=True)
    assert monitor["termination"]["class"] == "ORPHAN_RUNNING" and monitor["termination"]["success"] is False and monitor["containment"]["alive"] == 2
    assert {"D2-09_stop_exit_clean", "D2-11_removed_clean_evidence"} <= set(final["verdict"]["failed"])
    assert final["verdict"]["verdict"] == "FAIL"
    assert host.os.terminated == []                                             # o monitor OBSERVA; quem encerra e a recuperacao autorizada


def test_unknown_identities_leave_the_classification_unknown_never_clean(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    host.os.deny(901)

    def terminated(p):
        host.tasks[name].update(state=sched.STATE_READY, last=T)

    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc, probe_dies=True, tweak=terminated, orphans=True)
    assert monitor["termination"]["class"] == "UNKNOWN" and monitor["termination"]["owned_checked"] is False and monitor["containment"]["unknown"] >= 1
    assert "D2-11_removed_clean_evidence" in final["verdict"]["failed"]


def test_a_clean_stop_leaves_zero_owned_processes_and_passes(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert monitor["containment"] == {**NONE, "alive_pids": [], "unknown_detail": [], "suspect_detail": []} or monitor["containment"]["alive"] == 0
    assert monitor["termination"]["owned_checked"] is True and final["verdict"]["verdict"] == "PASS"


@pytest.mark.parametrize("change", [{"alive": 1}, {"unknown": 1}, {"suspects": 1}])
def test_d2_11_requires_zero_owned_alive_unknown_and_suspects(change):
    monitor = good_monitor("L1")
    monitor["containment"] = {**NONE, **change}
    assert detach_probe.judge_detach2(good_facts(), monitor, "L1")["failed"] == ["D2-11_removed_clean_evidence"]
    gone = good_monitor("L1")
    gone.pop("containment")
    assert detach_probe.judge_detach2(good_facts(), gone, "L1")["failed"] == ["D2-11_removed_clean_evidence"]


def test_d2_09_requires_that_the_owned_processes_were_actually_checked():
    monitor = good_monitor("L1")
    monitor["termination"]["owned_checked"] = False
    assert detach_probe.judge_detach2(good_facts(), monitor, "L1")["failed"] == ["D2-09_stop_exit_clean"]


# ------------------------------------------------------------------------------------------------------ NH
def scheduler_launch(proc):
    proc.mkdir(parents=True, exist_ok=True)
    (proc / detach.LAUNCH).write_text(json.dumps({"mechanism": "task_scheduler", "schedule_pid": 555, "run_id": "RUN-x"}), encoding="utf-8")


def test_nh_containment_is_inert_outside_the_scheduler(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(containment, "install", lambda proc, **kw: seen.update(kw) or (None, None))
    proc = tmp_path / "RUN-x.proc"
    assert nh_phases.start_containment(proc, object()) is None
    assert seen["applicable"] is False and seen["require"] is True and not (proc / detach.EXIT).exists()


def test_nh_refuses_to_start_without_a_verifiable_engine_and_records_why(tmp_path, monkeypatch):
    proc = tmp_path / "RUN-x.proc"
    scheduler_launch(proc)
    monkeypatch.setattr(containment, "install", lambda p, **kw: (_ for _ in ()).throw(containment.ContainmentUnavailable("motor sem prova")))
    with pytest.raises(containment.ContainmentUnavailable):
        nh_phases.start_containment(proc, object())
    saved = json.loads((proc / detach.EXIT).read_text())
    assert saved["code"] == 7 and saved["reason"] == "CONTAINMENT_UNAVAILABLE" and saved["process_code"] == 3 and "motor sem prova" in saved["detail"]
    assert termination.classify_termination(saved, 3, 3, owned_alive=0)["class"] == "CONTAINMENT_UNAVAILABLE"
    assert issubclass(containment.ContainmentUnavailable, RunnerError)                  # a CLI devolve 3 (violacao de contrato), nunca 0


def test_nh_wires_a_graceful_stop_then_a_hard_exit_after_120_seconds(tmp_path, monkeypatch):
    proc = tmp_path / "RUN-x.proc"
    scheduler_launch(proc)
    captured, exits = {}, []
    monkeypatch.setattr(containment, "install", lambda p, **kw: captured.update(kw) or (None, "GUARD"))
    monkeypatch.setattr(os, "_exit", exits.append)
    from sim.runner import host

    stop = host.StopFlag(proc / detach.STOP)
    assert nh_phases.start_containment(proc, stop) == "GUARD"
    assert captured["grace_s"] == containment.NH_GRACE_S == 120 and captured["applicable"] is True and captured["schedule_pid"] == 555
    assert stop.requested is False
    captured["graceful"]()
    assert stop.requested is True and stop.reason == "ENGINE_LOST"
    captured["hard_exit"]()
    saved = json.loads((proc / detach.EXIT).read_text())
    assert exits == [6] and saved["code"] == 6 and saved["reason"] == "ENGINE_LOST" and saved["process_code"] == 1


def test_a_lost_engine_makes_an_aborted_nh_run_end_as_engine_lost_but_never_overrides_completion(tmp_path):
    aborted = detach.run_and_record_exit(tmp_path / "a", lambda: {"state": "ABORTED"}, lambda: None, clock=lambda: 5, lost=lambda: True)
    assert aborted == {"state": "ABORTED"}
    saved = json.loads((tmp_path / "a" / detach.EXIT).read_text())
    assert (saved["code"], saved["reason"], saved["process_code"]) == (6, "ENGINE_LOST", 1)
    assert termination.classify_termination(saved, 1, 1, owned_alive=0)["class"] == "ENGINE_LOST"
    detach.run_and_record_exit(tmp_path / "c", lambda: {"state": "COMPLETED"}, lambda: None, clock=lambda: 5, lost=lambda: True)
    assert json.loads((tmp_path / "c" / detach.EXIT).read_text())["reason"] == "COMPLETED"
    detach.run_and_record_exit(tmp_path / "n", lambda: {"state": "ABORTED"}, lambda: None, clock=lambda: 5, lost=lambda: False)
    assert json.loads((tmp_path / "n" / detach.EXIT).read_text())["reason"] == "ABORTED"


def test_the_nh_preflight_collects_owned_processes_for_p15():
    import inspect

    assert "owned_of=" in inspect.getsource(nh_phases.collect_preflight_facts) and "containment.owned_summary" in inspect.getsource(nh_phases.collect_preflight_facts)


# ------------------------------------------------------------------------------------------------------ CLI
def test_cli_recover_forwards_confirmation_and_grace_and_signals_blocked_runs(tmp_path, monkeypatch, capsys):
    from sim.tests.runner_support import setup_run

    run = setup_run(tmp_path)
    root = str(run.registry.root)
    proc = detach.proc_dir(run.registry.root, run.run_id)
    proc.mkdir(parents=True, exist_ok=True)
    (proc / detach.LAUNCH).write_text(json.dumps({"run_id": run.run_id}), encoding="utf-8")
    seen = {}
    monkeypatch.setattr(containment, "recover", lambda p, r, name, **kw: seen.update(kw, name=name) or {"blocked": [], "owned_alive_after": 0, "actions": []})
    assert cli.main(["recover", "--run-id", run.run_id, "--runs-root", root, "--confirm", "--grace-s", "7"]) == 0
    assert seen["confirm"] is True and seen["grace_s"] == 7.0 and seen["name"] == run.run_id
    assert cli.main(["recover", "--run-id", run.run_id, "--runs-root", root]) == 0
    assert seen["confirm"] is False and seen["grace_s"] == 20.0                          # sem --confirm nunca ha encerramento forcado
    capsys.readouterr()
    monkeypatch.setattr(containment, "recover", lambda p, r, name, **kw: {"blocked": ["x"], "owned_alive_after": 1, "actions": []})
    assert cli.main(["recover", "--run-id", run.run_id, "--runs-root", root]) == 1
    monkeypatch.setattr(containment, "recover", lambda p, r, name, **kw: {"blocked": [], "owned_alive_after": None, "actions": []})
    assert cli.main(["recover", "--run-id", run.run_id, "--runs-root", root]) == 1                 # contagem indeterminada nunca e sucesso
    capsys.readouterr()


def test_cli_sched_reconcile_passes_the_owned_processes_to_the_reconciliation(tmp_path, monkeypatch, capsys):
    host = FakeHost(tmp_path)
    monkeypatch.setattr(sched, "run_default", host)
    launch(tmp_path, host)
    host.alive_pids.clear()
    host.tasks[RUN].update(state=sched.STATE_READY, instances=[], last=T)
    monkeypatch.setattr(containment, "owned_summary", lambda proc, run, **kw: dict(ALIVE2))
    assert cli.main(["sched-reconcile", "--run-id", RUN, "--runs-root", str(tmp_path), "--apply"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "ORPHAN_RUNNING" in {f["code"] for f in report["findings"]} and report["actions"] == [] and report["owned"]["alive"] == 2
    assert RUN in host.tasks
    monkeypatch.setattr(containment, "owned_summary", lambda proc, run, **kw: dict(NONE))
    assert cli.main(["sched-reconcile", "--run-id", RUN, "--runs-root", str(tmp_path), "--apply"]) == 1       # o achado ORPHAN_TASK continua a sair 1
    assert RUN not in host.tasks                                                                           # mas agora remove (0x41306, 0 vivos)


def test_a_stop_that_arrives_with_the_lost_engine_wins_and_is_not_reported_as_engine_lost(tmp_path):
    fake, clock = probe_os_world(), Clock()
    proc = scheduler_probe_proc(tmp_path)

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.t == 1012:
            fake.end(900)
            time.sleep(0.3)
            (proc / detach.STOP).write_text("go")

    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=containment_ports(fake, proc), sleep=sleep, clock=clock)
    assert facts["exit_reason"] == "STOP" and facts["exit_code"] == 0 and facts["engine_lost"] is False
    assert (proc / containment.ENGINE_LOST_JSON).exists()                              # o aviso fica, mas o motivo do termino e o STOP


def test_the_lost_engine_does_not_fire_the_hard_exit_when_the_probe_finishes_within_the_grace(tmp_path):
    fake, clock = probe_os_world(), Clock()
    proc = scheduler_probe_proc(tmp_path)
    exits = []

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.t == 1012:
            fake.end(900)
            time.sleep(0.3)

    detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=containment_ports(fake, proc, hard_exit=exits.append), sleep=sleep, clock=clock)
    assert exits == []                                                                  # a graca (5 s) nao expirou: saida normal, sem saida dura


def test_the_guard_is_always_finished_on_normal_and_exceptional_ends(tmp_path, monkeypatch):
    calls, real = [], containment.Containment.finish
    monkeypatch.setattr(containment.Containment, "finish", lambda self: calls.append(1) or real(self))
    fake, clock = probe_os_world(), Clock()
    proc = scheduler_probe_proc(tmp_path)
    detach_probe.collect2(proc, interactive_session=1, cap_s=10, ports=containment_ports(fake, proc), sleep=clock.sleep, clock=clock)
    assert calls == [1]
    fake2, clock2 = probe_os_world(), Clock()
    (tmp_path / "e").mkdir()
    proc2 = scheduler_probe_proc(tmp_path / "e")

    def boom(seconds):
        clock2.sleep(seconds)
        if clock2.t == 1005:
            raise RuntimeError("falha no laco")

    with pytest.raises(RuntimeError):
        detach_probe.collect2(proc2, interactive_session=1, cap_s=600, ports=containment_ports(fake2, proc2), sleep=boom, clock=clock2)
    assert calls == [1, 1]


def test_unknown_identities_block_removal_even_when_exit_json_exists(tmp_path):
    host, proc, audit = c2_like(tmp_path, exit_json=True)
    report = reconcile(tmp_path, host, proc, None, owned={"alive": 0, "unknown": 1, "suspects": 0}, apply=True)
    assert report["actions"] == [] and RUN in host.tasks and "IDENTITY_UNKNOWN" in codes(report)
    clean = reconcile(tmp_path, host, proc, None, owned=dict(NONE), apply=True)
    assert clean["actions"][0]["removed"] is True
