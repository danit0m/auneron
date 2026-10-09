"""
SIM-1.5G.3 -- sonda (`collect2`), monitor e finish com a vigilia duravel, os codigos de saida da tabela unica e a honestidade da
auditoria. TUDO com duplos (Scheduler falso + SO falso): nenhuma tarefa real, nenhum processo real.
"""

from __future__ import annotations

import json
import os

import pytest

from sim.runner import detach
from sim.runner import detach_probe
from sim.runner import sched
from sim.runner import termination
from sim.runner import watch
from sim.tests.sched_support import probe_os
from sim.tests.test_runner_sched import Clock
from sim.tests.test_runner_sched import good_facts
from sim.tests.test_runner_sched import good_monitor
from sim.tests.test_runner_sched import probe_ports
from sim.tests.test_runner_sched import probe_proc
from sim.tests.test_runner_sched import probe_world
from sim.tests.test_runner_sched import run_monitor_and_finish
from sim.tests.test_runner_sched import write_probe_outputs


def armed_probe_proc(tmp_path, fake, *, with_initiator=True):
    """Pasta `.proc` com `watch.json` valido (alvos: este processo como lancador e, opcionalmente, o CLI iniciador)."""
    proc = probe_proc(tmp_path)
    targets = [watch.make_target("launcher", os.getpid(), fake.query)]
    if with_initiator:
        targets.append(watch.make_target("initiator", 7777, fake.query))
    data, _ = watch.build_watch(targets, [], armed_t=1000)
    (proc / watch.WATCH_JSON).write_bytes(data)
    return proc


# ------------------------------------------------------------------------------------------------------ a sonda
def test_the_probe_watches_its_targets_every_beat_and_records_when_each_ended(tmp_path):
    fake, clock = probe_os(), Clock()
    proc = armed_probe_proc(tmp_path, fake)

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.t == 1012:
            fake.end(os.getpid())                                      # o lancador termina
        if clock.t == 1031:
            fake.end(7777)                                             # depois, o CLI iniciador
        if clock.t >= 1060:
            (proc / detach.STOP).write_text("go")

    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=probe_ports(query=fake.query), sleep=sleep, clock=clock)
    summary = watch.summarize(proc)
    assert facts["watch_armed"] is True and facts["watch_write_errors"] == 0 and summary["integrity_ok"] is True
    assert summary["ended"]["launcher"]["first_seen_ended_t"] == 1015 and summary["ended"]["launcher"]["last_seen_alive_t"] == 1010
    assert summary["ended"]["initiator"]["first_seen_ended_t"] == 1035 and summary["ended"]["initiator"]["last_seen_alive_t"] == 1030
    assert summary["ended"]["initiator"]["period_s"] == detach_probe.BEAT_EVERY_S
    assert facts["lineage"]["self"]["pid"] == 901 and facts["exit_code"] == 0


def test_a_probe_without_watch_json_runs_but_reports_the_watch_as_not_armed(tmp_path):
    proc, clock = probe_proc(tmp_path), Clock()
    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=10, ports=probe_ports(), sleep=clock.sleep, clock=clock)
    assert facts["watch_armed"] is False and facts["watch_write_errors"] == 0 and not (proc / watch.WATCH_LOG).exists()
    assert "D2-08_survives_launcher" in detach_probe.judge_detach2(facts, {**good_monitor(), "watch": {"armed": False}}, "L1")["failed"]


def test_watch_write_failures_are_counted_and_make_the_evidence_unusable(tmp_path):
    fake, clock = probe_os(), Clock()
    proc = armed_probe_proc(tmp_path, fake, with_initiator=False)
    boom = lambda path, doc: (_ for _ in ()).throw(OSError("disco cheio"))                                # noqa: E731
    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=20, ports=probe_ports(query=fake.query, watch_write=boom),
                                  sleep=clock.sleep, clock=clock)
    assert facts["watch_write_errors"] >= 1 and facts["exit_code"] == 4                    # a sonda segue (TIMEOUT), a evidencia nao presta
    assert "D2-08_survives_launcher" in detach_probe.judge_detach2(facts, good_monitor(), "L1")["failed"]


