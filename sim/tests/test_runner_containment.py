"""
SIM-1.5G.4 -- contencao da arvore de processos (achado F-1 do L1-C): ledger de identidade, vigia do motor, posse P1/P2/P3, estados de
liveness, encerramento por HANDLE e recuperacao. Duplos (`FakeOS`): nenhum processo real alem dos filhos criados pelo proprio teste.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sim.runner import containment
from sim.runner import detach
from sim.runner import sched
from sim.runner import watch
from sim.tests.sched_support import FakeOS

MARKER = '-m sim.runner.detach_probe --detach2 --proc-dir "{proc}" --interactive-session 1 --cap-s 600'


def build_world(tmp_path, *, with_ledger=True):
    """Arvore do C2: Schedule 555 -> cmd 900 -> launcher 902 -> interpretador 901; ledger gravado pelo proprio run."""
    proc = tmp_path / "RUN-x.proc"
    proc.mkdir(parents=True)
    marker = MARKER.format(proc=proc)
    fake = FakeOS()
    fake.add(555, created=50, ppid=1, name="svchost.exe").add(900, created=500, ppid=555, name="cmd.exe")
    fake.add(902, created=510, ppid=900, name="python.exe").add(901, created=520, ppid=902, name="python.exe")
    (proc / detach.LAUNCH).write_text(json.dumps({"run_id": "RUN-x", "mechanism": "task_scheduler", "schedule_pid": 555, "owner_marker": marker,
                                                   "cmdline": f"cmd.exe /d /c {marker}"}), encoding="utf-8")
    rows = [{"pid": 900, "ppid": 555, "name": "cmd.exe", "cmd": f'cmd.exe /d /v:on /s /c ""py" {marker} 1>>"o"'},
            {"pid": 902, "ppid": 900, "name": "python.exe", "cmd": f'"C:\\venv\\Scripts\\python.exe"  {marker}'},
            {"pid": 901, "ppid": 902, "name": "python.exe", "cmd": f'"C:\\Python311\\python.exe" {marker}  '}]
    identity = None
    if with_ledger:
        identity = containment.arm(proc, ops=fake, cmdline_of={900: rows[0]["cmd"]}.get, schedule_pid=555, pid=901)
    return fake, proc, marker, rows, identity


def owned_of(fake, proc, marker, rows, **kw):
    return containment.enumerate_owned(proc, ops=fake, table=rows, marker=marker, exclude=kw.get("exclude", set()))


# ------------------------------------------------------------------------------------------------------ marcador e ledger
def test_the_owner_marker_comes_from_the_launch_then_from_the_recorded_command_line_and_is_normalised():
    assert containment.owner_marker("x", {"owner_marker": "-m  a   b"}) == "-m a b"
    assert containment.owner_marker("C:\\p\\X.proc", {"cmdline": 'cmd /c py -m m --proc-dir  "C:\\p\\X.proc" --x'}) == '--proc-dir "C:\\p\\X.proc"'
    assert containment.owner_marker("C:\\p\\X.proc", {"run_id": "RUN-1", "cmdline": "cmd /c py -m sim.runner.cli run --run-id RUN-1"}) == "--run-id RUN-1"
    for doc in ({}, {"cmdline": "nada"}, {"owner_marker": "   "}, {"run_id": "RUN-1", "cmdline": "--run-id OUTRO"}):
        assert containment.owner_marker("C:\\p\\X.proc", doc) is None
    assert containment.norm("  a \t b\n") == "a b" and containment.norm(None) == ""


def test_arm_writes_the_identity_chain_and_verifies_the_engine(tmp_path):
    fake, proc, marker, rows, identity = build_world(tmp_path)
    assert identity["engine_verified"] is True and identity["reason"] is None
    assert [(c["role"], c["pid"]) for c in identity["chain"]] == [("interpreter", 901), ("launcher", 902), ("engine", 900)]
    assert identity["engine"]["pid"] == 900 and identity["engine"]["created"] == 500 and identity["schedule_pid"] == 555
    assert identity["written_by"] == {"pid": 901, "created": 520} and identity["marker"] == marker
    assert containment.read_identity(proc) == identity
    assert [(e["pid"], e["created"]) for e in containment.ledger_entries(proc)] == [(901, 520), (902, 510), (900, 500)]


@pytest.mark.parametrize("scenario", ["no_cmd", "parent_not_schedule", "parent_wrong_name", "no_marker", "marker_missing_in_cmd", "cmd_unreadable",
                                      "schedule_pid_unknown", "schedule_pid_bool"])
def test_arm_refuses_to_verify_an_engine_it_cannot_prove(tmp_path, scenario):
    fake, proc, marker, rows, _ = build_world(tmp_path, with_ledger=False)
    cmd = rows[0]["cmd"]
    schedule_pid, cmdline = 555, {900: cmd}.get
    if scenario == "no_cmd":
        fake.procs[900]["name"] = "python.exe"
    elif scenario == "parent_not_schedule":
        fake.add(777, created=40, ppid=1, name="svchost.exe")
        fake.procs[900]["ppid"] = 777
    elif scenario == "parent_wrong_name":
        fake.procs[555]["name"] = "explorer.exe"
    elif scenario == "no_marker":
        (proc / detach.LAUNCH).write_text(json.dumps({"run_id": "RUN-x"}), encoding="utf-8")
    elif scenario == "marker_missing_in_cmd":
        cmdline = {900: "cmd.exe /c outra coisa"}.get
    elif scenario == "cmd_unreadable":
        cmdline = {}.get
    elif scenario == "schedule_pid_unknown":
        schedule_pid = None
    elif scenario == "schedule_pid_bool":
        schedule_pid = True
    identity = containment.arm(proc, ops=fake, cmdline_of=cmdline, schedule_pid=schedule_pid, pid=901)
    assert identity["engine_verified"] is False and identity["engine"] is None and identity["reason"]
    assert containment.read_identity(proc)["engine_verified"] is False


def test_arm_without_a_process_table_is_unverified_and_a_write_failure_is_loud(tmp_path, monkeypatch):
    fake, proc, marker, rows, _ = build_world(tmp_path, with_ledger=False)

    def broken():
        raise watch.WatchError("sem tabela")

    fake.snapshot = broken
    identity = containment.arm(proc, ops=fake, cmdline_of={}.get, schedule_pid=555, pid=901)
    assert identity["engine_verified"] is False and "tabela de processos indisponivel" in identity["reason"]
    fake2, proc2, _, _, _ = build_world(tmp_path / "w", with_ledger=False)
    monkeypatch.setattr(detach, "_atomic_write", lambda path, doc: (_ for _ in ()).throw(OSError("disco cheio")))
    with pytest.raises(OSError, match="disco cheio"):                                  # nunca um 'verificado' silencioso sem ledger
        containment.arm(proc2, ops=fake2, cmdline_of={}.get, schedule_pid=555, pid=901)


def test_the_ledger_also_reads_the_lineage_of_older_evidence_and_never_duplicates(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path, with_ledger=False)
    assert containment.ledger_entries(proc) == []
    (proc / "facts.json").write_text(json.dumps({"lineage": {"self": {"pid": 901, "created": 520, "name": "python.exe"},
                                                             "parent": {"pid": 902, "created": 510, "name": "python.exe"},
                                                             "grandparent": {"pid": 900, "created": 500, "name": "cmd.exe"}}}), encoding="utf-8")
    assert [e["pid"] for e in containment.ledger_entries(proc)] == [901, 902, 900]
    containment.arm(proc, ops=fake, cmdline_of={900: rows[0]["cmd"]}.get, schedule_pid=555, pid=901)
    assert [e["pid"] for e in containment.ledger_entries(proc)] == [901, 902, 900]            # identity.json + lineage: sem duplicatas
    (proc / "facts.json").write_text("{", encoding="utf-8")
    (proc / containment.IDENTITY_JSON).write_text("[]", encoding="utf-8")
    assert containment.ledger_entries(proc) == [] and containment.read_identity(proc) is None


# ------------------------------------------------------------------------------------------------------ B: vigia do motor
def test_the_guard_fires_only_after_two_consecutive_ended_readings_and_unknown_never_fires():
    fake = FakeOS().add(900, created=500)
    seen = []
    guard = containment.Guard({"pid": 900, "created": 500}, ops=fake, clock=lambda: 100, on_lost=seen.append)
    assert guard.step() == watch.ALIVE and guard.lost is None
    fake.end(900)
    assert guard.step() == "ENDED" and guard.lost is None                      # 1a leitura: ainda nao
    fake.add(900, created=500)                                                 # o motor "volta": a sequencia recomeca (nunca acontece, mas prova o reset)
    assert guard.step() == watch.ALIVE
    fake.end(900)
    guard.step()
    assert guard.lost is None
    guard.step()
    assert guard.lost["confirmations"] == 2 and guard.lost["how"] == "absent" and len(seen) == 1 and guard.lost["engine"] == {"pid": 900, "created": 500}
    guard.step()
    assert len(seen) == 1                                                       # dispara uma unica vez
    denied = FakeOS().add(900, created=500)
    unknown = containment.Guard({"pid": 900, "created": 500}, ops=denied, clock=lambda: 1)
    denied.deny(900)
    for _ in range(10):
        assert unknown.step() == watch.UNKNOWN
    assert unknown.lost is None and unknown.unknown_observations == 10


def test_a_reused_engine_pid_counts_as_the_engine_being_gone():
    fake = FakeOS().add(900, created=500)
    guard = containment.Guard({"pid": 900, "created": 500}, ops=fake, clock=lambda: 1)
    fake.reuse(900, created=999)
    guard.step()
    guard.step()
    assert guard.lost["how"] == "pid_reused"


def test_unknown_between_ended_readings_resets_the_confirmation_streak():
    fake = FakeOS().add(900, created=500)
    guard = containment.Guard({"pid": 900, "created": 500}, ops=fake, clock=lambda: 1)
    fake.end(900)
    guard.step()
    fake.deny(900)
    guard.step()
    fake.denied.clear()
    guard.step()
    assert guard.lost is None
    guard.step()
    assert guard.lost is not None


def test_the_guard_thread_detects_the_loss_of_a_real_engine_process():
    engine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=0x08000000)
    try:
        target = watch.make_target("helper", engine.pid)
        lost = threading.Event()
        guard = containment.Guard(target, ops=watch.NativeOps, interval_s=0.05, on_lost=lambda ev: lost.set())
        guard.start()
        time.sleep(0.3)
        assert not lost.is_set() and guard.lost is None
        engine.terminate()
        engine.wait(timeout=30)
        assert lost.wait(timeout=10), "a vigia nao detectou o fim do motor real"
        assert guard.lost["how"] == "absent"
    finally:
        guard.stop()
        if engine.poll() is None:
            engine.terminate()
            engine.wait(timeout=30)


class FakeTimer:
    instances = []

    def __init__(self, interval, function):
        self.interval, self.function, self.daemon, self.started, self.cancelled = interval, function, False, False, False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def make_containment(tmp_path, **over):
    fake, proc, marker, rows, identity = build_world(tmp_path)
    FakeTimer.instances = []
    calls = {"graceful": 0, "hard": 0}
    kwargs = dict(ops=fake, graceful=lambda: calls.__setitem__("graceful", calls["graceful"] + 1),
                  hard_exit=lambda: calls.__setitem__("hard", calls["hard"] + 1), grace_s=7, clock=lambda: 1234, timer=FakeTimer)
    kwargs.update(over)
    return fake, proc, containment.Containment(proc, identity, **kwargs), calls


def test_losing_the_engine_writes_the_warning_asks_for_a_graceful_end_and_arms_the_hard_exit(tmp_path):
    fake, proc, guard, calls = make_containment(tmp_path)
    fake.end(900)
    guard.guard.step()
    guard.guard.step()
    assert guard.lost and calls["graceful"] == 1 and calls["hard"] == 0
    evidence = json.loads((proc / containment.ENGINE_LOST_JSON).read_text())
    assert evidence["engine"] == {"pid": 900, "created": 500} and evidence["grace_s"] == 7 and evidence["confirmations"] == 2
    timer = FakeTimer.instances[0]
    assert timer.interval == 7 and timer.started and timer.daemon is True
    timer.function()                                                           # a graca acabou e o processo ainda existe
    assert calls["hard"] == 1


def test_finishing_normally_cancels_the_hard_exit_and_a_late_timer_does_nothing(tmp_path):
    fake, proc, guard, calls = make_containment(tmp_path)
    fake.end(900)
    guard.guard.step()
    guard.guard.step()
    guard.finish()
    assert FakeTimer.instances[0].cancelled is True
    FakeTimer.instances[0].function()
    assert calls["hard"] == 0


def test_a_failure_to_write_the_warning_never_prevents_the_graceful_end_or_the_hard_exit(tmp_path, monkeypatch):
    fake, proc, guard, calls = make_containment(tmp_path)
    monkeypatch.setattr(detach, "_atomic_write", lambda path, doc: (_ for _ in ()).throw(OSError("disco cheio")))
    fake.end(900)
    guard.guard.step()
    guard.guard.step()
    assert calls["graceful"] == 1 and "disco cheio" in guard.write_error and FakeTimer.instances[0].started
    monkeypatch.undo()
    fake2, proc2, guard2, calls2 = make_containment(tmp_path / "b", graceful=lambda: (_ for _ in ()).throw(RuntimeError("falhou")))
    fake2.end(900)
    guard2.guard.step()
    with pytest.raises(RuntimeError):
        guard2.guard.step()
    assert FakeTimer.instances[-1].started                                       # mesmo se o gracioso falhar, a saida dura fica armada


def test_install_is_inert_outside_the_scheduler_and_fails_closed_when_required(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path, with_ledger=False)
    cmd = {900: rows[0]["cmd"]}.get
    common = dict(ops=fake, cmdline_of=cmd, schedule_pid=555, graceful=lambda: None, hard_exit=lambda: None, grace_s=1, interval_s=0.05, pid=901)
    assert containment.install(proc, applicable=False, **common) == (None, None)
    assert not (proc / containment.IDENTITY_JSON).exists()
    bad = dict(common, cmdline_of={}.get)
    identity, guard = containment.install(proc, applicable=True, require=False, **bad)
    assert identity["engine_verified"] is False and guard is None
    with pytest.raises(containment.ContainmentUnavailable, match="motor da tarefa nao verificavel"):
        containment.install(proc, applicable=True, require=True, **bad)
    identity, guard = containment.install(proc, applicable=True, require=True, **common)
    try:
        assert identity["engine_verified"] is True and guard is not None and guard.guard._thread is not None and guard.guard._thread.is_alive()
    finally:
        guard.finish()
    assert issubclass(containment.ContainmentUnavailable, containment.ContainmentError)


# ------------------------------------------------------------------------------------------------------ C: posse P1/P2/P3
def test_p1_requires_ledger_identity_creation_and_the_exact_marker(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    owned = owned_of(fake, proc, marker, rows)
    try:
        assert sorted((e["pid"], e["proof"], e["state"]) for e in owned["entries"]) == [(900, "P1", "ALIVE"), (901, "P1", "ALIVE"), (902, "P1", "ALIVE")]
        assert len(owned["alive"]) == 3 and not owned["unknown"] and not owned["suspects"] and set(owned["handles"]) == {900, 901, 902}
        assert containment.summary(owned)["alive"] == 3 and containment.summary(owned)["alive_pids"][0][2] == "P1"
    finally:
        containment.close_owned(owned)
    assert sorted(fake.closed) == [900, 901, 902] and owned["handles"] == {}


def test_a_dead_ledger_process_is_ended_and_a_reused_pid_is_never_adopted(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.end(900)
    fake.reuse(901, created=99_999)                                              # outro processo ocupa o PID do interpretador
    owned = owned_of(fake, proc, marker, rows)
    try:
        states = {e["pid"]: (e["state"], e.get("how")) for e in owned["entries"]}
        assert states[900] == ("ABSENT", "absent") and states[901] == ("ABSENT", "pid_reused") and states[902][0] == "ALIVE"
        assert [e["pid"] for e in owned["alive"]] == [902] and 901 not in owned["handles"]
        assert fake.procs[901]["alive"] is True and fake.terminated == []         # o novo ocupante nem foi tocado
    finally:
        containment.close_owned(owned)


def test_unknown_identity_or_missing_command_line_never_counts_as_ended_or_owned(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.deny(901)
    owned = owned_of(fake, proc, marker, [r for r in rows if r["pid"] != 902])   # 902 vivo mas sem linha de comando na tabela
    try:
        assert sorted(u["pid"] for u in owned["unknown"]) == [901, 902]
        assert [e["pid"] for e in owned["alive"]] == [900] and "posse nao comprovada" in next(u["error"] for u in owned["unknown"] if u["pid"] == 902)
    finally:
        containment.close_owned(owned)
    owned2 = containment.enumerate_owned(proc, ops=fake, table=None, marker=None, exclude=set())
    try:
        assert owned2["alive"] == [] and {u["pid"] for u in owned2["unknown"]} >= {902, 900}      # sem marcador/tabela: nada e comprovado
    finally:
        containment.close_owned(owned2)


def test_a_ledger_pid_whose_command_line_lost_the_marker_is_a_p3_suspect_not_owned(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    rows[1] = dict(rows[1], cmd="python.exe -m outra.coisa")
    owned = owned_of(fake, proc, marker, rows)
    try:
        assert [s["pid"] for s in owned["suspects"]] == [902] and owned["suspects"][0]["proof"] == "P3"
        assert 902 not in [e["pid"] for e in owned["alive"]] and 902 not in owned["handles"]
    finally:
        containment.close_owned(owned)


def test_p2_children_need_a_live_owned_parent_and_a_creation_not_older_than_the_parent(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.add(950, created=530, ppid=901, name="docker.exe")                      # filho legitimo do interpretador
    fake.add(951, created=300, ppid=901, name="docker.exe")                      # ppid obsoleto: criado ANTES do suposto pai
    fake.add(952, created=540, ppid=950, name="git.exe")                         # neto: cadeia integra
    fake.add(953, created=540, ppid=1, name="docker.exe")                        # sem relacao
    owned = owned_of(fake, proc, marker, rows)
    try:
        by_pid = {e["pid"]: e for e in owned["entries"]}
        assert by_pid[950]["proof"] == "P2" and by_pid[952]["proof"] == "P2" and by_pid[950]["ppid"] == 901
        assert 951 not in by_pid and [s["pid"] for s in owned["suspects"]] == [951] and 953 not in by_pid
        assert {e["pid"] for e in owned["alive"]} == {900, 901, 902, 950, 952}
    finally:
        containment.close_owned(owned)


def test_unreadable_descendants_and_a_missing_process_table_are_unknown_not_clean(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.add(950, created=530, ppid=901, name="docker.exe")
    fake.deny(950)
    owned = owned_of(fake, proc, marker, rows)
    try:
        assert [u["pid"] for u in owned["unknown"]] == [950]
    finally:
        containment.close_owned(owned)
    fake2, proc2, marker2, rows2, _ = build_world(tmp_path / "t")

    def broken():
        raise watch.WatchError("sem tabela")

    fake2.snapshot = broken
    owned2 = owned_of(fake2, proc2, marker2, rows2)
    try:
        assert any(u["role"] == "snapshot" for u in owned2["unknown"])
    finally:
        containment.close_owned(owned2)


def test_p3_marker_lookalikes_are_reported_and_the_inspector_tree_is_excluded(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.add(700, created=600, ppid=1, name="python.exe")
    fake.add(701, created=600, ppid=1, name="python.exe")
    rows += [{"pid": 700, "ppid": 1, "name": "python.exe", "cmd": f"python {marker}"},
             {"pid": 701, "ppid": 1, "name": "python.exe", "cmd": f"python outro.py"}]
    owned = owned_of(fake, proc, marker, rows)
    try:
        assert [s["pid"] for s in owned["suspects"]] == [700] and owned["suspects"][0]["proof"] == "P3"
    finally:
        containment.close_owned(owned)
    quiet = owned_of(fake, proc, marker, rows, exclude={700})                      # 700 e o proprio inspetor: nunca e suspeito
    try:
        assert quiet["suspects"] == []
    finally:
        containment.close_owned(quiet)


def test_the_process_table_is_only_read_when_a_ledger_process_is_still_alive(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    for pid in (900, 901, 902):
        fake.end(pid)
    reads = []
    owned = containment.enumerate_owned(proc, ops=fake, table=lambda: reads.append(1) or rows, marker=marker, exclude=set())
    assert reads == [] and owned["alive"] == [] and owned["unknown"] == [] and all(e["state"] == "ABSENT" for e in owned["entries"])
    fake.add(901, created=520, ppid=902, name="python.exe")
    owned = containment.enumerate_owned(proc, ops=fake, table=lambda: reads.append(1) or rows, marker=marker, exclude=set())
    containment.close_owned(owned)
    assert reads == [1]


# ------------------------------------------------------------------------------------------------------ estados de liveness
@pytest.mark.parametrize("alive,unknown,suspects,task,engine,terminal,expected", [
    (2, 0, 0, True, True, False, "RUNNING"),
    (2, 0, 0, True, False, False, "ORPHANED_RUNNING"),
    (2, 0, 0, True, None, True, "ORPHANED_RUNNING"),                              # engine indeterminado nunca e 'vivo'
    (2, 0, 0, False, False, True, "SUPERVISION_LOST"),                            # tarefa ausente + processo vivo
    (0, 0, 0, True, False, True, "ENDED_CLEAN"),
    (0, 0, 0, False, False, True, "ENDED_CLEAN"),
    (0, 0, 0, True, False, False, "ENDED_UNCLEAN"),
    (0, 0, 0, False, False, False, "ENDED_UNCLEAN"),
    (0, 1, 0, True, False, True, "UNKNOWN"),
    (2, 1, 0, True, True, True, "UNKNOWN"),
    (0, 0, 1, False, False, True, "UNKNOWN"),
])
def test_the_liveness_state_is_derived_from_identities_never_from_the_task(alive, unknown, suspects, task, engine, terminal, expected):
    summary = {"alive": alive, "unknown": unknown, "suspects": suspects}
    assert containment.liveness_state(summary, task_found=task, engine_alive=engine, terminal_evidence=terminal) == expected
    assert expected in containment.STATES
    as_lists = {"alive": [object()] * alive, "unknown": [object()] * unknown, "suspects": [object()] * suspects}      # tambem aceita a forma completa
    assert containment.liveness_state(as_lists, task_found=task, engine_alive=engine, terminal_evidence=terminal) == expected


# ------------------------------------------------------------------------------------------------------ encerramento por handle
def test_forced_termination_needs_confirmation_and_is_blocked_by_unknown_or_p3(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    owned = owned_of(fake, proc, marker, rows)
    try:
        refused = containment.terminate_owned(owned, confirm=False)
        assert refused["terminated"] == [] and "confirmacao" in refused["blocked"][0] and fake.terminated == []
        assert containment.terminate_owned(owned, confirm=None)["terminated"] == []
        assert containment.terminate_owned(owned, confirm="yes")["terminated"] == [] and fake.terminated == []      # so o booleano True confirma
        owned["suspects"].append({"pid": 700, "proof": "P3"})
        blocked = containment.terminate_owned(owned, confirm=True)
        assert blocked["terminated"] == [] and "UNKNOWN ou processo P3" in blocked["blocked"][0] and fake.terminated == []
        owned["suspects"].clear()
        owned["unknown"].append({"pid": 5, "error": "x"})
        assert containment.terminate_owned(owned, confirm=True)["terminated"] == [] and fake.terminated == []
    finally:
        containment.close_owned(owned)


def test_forced_termination_goes_leaf_to_root_by_handle_and_reverifies_the_creation(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.add(950, created=530, ppid=901, name="docker.exe")
    owned = owned_of(fake, proc, marker, rows)
    try:
        result = containment.terminate_owned(owned, confirm=True, sleep=lambda s: None)
        order = [pid for pid, _ in fake.terminated]
        assert result["blocked"] == [] and order.index(950) < order.index(901) < order.index(902) < order.index(900)
        assert sorted(result["terminated"]) == [900, 901, 902, 950] and all(not fake.procs[p]["alive"] for p in (900, 901, 902, 950))
    finally:
        containment.close_owned(owned)


def test_a_pid_reused_after_the_handle_was_opened_is_never_the_one_that_gets_terminated(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    owned = owned_of(fake, proc, marker, rows)
    try:
        fake.reuse(901, created=99_999)                                           # entre a identificacao e a acao: o PID e reciclado
        result = containment.terminate_owned(owned, confirm=True, sleep=lambda s: None)
        assert (901, 520) in fake.terminated and (901, 99_999) not in fake.terminated      # morreu o ORIGINAL (handle retido)
        assert fake.procs[901]["alive"] is True and fake.procs[901]["created"] == 99_999    # o novo ocupante segue intacto
        assert 901 in result["terminated"]
    finally:
        containment.close_owned(owned)


@pytest.mark.parametrize("mutation,needle", [("handle_created", "criacao lida pelo handle difere"), ("indeterminate", "estado indeterminado"),
                                             ("refuse", "TerminateProcess falhou"), ("no_handle", "sem handle retido")])
def test_each_failed_check_blocks_that_process_and_nothing_else_is_assumed(tmp_path, mutation, needle):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    owned = owned_of(fake, proc, marker, rows)
    try:
        if mutation == "handle_created":
            fake.handle_created[901] = 123
        elif mutation == "indeterminate":
            fake.indeterminate.add(901)
        elif mutation == "refuse":
            fake.refuse_terminate.add(901)
        else:
            owned["handles"].pop(901)
        result = containment.terminate_owned(owned, confirm=True, sleep=lambda s: None)
        assert any(needle in b and "901" in b for b in result["blocked"]) and 901 not in result["terminated"]
        assert 901 not in [pid for pid, _ in fake.terminated] or mutation == "no_handle"
    finally:
        containment.close_owned(owned)


def test_a_process_that_survives_the_terminate_call_is_reported_not_assumed_dead(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    owned = owned_of(fake, proc, marker, rows)
    handle = owned["handles"][901]
    handle.terminate = lambda: True                                              # TerminateProcess "ok", mas o processo continua vivo
    try:
        result = containment.terminate_owned(owned, confirm=True, sleep=lambda s: None, wait_s=0.4)
        assert any("901" in b and "continua vivo" in b for b in result["blocked"]) and 901 not in result["terminated"]
    finally:
        containment.close_owned(owned)


def test_an_already_dead_owned_process_is_skipped_not_terminated(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    owned = owned_of(fake, proc, marker, rows)
    fake.end(901)
    try:
        result = containment.terminate_owned(owned, confirm=True, sleep=lambda s: None)
        assert 901 in result["skipped"] and 901 not in [pid for pid, _ in fake.terminated]
    finally:
        containment.close_owned(owned)


# ------------------------------------------------------------------------------------------------------ preservar evidencia
def test_the_snapshot_copies_the_run_files_with_a_hash_manifest_and_never_overwrites(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    (proc / "beats.jsonl").write_bytes(b"x\n")
    first = containment.snapshot_proc(proc, clock=lambda: 1_700_000_000)
    second = containment.snapshot_proc(proc, clock=lambda: 1_700_000_000)
    assert first["dir"] != second["dir"] and first["files"] >= 3
    manifest = json.loads((Path(first["dir"]) / "manifest.json").read_text())
    import hashlib

    assert manifest["beats.jsonl"] == hashlib.sha256(b"x\n").hexdigest() and (Path(first["dir"]) / "beats.jsonl").read_bytes() == b"x\n"
    assert (proc / containment.RECOVERY_DIR).is_dir()


def test_a_snapshot_that_cannot_be_written_raises(tmp_path, monkeypatch):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    monkeypatch.setattr(Path, "mkdir", lambda self, *a, **k: (_ for _ in ()).throw(OSError("somente leitura")))
    with pytest.raises(OSError, match="somente leitura"):
        containment.snapshot_proc(proc)


# ------------------------------------------------------------------------------------------------------ recuperacao
def events_of(proc, name="RUN-x"):
    return [r["event"] for r in sched.Audit(proc, proc.parent / sched.ANCHOR_DIR, name).records()]


def run_recover(fake, proc, rows, **over):
    kwargs = dict(ops=fake, table_fn=lambda: rows, grace_s=1, sleep=lambda s: None, clock=lambda: 1_700_000_000, exclude=set())
    kwargs.update(over)
    return containment.recover(proc, None, "RUN-x", **kwargs)


def test_recovery_with_nothing_alive_preserves_evidence_and_adds_no_audit_noise(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    for pid in (900, 901, 902):
        fake.end(pid)
    result = run_recover(fake, proc, rows)
    assert result["owned_alive_after"] == 0 and result["blocked"] == [] and result["snapshot"]["files"] >= 2
    assert events_of(proc) == [] and fake.terminated == [] and not (proc / detach.STOP).exists()


def test_recovery_prefers_the_graceful_stop_and_never_forces_a_process_that_obeys(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)

    def obey(seconds):
        if (proc / detach.STOP).exists():
            for pid in (901, 902, 900):
                fake.end(pid)

    result = run_recover(fake, proc, rows, sleep=obey)
    assert result["owned_alive_after"] == 0 and result["blocked"] == [] and (proc / detach.STOP).exists() and fake.terminated == []
    assert result["actions"] == ["STOP"]
    events = events_of(proc)
    assert events[:2] == ["RECOVERY_STARTED", "EVIDENCE_SNAPSHOT"] and events.count("ORPHAN_FOUND") == 3
    assert "ORPHAN_STOP_REQUESTED" in events and events.count("ORPHAN_STOPPED") == 3 and "ORPHAN_TERMINATED" not in events and events[-1] == "RECOVERY_DONE"
    assert sched.Audit(proc, proc.parent / sched.ANCHOR_DIR, "RUN-x").verify()["ok"]


def test_recovery_without_confirmation_stops_gracefully_but_never_forces(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    result = run_recover(fake, proc, rows, confirm=False)                              # os processos ignoram o STOP
    assert result["owned_alive_after"] == 3 and fake.terminated == [] and any("confirmacao" in b for b in result["blocked"])
    events = events_of(proc)
    assert "ORPHAN_STOP_REQUESTED" in events and "ORPHAN_TERMINATED" not in events and events[-1] == "RECOVERY_BLOCKED"


def test_confirmed_recovery_forces_only_after_the_grace_and_ends_with_zero_alive(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.add(950, created=530, ppid=901, name="docker.exe")
    result = run_recover(fake, proc, rows, confirm=True)
    assert result["owned_alive_after"] == 0 and result["blocked"] == []
    order = [pid for pid, _ in fake.terminated]
    assert order.index(950) < order.index(901) < order.index(902) < order.index(900)
    events = events_of(proc)
    assert events.index("ORPHAN_STOP_REQUESTED") < events.index("ORPHAN_TERMINATED") and events.count("ORPHAN_TERMINATED") == 4 and events[-1] == "RECOVERY_DONE"
    assert "ORPHAN_STOPPED" not in events                                                                  # ninguem obedeceu ao STOP


def test_a_process_that_survives_forcing_leaves_the_recovery_blocked(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.refuse_terminate.add(901)
    result = run_recover(fake, proc, rows, confirm=True)
    assert result["owned_alive_after"] >= 1 and result["blocked"] and events_of(proc)[-1] == "RECOVERY_BLOCKED"
    assert any("PID 901" in b and "TerminateProcess falhou" in b for b in result["blocked"])                 # a causa original e preservada


def test_unknown_identity_blocks_the_recovery_before_any_stop_or_kill(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.deny(901)
    result = run_recover(fake, proc, rows, confirm=True)
    assert fake.terminated == [] and not (proc / detach.STOP).exists() and result["blocked"]
    assert events_of(proc)[-1] == "RECOVERY_BLOCKED" and "ORPHAN_STOP_REQUESTED" not in events_of(proc)


def test_a_non_owned_lookalike_blocks_everything_and_is_never_touched(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.add(700, created=600, ppid=1, name="python.exe")
    rows.append({"pid": 700, "ppid": 1, "name": "python.exe", "cmd": f"python {marker}"})
    result = run_recover(fake, proc, rows, confirm=True)
    assert fake.terminated == [] and fake.procs[700]["alive"] is True and result["summary"]["suspects"] == 1
    assert events_of(proc)[-1] == "RECOVERY_BLOCKED"


def test_a_process_of_another_run_is_ignored_and_the_owned_ones_are_still_recovered(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    other = MARKER.format(proc=tmp_path / "OUTRO.proc")
    fake.add(800, created=700, ppid=1, name="python.exe")
    rows.append({"pid": 800, "ppid": 1, "name": "python.exe", "cmd": f'python {other}'})
    result = run_recover(fake, proc, rows, confirm=True)
    assert result["owned_alive_after"] == 0 and fake.procs[800]["alive"] is True and 800 not in [pid for pid, _ in fake.terminated]


def test_evidence_is_preserved_before_any_action_and_a_failure_stops_everything(tmp_path, monkeypatch):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    monkeypatch.setattr(containment, "snapshot_proc", lambda *a, **k: (_ for _ in ()).throw(OSError("sem espaco")))
    result = run_recover(fake, proc, rows, confirm=True)
    assert result["owned_alive_after"] is None and "evidencia nao preservada" in result["blocked"][0]
    assert fake.terminated == [] and fake.opened == [] and not (proc / detach.STOP).exists() and events_of(proc) == []


def test_the_snapshot_precedes_the_stop_request(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    order = []
    real = containment.snapshot_proc
    containment.snapshot_proc = lambda *a, **k: order.append("snapshot") or real(*a, **k)
    try:
        run_recover(fake, proc, rows, confirm=False, sleep=lambda s: order.append("stop" if (proc / detach.STOP).exists() else "wait"))
    finally:
        containment.snapshot_proc = real
    assert order[0] == "snapshot" and "stop" in order


def test_an_audit_that_cannot_be_written_blocks_the_recovery_before_acting(tmp_path, monkeypatch):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    monkeypatch.setattr(sched.Audit, "append", lambda self, event, payload=None: (_ for _ in ()).throw(OSError("trilha cheia")))
    result = run_recover(fake, proc, rows, confirm=True)
    assert fake.terminated == [] and not (proc / detach.STOP).exists() and any("trilha nao gravou" in b for b in result["blocked"])


def test_recovery_does_not_depend_on_the_task_existing(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    result = run_recover(fake, proc, rows, confirm=True, table_fn=lambda: rows)
    assert result["owned_alive_after"] == 0                                         # nenhuma consulta ao Scheduler foi necessaria


def test_owned_summary_closes_every_handle_and_a_run_without_ledger_owns_nothing(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    summary = containment.owned_summary(proc, None, ops=fake, table_fn=lambda: rows, exclude=set())
    assert summary["alive"] == 3 and summary["unknown"] == 0 and sorted(fake.closed) == [900, 901, 902]
    assert [a[2] for a in summary["alive_pids"]] == ["P1", "P1", "P1"]
    bare = tmp_path / "BARE.proc"
    bare.mkdir()
    untouched = FakeOS()
    assert containment.owned_summary(bare, None, ops=untouched) == {"alive": 0, "unknown": 0, "suspects": 0, "alive_pids": [], "unknown_detail": [],
                                                                    "suspect_detail": []}
    assert untouched.opened == []
    fake.deny(901)
    unknown = containment.owned_summary(proc, None, ops=fake, table_fn=lambda: rows, exclude=set())
    assert unknown["unknown"] == 1 and unknown["unknown_detail"][0]["pid"] == 901


def test_owned_summary_with_an_unavailable_table_marks_live_ledger_processes_unknown(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)

    def broken():
        raise sched.SchedError("sem tabela")

    summary = containment.owned_summary(proc, None, ops=fake, table_fn=broken, exclude=set())
    assert summary["alive"] == 0 and summary["unknown"] == 3


# ------------------------------------------------------------------------------------------------------ SO real (somente filhos do teste)
def spawn(code="import time; time.sleep(120)"):
    return subprocess.Popen([sys.executable, "-c", code], creationflags=0x08000000)


def test_real_handles_report_creation_and_alive_state_and_terminate_only_their_own_process():
    child = spawn()
    try:
        opened = watch.open_native(child.pid)
        handle = opened["handle"]
        assert opened["state"] == watch.ALIVE and handle.created() == opened["created"] == watch.query_native(child.pid)["created"]
        assert handle.alive() is True
        assert handle.terminate() is True
        child.wait(timeout=30)
        assert handle.alive() is False and child.poll() is not None
        handle.close()
        handle.close()                                                              # idempotente
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=30)
    assert watch.open_native(0x7FFFFFF0)["state"] == watch.ABSENT
    assert watch.open_native(4)["state"] == watch.UNKNOWN
    for bad in (0, -5, None, True, "9"):
        assert watch.open_native(bad)["state"] == watch.UNKNOWN
    me = watch.open_native(os.getpid())
    try:
        assert me["state"] == watch.ALIVE and me["handle"].alive() is True
    finally:
        me["handle"].close()


def test_real_recovery_ends_only_the_owned_child_and_leaves_a_decoy_untouched(tmp_path):
    proc = tmp_path / "REAL-x.proc"
    proc.mkdir()
    token = "marcador_real_exato_g4"
    owned_child = spawn(f"import time; time.sleep(120)  # {token}")
    decoy = spawn("import time; time.sleep(120)  # outro processo qualquer")
    try:
        ident = watch.identity_of(owned_child.pid)
        detach._atomic_write(proc / containment.IDENTITY_JSON, {"schema": 1, "chain": [{"role": "interpreter", "pid": owned_child.pid,
                                                                                         "created": ident["created"], "name": "python.exe"}]})
        (proc / detach.LAUNCH).write_text(json.dumps({"run_id": "REAL-x", "owner_marker": token}), encoding="utf-8")
        rows = [{"pid": owned_child.pid, "ppid": os.getpid(), "name": "python.exe", "cmd": f"python -c import time; time.sleep(120)  # {token}"},
                {"pid": decoy.pid, "ppid": os.getpid(), "name": "python.exe", "cmd": "python -c import time; time.sleep(120)  # outro processo qualquer"}]
        result = containment.recover(proc, None, "REAL-x", ops=watch.NativeOps, table_fn=lambda: rows, confirm=True, grace_s=0.2, exclude={os.getpid()})
        assert result["owned_alive_after"] == 0 and result["blocked"] == []
        owned_child.wait(timeout=30)
        assert owned_child.poll() is not None and decoy.poll() is None              # o decoy NAO foi tocado
    finally:
        for process in (owned_child, decoy):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=30)


# ------------------------------------------------------------------------------------------------------ fronteiras extras (mutantes)
def test_an_identity_file_of_another_schema_is_ignored(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    assert containment.read_identity(proc)["schema"] == 1
    doc = json.loads((proc / containment.IDENTITY_JSON).read_text())
    doc["schema"] = 2
    (proc / containment.IDENTITY_JSON).write_text(json.dumps(doc), encoding="utf-8")
    assert containment.read_identity(proc) is None and containment.ledger_entries(proc) == []


def test_the_guard_evidence_keeps_the_first_ended_reading_and_the_thread_stops_after_the_loss():
    fake = FakeOS().add(900, created=500)
    times = iter([100, 105, 110, 115])
    guard = containment.Guard({"pid": 900, "created": 500}, ops=fake, clock=lambda: next(times))
    fake.end(900)
    guard.step()
    guard.step()
    assert guard.lost["first_seen_ended_t"] == 100 and guard.lost["confirmed_t"] == 105 and guard.lost["samples"] == 2
    lost = threading.Event()
    fake2 = FakeOS().add(900, created=500)
    threaded = containment.Guard({"pid": 900, "created": 500}, ops=fake2, interval_s=0.01, on_lost=lambda ev: lost.set())
    threaded.start()
    fake2.end(900)
    assert lost.wait(timeout=10)
    time.sleep(0.1)
    count = threaded.samples
    time.sleep(0.1)
    assert threaded.samples == count                                                  # depois do gatilho a thread para de amostrar
    threaded.stop()


def test_the_ledger_ignores_incomplete_entries_and_keeps_one_pid_with_two_creations(tmp_path):
    proc = tmp_path / "L.proc"
    proc.mkdir()
    detach._atomic_write(proc / containment.IDENTITY_JSON, {"schema": 1, "chain": [
        {"role": "interpreter", "pid": 5, "created": 10, "name": "a"}, {"role": "launcher", "pid": 6, "created": None, "name": "b"},
        {"role": "engine", "pid": "7", "created": 3, "name": "c"}, "lixo", {"role": "x", "pid": 8, "created": True}]})
    (proc / "facts.json").write_text(json.dumps({"lineage": {"self": {"pid": 5, "created": 11, "name": "a"}, "parent": {"pid": 6}, "grandparent": None}}),
                                     encoding="utf-8")
    assert [(e["pid"], e["created"]) for e in containment.ledger_entries(proc)] == [(5, 10), (5, 11)]


def test_excluded_descendants_equal_creation_and_only_live_python_or_cmd_lookalikes(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.add(950, created=520, ppid=901, name="a.exe")                                # criacao IGUAL a do pai: filho legitimo (>=)
    fake.add(960, created=600, ppid=901, name="b.exe")                                # esta no conjunto excluido (arvore do inspetor)
    fake.add(700, created=600, ppid=1, name="notepad.exe")
    fake.add(701, created=600, ppid=1, name="python.exe")
    fake.end(701)
    rows += [{"pid": 700, "ppid": 1, "name": "notepad.exe", "cmd": f"x {marker}"}, {"pid": 701, "ppid": 1, "name": "python.exe", "cmd": f"python {marker}"}]
    owned = owned_of(fake, proc, marker, rows, exclude={960})
    try:
        pids = {e["pid"] for e in owned["alive"]}
        assert 950 in pids and 960 not in pids and owned["suspects"] == [] and owned["unknown"] == []
    finally:
        containment.close_owned(owned)


def test_real_open_native_reports_absent_for_an_exited_process_whose_handle_is_still_held():
    child = spawn("pass")
    child.wait(timeout=30)                                                              # o Popen ainda segura um handle: o PID existe, o processo nao
    assert watch.open_native(child.pid)["state"] == watch.ABSENT


def test_forced_termination_orders_by_depth_even_when_the_ledger_lists_the_root_first(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    doc = containment.read_identity(proc)
    doc["chain"] = list(reversed(doc["chain"]))                                      # cmd, launcher, interpretador: raiz primeiro
    detach._atomic_write(proc / containment.IDENTITY_JSON, doc)
    owned = owned_of(fake, proc, marker, rows)
    try:
        assert [e["pid"] for e in owned["entries"]] == [900, 902, 901]
        containment.terminate_owned(owned, confirm=True, sleep=lambda s: None)
        assert [pid for pid, _ in fake.terminated] == [901, 902, 900]               # a ordem vem da profundidade (ppid), nao da ordem do arquivo
    finally:
        containment.close_owned(owned)


# ------------------------------------------------------------------------------------------------------ lacunas apontadas pelos mutantes
def test_finishing_stops_the_guard_thread_flag(tmp_path):
    fake, proc, guard, calls = make_containment(tmp_path)
    assert guard.guard._stop.is_set() is False
    guard.finish()
    assert guard.guard._stop.is_set() is True and guard._finished is True


def test_without_a_marker_even_a_present_command_line_cannot_prove_ownership(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    owned = containment.enumerate_owned(proc, ops=fake, table=rows, marker=None, exclude=set())
    try:
        assert owned["alive"] == [] and sorted(u["pid"] for u in owned["unknown"]) == [900, 901, 902]
        assert all("posse nao comprovada" in u["error"] for u in owned["unknown"]) and fake.closed.count(900) == 1
    finally:
        containment.close_owned(owned)


def test_recovery_reports_unknown_identities_even_when_nothing_else_is_alive(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)
    fake.end(900)
    fake.end(902)
    fake.deny(901)                                                                 # so resta uma identidade ilegivel: NUNCA 'tudo limpo'
    result = run_recover(fake, proc, rows, confirm=True)
    assert result["owned_alive_after"] == 0 and result["blocked"] and result["summary"]["unknown"] == 1
    assert fake.terminated == [] and events_of(proc)[-1] == "RECOVERY_BLOCKED" and not (proc / detach.STOP).exists()


def test_partial_obedience_forces_only_the_stubborn_processes_and_records_who_stopped_alone(tmp_path):
    fake, proc, marker, rows, _ = build_world(tmp_path)

    def only_the_interpreter_obeys(seconds):
        if (proc / detach.STOP).exists():
            fake.end(901)

    result = run_recover(fake, proc, rows, confirm=True, sleep=only_the_interpreter_obeys)
    forced = result["actions"][1]["forced"]
    assert forced["skipped"] == [] and sorted(forced["terminated"]) == [900, 902] and result["owned_alive_after"] == 0
    events = events_of(proc)
    assert events.count("ORPHAN_STOPPED") == 1 and events.count("ORPHAN_TERMINATED") == 2
    assert [pid for pid, _ in fake.terminated] == [902, 900]                                 # o 901 parou sozinho: nao foi encerrado a forca


def test_real_open_native_treats_a_handle_without_readable_times_as_unknown(monkeypatch):
    monkeypatch.setattr(watch.ProcHandle, "created", lambda self: None)
    assert watch.open_native(os.getpid())["state"] == watch.UNKNOWN
    monkeypatch.undo()
    monkeypatch.setattr(watch.ProcHandle, "alive", lambda self: None)
    assert watch.open_native(os.getpid())["state"] == watch.UNKNOWN
