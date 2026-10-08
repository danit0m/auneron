"""
Sonda de aceitacao DETACH-1 (SIM-1.5F C.3). O processo-sonda roda PELO MESMO CAMINHO do run (WMI) e coleta, de dentro,
os fatos que decidem se o mecanismo e aceitavel; o juiz (`judge_detach1`) e puro. Os fatos "o lancador terminou e o
processo continua vivo" sao do MONITOR (fora do processo-sonda).

Nesta etapa nada disto e executado de verdade: os testes injetam as portas. O DETACH-1 real e um passo do SR-1.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from sim.runner import detach
from sim.runner import host

CRITERIA = (
    "1_outside_job", "2_same_session", "3_docker_answers", "4_git_and_interpreter", "5_import_sim_and_launch_json",
    "6_keep_awake", "7_heartbeat_written", "8_stop_obeyed", "9_exit_json_correct", "10_survives_launcher",
)


def _job_state() -> dict:
    if sys.platform != "win32":
        return {"in_job": False, "kill_on_close": False}
    import ctypes
    import ctypes.wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = w.HANDLE
    k.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]
    result = w.BOOL()
    k.IsProcessInJob(k.GetCurrentProcess(), None, ctypes.byref(result))
    if not result.value:
        return {"in_job": False, "kill_on_close": False}

    class Basic(ctypes.Structure):
        _fields_ = [("a", ctypes.c_longlong), ("b", ctypes.c_longlong), ("LimitFlags", w.DWORD), ("c", ctypes.c_size_t),
                    ("d", ctypes.c_size_t), ("e", w.DWORD), ("f", ctypes.c_size_t), ("g", w.DWORD), ("h", w.DWORD)]

    class Io(ctypes.Structure):
        _fields_ = [("x", ctypes.c_ulonglong)] * 6

    class Ext(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", Io), ("p", ctypes.c_size_t), ("q", ctypes.c_size_t), ("r", ctypes.c_size_t),
                    ("s", ctypes.c_size_t)]

    info, returned = Ext(), w.DWORD()
    ok = k.QueryInformationJobObject(None, 9, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned))
    return {"in_job": True, "kill_on_close": bool(ok and info.Basic.LimitFlags & 0x2000)}


def _session_id() -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes as w

    session = w.DWORD()
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    return int(session.value)


def _run(cmd: list):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return out.returncode, (out.stdout or out.stderr)[:80].strip()
    except Exception as error:                                                       # noqa: BLE001 - falha vira fato
        return None, str(error)[:80]


def collect(proc: Path, *, interactive_session: int | None, ports: dict | None = None, stop_wait_s: int = 600,
            sleep=time.sleep, clock=time.time) -> dict:
    """Roda DENTRO do processo-sonda. Escreve o heartbeat, espera o STOP, obedece e grava `exit.json`."""
    ports = ports or {}
    job = (ports.get("job") or _job_state)()
    launch_doc = detach.read_launch(proc)
    result = {
        "pid": os.getpid(), "job": job, "session_id": (ports.get("session") or _session_id)(),
        "interactive_session": interactive_session,
        "docker": (ports.get("run") or _run)(["docker", "version", "--format", "{{.Server.Version}}"]),
        "git": (ports.get("run") or _run)(["git", "--version"]),
        "interpreter": {"path": sys.executable, "expected": (launch_doc or {}).get("python")},
        "launch_json_read": launch_doc is not None, "import_sim": False,
        "keep_awake": (ports.get("keep_awake") or host.keep_awake)(),
    }
    try:
        import sim  # noqa: F401

        result["import_sim"] = True
    except ImportError:
        pass
    writer = host.HeartbeatWriter(proc / detach.HEARTBEAT, lambda: {"probe": True}, interval_s=5)
    writer.beat()
    result["heartbeat_written"] = (proc / detach.HEARTBEAT).exists()
    stop_flag = host.StopFlag(proc / detach.STOP)
    waited = 0
    while not stop_flag.requested and waited < stop_wait_s:
        sleep(1)
        waited += 1
        if waited % 5 == 0:
            writer.beat()
    result["stop_obeyed"] = stop_flag.requested
    detach.write_exit(proc, 0 if stop_flag.requested else 1, "STOP" if stop_flag.requested else "TIMEOUT", clock=clock)
    (proc / "probe_result.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    return result


def judge_detach1(result: dict, monitor: dict) -> list:
    """Falhas (lista de criterios) do DETACH-1. `monitor`: fatos observados de FORA ({launcher_alive, probe_alive,
    heartbeat_age_s, exit}). Vazio = aceito. Fato ausente = reprovado."""
    fails = []

    def need(name: str, ok: bool) -> None:
        if not ok:
            fails.append(name)

    job = result.get("job") or {}
    need("1_outside_job", job.get("in_job") is False)
    need("2_same_session", result.get("session_id") is not None and result.get("session_id") == result.get("interactive_session"))
    docker = result.get("docker") or (None, "")
    need("3_docker_answers", docker[0] == 0 and bool(docker[1]))
    git = result.get("git") or (None, "")
    interpreter = result.get("interpreter") or {}
    need("4_git_and_interpreter", git[0] == 0 and bool(interpreter.get("path")) and interpreter.get("path") == interpreter.get("expected"))
    need("5_import_sim_and_launch_json", result.get("import_sim") is True and result.get("launch_json_read") is True)
    need("6_keep_awake", result.get("keep_awake") is True)
    need("7_heartbeat_written", result.get("heartbeat_written") is True and monitor.get("heartbeat_age_s") is not None
         and monitor["heartbeat_age_s"] <= detach.STALE_SECONDS)
    need("8_stop_obeyed", result.get("stop_obeyed") is True)
    exit_doc = monitor.get("exit") or {}
    need("9_exit_json_correct", exit_doc.get("code") == 0 and exit_doc.get("reason") == "STOP")
    need("10_survives_launcher", monitor.get("launcher_alive") is False and monitor.get("probe_alive_before_stop") is True)
    return fails


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="sim.runner.detach_probe")
    parser.add_argument("--proc-dir", required=True)
    parser.add_argument("--interactive-session", type=int, required=True)
    args = parser.parse_args(argv)
    collect(Path(args.proc_dir), interactive_session=args.interactive_session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