def test_a_failure_to_write_exit_json_is_never_success_and_keeps_the_original_reason(tmp_path, monkeypatch):
    proc, clock = probe_proc(tmp_path), Clock()
    (proc / detach.STOP).write_text("go")
    monkeypatch.setattr(detach, "write_exit", lambda *a, **k: (_ for _ in ()).throw(PermissionError("acesso negado")))
    facts = detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=probe_ports(), sleep=clock.sleep, clock=clock)
    assert facts["exit_code"] == termination.EXIT_CODES["EVIDENCE_FAILURE"] == detach_probe.process_exit_code(facts) == 5
    assert facts["exit_reason"] == "STOP" and facts["stop_obeyed"] is True and "acesso negado" in facts["exit_write_error"]
    assert not (proc / detach.EXIT).exists()
    assert termination.classify_termination(None, 5, 5)["class"] == "EVIDENCE_WRITE_FAILURE"
    verdict = detach_probe.judge_detach2({**good_facts(), "exit_write_error": facts["exit_write_error"]}, good_monitor(), "L1")
    assert verdict["failed"] == ["D2-09_stop_exit_clean"]


def test_an_exception_after_the_terminal_record_does_not_overwrite_it_and_the_process_still_fails(tmp_path, monkeypatch):
    proc, clock = probe_proc(tmp_path), Clock()
    (proc / detach.STOP).write_text("go")
    real = detach_probe.FactsWriter.record

    def record(self, phase, **values):
        if phase == "exit":
            raise OSError("facts nao gravou")
        return real(self, phase, **values)

    monkeypatch.setattr(detach_probe.FactsWriter, "record", record)
    with pytest.raises(OSError, match="facts nao gravou"):
        detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=probe_ports(), sleep=clock.sleep, clock=clock)
    saved = json.loads((proc / detach.EXIT).read_text())
    assert saved["reason"] == "STOP"                                                       # nao sobrescrito por EXCEPTION
    assert termination.classify_termination(saved, 1, 1)["class"] == "INCONSISTENT_SIGNALS"


def test_a_failed_terminal_write_followed_by_an_exception_is_retried_and_reports_the_secondary_failure(tmp_path, monkeypatch, capsys):
    proc, clock = probe_proc(tmp_path), Clock()
    (proc / detach.STOP).write_text("go")
    monkeypatch.setattr(detach, "write_exit", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio")))
    real = detach_probe.FactsWriter.record

    def record(self, phase, **values):
        if phase == "exit":
            raise OSError("facts nao gravou")
        return real(self, phase, **values)

    monkeypatch.setattr(detach_probe.FactsWriter, "record", record)
    with pytest.raises(OSError, match="facts nao gravou"):
        detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=probe_ports(), sleep=clock.sleep, clock=clock)
    assert "SECUNDARIO" in capsys.readouterr().err                                         # o termino nao tinha sido gravado: tenta de novo


