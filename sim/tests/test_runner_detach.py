"""
SIM-1.5F -- lancador WMI, monitor, STOP/kill, heartbeat, saida inesperada e a sonda DETACH-1, TUDO com duplos:
nenhum processo real (nem destacado) e criado por estes testes.
"""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from sim.runner import cli
from sim.runner import detach
from sim.runner import detach_probe
from sim.runner import host
from sim.runner.errors import OperatorStop
from sim.runner.ledger import RunLedger
from sim.tests.runner_support import make_runner
from sim.tests.runner_support import services_for
from sim.tests.runner_support import setup_run

SLOTS = [(0, "09:00"), (1, "09:00"), (2, "11:00"), (3, "10:00")]


@pytest.fixture(autouse=True)
def no_real_process(monkeypatch):
    """Garantia da emenda: nenhum teste deste arquivo cria processo real."""
    def boom(*args, **kwargs):
        raise AssertionError(f"processo real proibido nos testes: {args[:1]}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


class FakeWmi:
    def __init__(self, rv=0, pid=4321, writes_heartbeat=True, proc=None):
        self.rv, self.pid, self.writes_heartbeat, self.proc, self.calls = rv, pid, writes_heartbeat, proc, []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.writes_heartbeat and self.proc is not None:
            (self.proc / detach.HEARTBEAT).write_text(json.dumps({"pid": self.pid + 1, "t_epoch": 1000, "t_real": "x", "status": {}}))
        return SimpleNamespace(stdout=f"RV={self.rv} PID={self.pid}\n", stderr="", returncode=0)


def launch_args(tmp_path, **over):
    base = dict(runs_root=tmp_path, run_id="RUN-aaaaaaaaaaaa-A01", python="C:\\py\\python.exe", repo="C:\\repo",
                cli_args="run --run-id X", env={"AUNERON_SIM_HOME": "C:\\home"}, ledger_state="PREFLIGHTED",
                python_info={"version": "3.11.9"}, alive=lambda pid: True, sleep=lambda s: None, clock=lambda: 1000)
    base.update(over)
    return base


# ------------------------------------------------------------------------------------------------- lancador
def test_launch_uses_wmi_with_the_command_line_in_the_environment_and_records_everything(tmp_path):
    proc = detach.proc_dir(tmp_path, "RUN-aaaaaaaaaaaa-A01")
    proc.mkdir(parents=True)
    wmi = FakeWmi(proc=proc)
    doc = detach.launch(**launch_args(tmp_path), run=wmi)
    cmd, kwargs = wmi.calls[0]
    assert cmd[0] == "powershell.exe" and "Win32_Process" in cmd[-1] and "Invoke-CimMethod" in cmd[-1]
    assert kwargs["env"]["SIM_CWD"] == "C:\\repo" and "-m sim.runner.cli run --run-id X" in kwargs["env"]["SIM_CMDLINE"]
    assert "Start-Process" not in cmd[-1] and "schtasks" not in " ".join(cmd).lower()
    saved = json.loads((proc / detach.LAUNCH).read_text())
    assert saved["pid"] == 4321 and saved["mechanism"] == "wmi.Win32_Process.Create" and saved["env"] == {"AUNERON_SIM_HOME": "C:\\home"}
    assert saved["python"] == "C:\\py\\python.exe" and saved["python_info"] == {"version": "3.11.9"}
    assert doc["heartbeat_pid"] == 4322


def test_command_line_redirects_logs_and_records_the_exit_code(tmp_path):
    line = detach.build_cmdline("C:\\py\\python.exe", "run --run-id X", tmp_path)
    assert line.startswith("cmd.exe /d /v:on /s /c ") and "stdout.log" in line and "stderr.log" in line and "exit_code.txt" in line
    assert "-m sim.runner.cli run --run-id X" in line


def test_only_a_preflighted_run_is_launched_and_only_once(tmp_path):
    ticks = iter(range(1000, 10**7, 20))                       # relogio que avanca: nunca pendura se a trava sumir
    for state in ("CREATED", "RUNNING", "COMPLETED", "ABORTED", None):
        with pytest.raises(detach.DetachError, match="PREFLIGHTED"):
            detach.launch(**launch_args(tmp_path, ledger_state=state, clock=lambda: next(ticks)), run=FakeWmi())
    proc = detach.proc_dir(tmp_path, "RUN-aaaaaaaaaaaa-A01")
    proc.mkdir(parents=True, exist_ok=True)
    detach.launch(**launch_args(tmp_path), run=FakeWmi(proc=proc))
    with pytest.raises(detach.DetachError, match="ja foi lancado"):
        detach.launch(**launch_args(tmp_path), run=FakeWmi(proc=proc))


@pytest.mark.parametrize("wmi,message", [(FakeWmi(rv=9), "WMI recusou"), (FakeWmi(writes_heartbeat=False), "heartbeat")])
def test_launch_fails_closed_when_wmi_refuses_or_the_process_never_beats(tmp_path, wmi, message):
    clock = iter(range(1000, 100000, 20))
    args = launch_args(tmp_path, clock=lambda: next(clock))
    with pytest.raises(detach.DetachError, match=message):
        detach.launch(**args, run=wmi)


def test_launch_rejects_garbage_from_wmi(tmp_path):
    garbage = lambda cmd, **k: SimpleNamespace(stdout="oops", stderr="", returncode=0)               # noqa: E731
    with pytest.raises(detach.DetachError, match="retorno inesperado"):
        detach.launch(**launch_args(tmp_path), run=garbage)


# ------------------------------------------------------------------------------------------------- monitor
def seed_proc(tmp_path, *, pid=10, beat_age=None, launched_ago=500, beat_pid=11):
    proc = detach.proc_dir(tmp_path, "RUN-aaaaaaaaaaaa-A01")
    proc.mkdir(parents=True, exist_ok=True)
    (proc / detach.LAUNCH).write_text(json.dumps({"pid": pid, "t_launch": 10_000 - launched_ago}))
    if beat_age is not None:
        (proc / detach.HEARTBEAT).write_text(json.dumps({"pid": beat_pid, "t_epoch": 10_000 - beat_age, "t_real": "x",
                                                         "status": {"day": 3}}))
    return proc


@pytest.mark.parametrize("kwargs,alive,ledger,state,action", [
    (dict(beat_age=10), True, "RUNNING", "RUNNING", None),
    (dict(beat_age=500), True, "RUNNING", "HUNG", "investigate"),
    (dict(beat_age=10), False, "RUNNING", "UNEXPECTED_EXIT", "seal-unclean"),
    (dict(beat_age=10), False, "COMPLETED", "TERMINAL", None),
    (dict(beat_age=10), True, "ABORTED", "TERMINAL", None),
    (dict(beat_age=None, launched_ago=5), True, "PREFLIGHTED", "STARTING", None),
    (dict(beat_age=None, launched_ago=500), True, "PREFLIGHTED", "HUNG", "investigate"),
    (dict(beat_age=None, launched_ago=500), False, "PREFLIGHTED", "UNEXPECTED_EXIT", "seal-unclean"),
])
def test_assess_classifies_running_hung_unexpected_exit_and_terminal(tmp_path, kwargs, alive, ledger, state, action):
    proc = seed_proc(tmp_path, **kwargs)
    verdict = detach.assess(proc, ledger, alive=lambda pid: alive, now=lambda: 10_000)
    assert (verdict["state"], verdict["action"]) == (state, action)


def test_assess_without_a_launch_and_a_hung_process_is_never_killed_by_the_monitor(tmp_path):
    proc = detach.proc_dir(tmp_path, "RUN-none")
    assert detach.assess(proc, "PREFLIGHTED")["state"] == "NOT_LAUNCHED"
    proc = seed_proc(tmp_path, beat_age=900)
    verdict = detach.assess(proc, "RUNNING", alive=lambda pid: True, now=lambda: 10_000)
    assert verdict["state"] == "HUNG" and "nada e encerrado" in verdict["note"]


def test_unexpected_exit_reports_the_exit_code_file(tmp_path):
    proc = seed_proc(tmp_path, beat_age=10)
    (proc / detach.EXIT_CODE).write_text("3\n")
    assert detach.assess(proc, "RUNNING", alive=lambda pid: False, now=lambda: 10_000)["exit_code"] == 3


# ------------------------------------------------------------------------------------------------- STOP e kill
def test_stop_creates_the_file_once_and_the_runner_aborts_cleanly_at_the_next_safe_point(tmp_path):
    run = setup_run(tmp_path)
    proc = detach.proc_dir(run.registry.root, run.run_id)
    stop = host.StopFlag(proc / detach.STOP)
    assert stop.requested is False
    seen = {"n": 0}

    def facts(day):
        from sim.tests.runner_support import good_day_facts

        seen["n"] += 1
        if seen["n"] == 2:
            detach.request_stop(proc, "manutencao")
        return good_day_facts()

    result = make_runner(run, services_for(run, slots=SLOTS, day_facts=facts), stop=stop).run()
    assert (result["state"], result["class"]) == ("ABORTED", "OPERATOR_STOP")
    assert run.calls["preserve"] == ["OPERATOR_STOP"] and RunLedger(run.run_dir).replay.last_closed == 1
    assert stop.reason == "STOP_FILE:manutencao"
    with pytest.raises(detach.DetachError, match="STOP ja solicitado"):
        detach.request_stop(proc, "outra vez")


def test_stop_file_is_checked_at_hook_boundaries_and_raises_operator_stop(tmp_path):
    flag = host.StopFlag(tmp_path / "STOP")
    assert not flag.requested
    (tmp_path / "STOP").write_text("x")
    assert flag.requested and flag.reason.startswith("STOP_FILE")
    assert issubclass(OperatorStop, Exception)


def test_kill_needs_explicit_confirmation_and_uses_taskkill_on_live_pids_only(tmp_path):
    proc = seed_proc(tmp_path, beat_age=10, pid=10, beat_pid=11)
    with pytest.raises(detach.DetachError, match="--confirm"):
        detach.kill(proc, confirm=False)
    calls = []
    run = lambda cmd, **k: calls.append(cmd) or SimpleNamespace(returncode=0)                         # noqa: E731
    report = detach.kill(proc, confirm=True, run=run, alive=lambda pid: pid == 11)
    assert calls == [["taskkill", "/PID", "11", "/T", "/F"]] and report["pids"] == [11, 10]
    assert json.loads((proc / detach.KILLED).read_text())["pids"] == [11, 10]


def test_killed_process_ends_as_unclean_exit_and_cannot_be_resumed(tmp_path):
    from sim.runner import runner as runner_mod
    from sim.runner.errors import ResumeRefused

    run = setup_run(tmp_path)
    dead = host.RunLock(run.run_dir, pid=lambda: 1, alive=lambda pid: False)
    dead.release = lambda: None
    with pytest.raises(KeyboardInterrupt):
        make_runner(run, services_for(run, slots=SLOTS, fail={"barrier": KeyboardInterrupt()}), lock=dead).run()
    proc = detach.proc_dir(run.registry.root, run.run_id)
    seed_proc_for = proc
    seed_proc_for.mkdir(parents=True, exist_ok=True)
    (proc / detach.LAUNCH).write_text(json.dumps({"pid": 10, "t_launch": 10_000 - 900}))
    verdict = detach.assess(proc, RunLedger(run.run_dir).state, alive=lambda pid: False, now=lambda: 10_000)
    assert verdict["action"] == "seal-unclean"
    assert runner_mod.seal_unclean_exit(run.run_dir, run.registry, alive=lambda pid: False) is True
    run.ledger = RunLedger(run.run_dir)
    with pytest.raises(ResumeRefused):
        make_runner(run, services_for(run, slots=SLOTS)).run()


def test_tail_and_exit_json(tmp_path):
    proc = tmp_path / "p"
    proc.mkdir()
    (proc / detach.STDOUT).write_text("\n".join(f"l{i}" for i in range(100)))
    assert detach.tail(proc, "stdout", 3) == ["l97", "l98", "l99"] and detach.tail(proc, "stderr") == []
    detach.write_exit(proc, 2, "ABORTED", clock=lambda: 7)
    assert json.loads((proc / detach.EXIT).read_text()) == {"code": 2, "reason": "ABORTED", "t_epoch": 7}


def test_heartbeat_writer_is_atomic_and_reports_the_runner_status(tmp_path):
    state = {"day": 4, "hook": "clock_set"}
    writer = host.HeartbeatWriter(tmp_path / "hb.json", lambda: state, clock=lambda: 5000, pid=lambda: 77)
    doc = writer.beat()
    saved = json.loads((tmp_path / "hb.json").read_text())
    assert saved == doc and saved["pid"] == 77 and saved["status"] == {"day": 4, "hook": "clock_set"} and saved["t_epoch"] == 5000
    assert not list(tmp_path.glob("*.tmp"))
    writer.stop()


# ------------------------------------------------------------------------------------------------- DETACH-1
def good_probe():
    return {"job": {"in_job": False}, "session_id": 1, "interactive_session": 1, "docker": (0, "29.6.2"), "git": (0, "git"),
            "interpreter": {"path": "C:\\py\\python.exe", "expected": "C:\\py\\python.exe"}, "import_sim": True,
            "launch_json_read": True, "keep_awake": True, "heartbeat_written": True, "stop_obeyed": True}


def good_monitor():
    return {"heartbeat_age_s": 10, "exit": {"code": 0, "reason": "STOP"}, "launcher_alive": False, "probe_alive_before_stop": True}


def test_detach1_accepts_only_when_all_ten_criteria_hold():
    assert detach_probe.judge_detach1(good_probe(), good_monitor()) == []


@pytest.mark.parametrize("mutate,criterion", [
    (lambda r, m: r["job"].update(in_job=True), "1_outside_job"),
    (lambda r, m: r.update(session_id=0), "2_same_session"),
    (lambda r, m: r.update(docker=(1, "")), "3_docker_answers"),
    (lambda r, m: r.update(git=(None, "x")), "4_git_and_interpreter"),
    (lambda r, m: r["interpreter"].update(path="C:\\other\\python.exe"), "4_git_and_interpreter"),
    (lambda r, m: r.update(import_sim=False), "5_import_sim_and_launch_json"),
    (lambda r, m: r.update(launch_json_read=False), "5_import_sim_and_launch_json"),
    (lambda r, m: r.update(keep_awake=False), "6_keep_awake"),
    (lambda r, m: m.update(heartbeat_age_s=900), "7_heartbeat_written"),
    (lambda r, m: m.update(heartbeat_age_s=None), "7_heartbeat_written"),
    (lambda r, m: r.update(stop_obeyed=False), "8_stop_obeyed"),
    (lambda r, m: m.update(exit={"code": 1, "reason": "TIMEOUT"}), "9_exit_json_correct"),
    (lambda r, m: m.update(launcher_alive=True), "10_survives_launcher"),
    (lambda r, m: m.update(probe_alive_before_stop=False), "10_survives_launcher"),
])
def test_each_detach1_criterion_failing_alone_rejects_the_mechanism(mutate, criterion):
    result, monitor = good_probe(), good_monitor()
    mutate(result, monitor)
    assert criterion in detach_probe.judge_detach1(result, monitor)


def test_detach1_treats_missing_facts_as_failure_and_covers_exactly_the_ten_criteria():
    assert len(detach_probe.judge_detach1({}, {})) == 10
    assert len(detach_probe.CRITERIA) == 10


def test_probe_collect_obeys_stop_and_writes_exit_with_injected_ports(tmp_path):
    proc = tmp_path / "p"
    proc.mkdir()
    (proc / detach.LAUNCH).write_text(json.dumps({"python": "X", "pid": 1, "t_launch": 1}))
    (proc / detach.STOP).write_text("go")
    ports = {"job": lambda: {"in_job": False, "kill_on_close": False}, "session": lambda: 1,
             "run": lambda cmd: (0, "ok"), "keep_awake": lambda: True}
    result = detach_probe.collect(proc, interactive_session=1, ports=ports, stop_wait_s=3, sleep=lambda s: None, clock=lambda: 5)
    assert result["stop_obeyed"] is True and result["heartbeat_written"] is True and result["keep_awake"] is True
    assert json.loads((proc / detach.EXIT).read_text())["reason"] == "STOP" and (proc / "probe_result.json").exists()


def test_probe_collect_times_out_without_stop_and_records_it(tmp_path):
    proc = tmp_path / "p"
    proc.mkdir()
    ports = {"job": lambda: {"in_job": True}, "session": lambda: 0, "run": lambda cmd: (1, "x"), "keep_awake": lambda: False}
    result = detach_probe.collect(proc, interactive_session=1, ports=ports, stop_wait_s=2, sleep=lambda s: None)
    assert result["stop_obeyed"] is False and json.loads((proc / detach.EXIT).read_text())["code"] == 1


# ------------------------------------------------------------------------------------------------- CLI (sem processo real)
def test_cli_status_stop_tail_and_launch_requirements(tmp_path, capsys):
    run = setup_run(tmp_path)
    root = str(run.registry.root)
    assert cli.main(["status", "--run-id", run.run_id, "--runs-root", root]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "NOT_LAUNCHED"
    assert cli.main(["stop", "--run-id", run.run_id, "--runs-root", root, "--reason", "teste"]) == 0
    capsys.readouterr()
    assert cli.main(["stop", "--run-id", run.run_id, "--runs-root", root]) == 3
    capsys.readouterr()
    assert cli.main(["kill", "--run-id", run.run_id, "--runs-root", root]) == 3                          # sem --confirm
    assert "--confirm" in capsys.readouterr().out
    assert cli.main(["launch", "--run-id", run.run_id, "--runs-root", root]) == 3                        # sem interpretador estavel
    assert "interpretador estavel" in capsys.readouterr().out
    assert cli.main(["tail", "--run-id", run.run_id, "--runs-root", root]) == 0


def test_detach_module_never_starts_a_task_scheduler_job_or_a_plain_process():
    import inspect

    source = inspect.getsource(detach)
    assert "schtasks" not in source.lower()
    assert "Start-Process" not in detach.POWERSHELL_SCRIPT and "Register-ScheduledTask" not in source
    assert "Popen" not in source