def test_an_exception_whose_exit_json_cannot_be_written_reports_the_secondary_failure(tmp_path, monkeypatch, capsys):
    proc, clock = probe_proc(tmp_path), Clock()
    monkeypatch.setattr(detach, "write_exit", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio")))

    def dies(cmd):
        raise RuntimeError("causa original")

    with pytest.raises(RuntimeError, match="causa original"):
        detach_probe.collect2(proc, interactive_session=1, cap_s=600, ports=probe_ports(run=dies), sleep=clock.sleep, clock=clock)
    assert "SECUNDARIO" in capsys.readouterr().err and not (proc / detach.EXIT).exists()


@pytest.mark.parametrize("facts,expected", [({"exit_code": 0}, 0), ({"exit_code": 1}, 1), ({"exit_code": 2}, 2), ({"exit_code": 4}, 4),
                                            ({"exit_code": 5}, 5), ({"exit_code": 6}, 6), ({"exit_code": 7}, 7), ({"exit_code": 3}, 1), ({"exit_code": 8}, 1),
                                            ({"exit_code": True}, 1),
                                            ({"exit_code": "0"}, 1), ({"exit_code": None}, 1), ({}, 1), (None, 1), ("x", 1)])
def test_the_process_exit_code_comes_from_the_table_and_a_missing_code_is_never_zero(facts, expected):
    assert detach_probe.process_exit_code(facts) == expected


def test_the_l2_cap_is_6000_seconds_and_the_scheduler_limit_stays_two_hours(tmp_path):
    assert detach_probe.LEVELS["L2"] == {"required_s": 2700, "survive_s": 600, "profile": "PROBE_L2", "cap_s": 6000}
    assert detach_probe.LEVELS["L1"]["cap_s"] == 1500 and sched.LIMITS["PROBE_L2"] == "PT2H"
    host, name, doc, proc = probe_world(tmp_path, "L2")
    assert "--cap-s 6000" in sched.parse_task_xml(host.tasks[name]["xml"])["execs"][0]["Arguments"] and doc["limit"] == "PT2H"
    assert detach_probe.LEVELS["L2"]["cap_s"] < 7200                               # o teto da sonda cabe no limite do Scheduler


@pytest.mark.parametrize("limit,seconds", [("PT2H", 7200), ("PT30M", 1800), ("PT1H30M", 5400), ("PT", None), ("", None), (None, None), ("P1D", None),
                                           ("PT5S", None)])
def test_the_scheduler_limit_is_parsed_in_seconds_or_not_at_all(limit, seconds):
    assert detach_probe._limit_seconds(limit) == seconds


# ------------------------------------------------------------------------------------------------------ o monitor e a auditoria
def test_the_audit_never_claims_a_stop_that_was_not_requested(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc, probe_dies=True)
    records = sched.Audit(proc, tmp_path / sched.ANCHOR_DIR, name).records()
    events = [r["event"] for r in records]
    assert "PROBE_ENDED_BEFORE_MONITOR" in events and "STOPPED" not in events and "STOP_REQUESTED" not in events
    assert monitor["stop"] == {"t": None, "requester": []} and monitor["probe_alive_before_stop"] is False
    assert sched.recorded_state(records) == "UNREGISTERED"                      # o finish removeu a tarefa; o STOPPED nunca foi afirmado


def test_a_stop_that_never_produces_exit_json_is_recorded_as_not_confirmed(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    write_probe_outputs(proc, "L1")
    monitor = detach_probe.probe_monitor(tmp_path, name, level="L1", run=host, alive=host.alive, sleep=lambda s: None, clock=lambda: 3000,
                                         poll_s=1, stop_wait_s=2, query=host.os.query, snapshot=host.os.snapshot)
    events = [r["event"] for r in sched.Audit(proc, tmp_path / sched.ANCHOR_DIR, name).records()]
    assert "STOP_REQUESTED" in events and "STOP_NOT_CONFIRMED" in events and "STOPPED" not in events
    assert monitor["exit"] is None and monitor["termination"]["class"] == "UNKNOWN"


def test_the_monitor_classifies_the_old_timeout_evidence_as_inconsistent_signals(tmp_path):
    """Os tres sinais da tentativa L2 expirada (exit.json 1/TIMEOUT, exit_code.txt 0, LastTaskResult 0) nunca sao encerramento limpo."""
    host, name, doc, proc = probe_world(tmp_path)

    def stale(p):
        (p / detach.EXIT).write_text(json.dumps({"code": 1, "reason": "TIMEOUT", "t_epoch": 5}))
        (p / detach.EXIT_CODE).write_text("0\r\n")
        host.tasks[name].update(state=sched.STATE_READY, last=0)

    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc, probe_dies=True, tweak=stale)
    assert monitor["termination"]["class"] == "INCONSISTENT_SIGNALS" and monitor["termination"]["success"] is False
    assert {"D2-08_survives_launcher", "D2-09_stop_exit_clean"} <= set(final["verdict"]["failed"])


def test_the_monitor_names_timeout_forced_end_and_scheduler_limit_only_with_their_own_evidence(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)

    def timeout(p):
        (p / detach.EXIT).write_text(json.dumps({"code": 4, "reason": "TIMEOUT", "t_epoch": 5}))
        (p / detach.EXIT_CODE).write_text("4\r\n")
        host.tasks[name].update(state=sched.STATE_READY, last=4)

    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc, probe_dies=True, tweak=timeout)
    assert monitor["termination"]["class"] == "TIMEOUT" and final["verdict"]["verdict"] == "FAIL"
    # limite do Scheduler: sem arquivos, 0x41306 e tempo >= PT30M (clock do monitor 3000, lancamento 1000 => 2000 s >= 1800 s)
    host2, name2, doc2, proc2 = probe_world(tmp_path / "limit")

    def terminated(p):
        host2.tasks[name2].update(state=sched.STATE_READY, last=termination.TASK_TERMINATED)

    monitor2, _ = run_monitor_and_finish(tmp_path / "limit", host2, name2, proc2, probe_dies=True, tweak=terminated)
    assert monitor2["termination"]["class"] == "SCHEDULER_LIMIT"
    # com FORCED_END na trilha, o mesmo resultado e fim forcado do operador, nunca limite
    host3, name3, doc3, proc3 = probe_world(tmp_path / "forced")
    sched.Audit(proc3, tmp_path / "forced" / sched.ANCHOR_DIR, name3).append("FORCED_END", {"by": "operator", "confirm": True})

    def terminated3(p):
        host3.tasks[name3].update(state=sched.STATE_READY, last=termination.TASK_TERMINATED)

    monitor3, _ = run_monitor_and_finish(tmp_path / "forced", host3, name3, proc3, probe_dies=True, tweak=terminated3)
    assert monitor3["termination"]["class"] == "FORCED_END"
    # sem 0x41306 nem FORCED_END: nada e inferido
    host4, name4, doc4, proc4 = probe_world(tmp_path / "unknown")
    monitor4, _ = run_monitor_and_finish(tmp_path / "unknown", host4, name4, proc4, probe_dies=True)
    assert monitor4["termination"]["class"] in ("UNKNOWN", "INCOMPLETE_EVIDENCE") and monitor4["termination"]["success"] is False


def test_audit_write_failures_in_the_monitor_are_recorded_and_fail_d2_11(tmp_path, monkeypatch):
    host, name, doc, proc = probe_world(tmp_path)
    real = sched.Audit.append

    def flaky(self, event, payload=None):
        if event == "STOP_REQUESTED":
            raise OSError("trilha cheia")
        return real(self, event, payload)

    monkeypatch.setattr(sched.Audit, "append", flaky)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert monitor["audit_write_failures"] == [{"event": "STOP_REQUESTED", "error": "OSError: trilha cheia"}]
    assert final["verdict"]["failed"] == ["D2-11_removed_clean_evidence"]


def test_a_removal_that_cannot_write_its_evidence_is_never_success_and_keeps_the_cause(tmp_path, monkeypatch):
    host, name, doc, proc = probe_world(tmp_path)
    monkeypatch.setattr(sched, "remove_verified", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio")))
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert final["removal"]["error"].startswith("OSError: disco cheio") and "D2-11_removed_clean_evidence" in final["verdict"]["failed"]
    assert name in host.tasks                                                          # a tarefa fica registrada: o P15 bloqueia ate reconciliar


def test_new_evidence_needs_its_watch_files_to_be_complete(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    monitor, final = run_monitor_and_finish(tmp_path, host, name, proc)
    assert detach_probe.verify_evidence(proc) is True
    for victim in (watch.WATCH_JSON, watch.WATCH_LOG):
        (proc / victim).rename(proc / (victim + ".moved"))
        assert detach_probe.verify_evidence(proc) is False
        (proc / (victim + ".moved")).rename(proc / victim)
    assert detach_probe.verify_evidence(proc) is True


def test_tampering_with_the_watch_between_monitor_and_finish_fails_instead_of_trusting_the_stored_monitor(tmp_path):
    host, name, doc, proc = probe_world(tmp_path)
    write_probe_outputs(proc, "L1")

    def sleep(seconds):
        if (proc / detach.STOP).exists() and not (proc / detach.EXIT).exists():
            (proc / detach.EXIT).write_text(json.dumps({"code": 0, "reason": "STOP", "t_epoch": 1}))
            (proc / detach.EXIT_CODE).write_text("0\n")
            host.tasks[name].update(state=sched.STATE_READY, instances=[], last=0)
            host.alive_pids -= {host.engine_pid, host.probe_pid, host.launcher_pid}

    monitor = detach_probe.probe_monitor(tmp_path, name, level="L1", run=host, alive=host.alive, sleep=sleep, clock=lambda: 3000, poll_s=1,
                                         query=host.os.query, snapshot=host.os.snapshot)
    assert monitor["evidence_version"] == "v2" and monitor["launcher_watch"]["first_seen_ended_t"] == 2992
    lines = [json.loads(line) for line in (proc / watch.WATCH_LOG).read_text().splitlines()]
    for line in lines:
        if line["event"] == "ENDED":
            line["first_seen_ended_t"] = 5000                                    # empurra o fim do lancador para perto do ultimo batimento
    (proc / watch.WATCH_LOG).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in lines))
    final = detach_probe.probe_finish(tmp_path, name, level="L1", run=host, alive=host.alive, clock=lambda: 3100)
    assert "D2-08_survives_launcher" in final["verdict"]["failed"]


# ------------------------------------------------------------------------------------------------------ fronteiras dos limiares
@pytest.mark.parametrize("level,survive", [("L1", 840), ("L2", 600)])
def test_d2_08_thresholds_are_inclusive_and_unchanged(level, survive):
    for value, expected in ((survive, True), (survive - 1, False), (survive + 1, True)):
        monitor = good_monitor(level)
        monitor["beats"]["survived_durable_s"] = value
        assert ("D2-08_survives_launcher" in detach_probe.judge_detach2(good_facts(), monitor, level)["passed"]) is expected, value
    assert detach_probe.LEVELS[level]["survive_s"] == survive


def test_d2_12_boundaries_are_inclusive_for_the_gap_and_the_time_after_the_end():
    def passes(**changes):
        monitor = good_monitor("L2")
        monitor["session_end"].update(changes)
        return "D2-12_session_end_continuity" in detach_probe.judge_detach2(good_facts(), monitor, "L2")["passed"]

    assert passes(gap_at_cross_s=15) is True and passes(gap_at_cross_s=16) is False
    assert passes(survived_after_end_s=600) is True and passes(survived_after_end_s=599) is False
    assert detach_probe.MAX_GAP_S == 15


def test_a_launch_whose_audit_cannot_record_the_watch_never_starts_the_task(tmp_path, monkeypatch):
    from sim.tests.sched_support import FakeHost

    host, fake = FakeHost(tmp_path), probe_os()
    real = sched.Audit.append

    def flaky(self, event, payload=None):
        if event == "WATCH_ARMED":
            raise OSError("trilha cheia")
        return real(self, event, payload)

    monkeypatch.setattr(sched.Audit, "append", flaky)
    with pytest.raises(detach.DetachError, match="WATCH_ARMED"):
        detach_probe.probe_launch(tmp_path, "DETACH2-PROBE-L1-20261008T120000Z", level="L1", python="C:\\v\\python.exe", repo="C:\\repo",
                                  interactive_session=1, run=host, alive=host.alive, sleep=lambda s: None, clock=lambda: 1000, query=fake.query)
    assert "start" not in host.labels() and "register" in host.labels()                    # registrada, nunca iniciada (o P15 a acusa)


def test_the_nh_run_phase_records_its_exit_through_the_single_contract_function():
    import inspect

    from sim.runner import nh_phases

    source = inspect.getsource(nh_phases.phase_run)
    assert "detach.run_and_record_exit(proc, nh.run, cleanup, lost=lambda: bool(guard is not None and guard.lost))" in source
    assert "detach.write_exit(" not in source and "start_containment(proc, stop)" in source and "guard.finish()" in source
