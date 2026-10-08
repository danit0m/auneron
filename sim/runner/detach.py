"""
Processo DESTACADO do run (SIM-1.5F C): lancador WMI, monitor e parada. Mecanismo candidato, nao prova de sobrevivencia
(emenda A do PO): processos criados pela sessao ficam em um Job object; o criado por `Win32_Process.Create` (WMI) sai dele
(medido). O teste definitivo de sobrevivencia ao fim da sessao continua em aberto; a sonda DETACH-1 (`detach_probe`) so o
verifica DEPOIS que o lancador termina. Task Scheduler NAO e usado (configuracao persistente: exige aprovacao do PO).

Nada neste modulo cria processo por conta propria nos testes: `run`, `alive` e `sleep` sao injetados.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.runner.errors import RunnerError
from sim.runner.host import pid_alive

PROC_SUFFIX = ".proc"
LAUNCH, HEARTBEAT, STOP, EXIT, EXIT_CODE = "launch.json", "heartbeat.json", "STOP", "exit.json", "exit_code.txt"
STDOUT, STDERR, KILLED = "stdout.log", "stderr.log", "killed.json"
STALE_SECONDS, START_GRACE_SECONDS = 180, 60

POWERSHELL_SCRIPT = (
    "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
    "-Arguments @{CommandLine=$env:SIM_CMDLINE; CurrentDirectory=$env:SIM_CWD}; "
    "Write-Output ('RV=' + $r.ReturnValue + ' PID=' + $r.ProcessId)"
)


class DetachError(RunnerError):
    pass


def proc_dir(runs_root, run_id: str) -> Path:
    return Path(runs_root) / f"{run_id}{PROC_SUFFIX}"


def build_cmdline(python: str, cli_args: str, proc: Path) -> str:
    """Linha de comando do processo destacado: o `cmd` redireciona stdout/stderr e grava o codigo de saida."""
    inner = f'"{python}" -m sim.runner.cli {cli_args} 1>>"{proc / STDOUT}" 2>>"{proc / STDERR}"'
    return f'cmd.exe /d /s /c "{inner} & echo %errorlevel%> "{proc / EXIT_CODE}""'


def _atomic_write(path: Path, doc: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(canonical_bytes(doc))
    os.replace(tmp, path)


def launch(runs_root, run_id: str, *, python: str, repo: str, cli_args: str, env: dict, ledger_state: str,
           python_info: dict, run=subprocess.run, alive=pid_alive, sleep=time.sleep, clock=time.time, wait_s: int = 60) -> dict:
    """Cria o processo via WMI. Um unico lancamento por run (`launch.json` e exclusivo): relancar e retry (novo run_id)."""
    if ledger_state != "PREFLIGHTED":
        raise DetachError(f"so um run PREFLIGHTED e lancado (estado: {ledger_state})")
    proc = proc_dir(runs_root, run_id)
    proc.mkdir(parents=True, exist_ok=True)
    launch_path = proc / LAUNCH
    cmdline = build_cmdline(python, cli_args, proc)
    doc = {"run_id": run_id, "python": python, "python_info": python_info, "repo": repo, "cmdline": cmdline,
           "env": dict(sorted(env.items())), "pid": None, "t_launch": int(clock()), "mechanism": "wmi.Win32_Process.Create"}
    try:
        fd = os.open(launch_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise DetachError("este run ja foi lancado (launch.json existe): relancar e retry com novo run_id") from error
    os.close(fd)
    _atomic_write(launch_path, doc)
    result = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_SCRIPT],
                 env={**os.environ, "SIM_CMDLINE": cmdline, "SIM_CWD": repo}, capture_output=True, text=True, timeout=60)
    out = (getattr(result, "stdout", "") or "").strip()
    try:
        fields = dict(part.split("=", 1) for part in out.split() if "=" in part)
        return_value, pid = int(fields["RV"]), int(fields["PID"])
    except (KeyError, ValueError) as error:
        raise DetachError(f"retorno inesperado do WMI: {out!r}") from error
    if getattr(result, "returncode", 0) != 0 or return_value != 0:
        raise DetachError(f"WMI recusou a criacao do processo (RV={return_value})")
    doc["pid"] = pid
    _atomic_write(launch_path, doc)
    deadline = clock() + wait_s
    while clock() < deadline:
        beat = read_heartbeat(proc)
        if beat is not None and alive(beat["pid"]):
            return {**doc, "heartbeat_pid": beat["pid"]}
        sleep(1)
    raise DetachError("o processo criado nao escreveu heartbeat no prazo (nao esta saudavel)")


def read_heartbeat(proc: Path) -> dict | None:
    try:
        return json.loads((proc / HEARTBEAT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_launch(proc: Path) -> dict | None:
    try:
        return json.loads((proc / LAUNCH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def assess(proc: Path, ledger_state: str | None, *, alive=pid_alive, now=time.time) -> dict:
    """Classifica o processo. `UNEXPECTED_EXIT` (pid morto e ledger nao terminal) => proximo comando e `seal-unclean`."""
    launch_doc = read_launch(proc)
    if launch_doc is None:
        return {"state": "NOT_LAUNCHED", "action": None}
    beat = read_heartbeat(proc)
    terminal = ledger_state in ("COMPLETED", "ABORTED", "INVALID")
    pid = beat["pid"] if beat else launch_doc.get("pid")
    pid_ok = bool(pid) and alive(pid)
    age = None if beat is None else max(int(now()) - int(beat["t_epoch"]), 0)
    if terminal:
        return {"state": "TERMINAL", "action": None, "ledger_state": ledger_state, "pid_alive": pid_ok, "heartbeat_age_s": age}
    if not pid_ok:
        starting = beat is None and launch_doc.get("pid") and (int(now()) - launch_doc["t_launch"]) < START_GRACE_SECONDS \
            and alive(launch_doc["pid"])
        if starting:
            return {"state": "STARTING", "action": None, "pid_alive": True, "heartbeat_age_s": age}
        return {"state": "UNEXPECTED_EXIT", "action": "seal-unclean", "ledger_state": ledger_state, "pid_alive": False,
                "heartbeat_age_s": age, "exit_code": read_exit_code(proc)}
    if beat is None:
        young = (int(now()) - launch_doc["t_launch"]) < START_GRACE_SECONDS
        return {"state": "STARTING" if young else "HUNG", "action": None if young else "investigate", "pid_alive": True,
                "heartbeat_age_s": None}
    if age > STALE_SECONDS:
        return {"state": "HUNG", "action": "investigate", "pid_alive": True, "heartbeat_age_s": age,
                "note": "so avisa; nada e encerrado automaticamente"}
    return {"state": "RUNNING", "action": None, "pid_alive": True, "heartbeat_age_s": age, "status": beat.get("status")}


def read_exit_code(proc: Path):
    try:
        return int((proc / EXIT_CODE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def request_stop(proc: Path, reason: str) -> Path:
    """Parada CONTROLADA: cria o arquivo `STOP`; o runner le nas fronteiras de hook (ABORTED(OPERATOR_STOP))."""
    proc.mkdir(parents=True, exist_ok=True)
    path = proc / STOP
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise DetachError("STOP ja solicitado") from error
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(reason.strip() or "OPERATOR_STOP")
    return path


def kill(proc: Path, *, confirm: bool, run=subprocess.run, alive=pid_alive) -> dict:
    """Ultimo recurso (`taskkill /T /F`): exige confirmacao explicita. O run vira UNCLEAN_EXIT no proximo `seal-unclean`."""
    if confirm is not True:
        raise DetachError("kill exige --confirm (ultimo recurso; use stop)")
    pids = []
    beat, launch_doc = read_heartbeat(proc), read_launch(proc)
    for pid in (beat and beat.get("pid"), launch_doc and launch_doc.get("pid")):
        if pid and pid not in pids:
            pids.append(pid)
    results = []
    for pid in pids:
        if alive(pid):
            out = run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
            results.append({"pid": pid, "rc": getattr(out, "returncode", None)})
    _atomic_write(proc / KILLED, {"pids": pids, "results": results})
    return {"pids": pids, "results": results}


def tail(proc: Path, which: str = "stdout", lines: int = 50) -> list:
    name = {"stdout": STDOUT, "stderr": STDERR}[which]
    try:
        return (proc / name).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def write_exit(proc: Path, code: int, reason: str, clock=time.time) -> None:
    proc.mkdir(parents=True, exist_ok=True)
    _atomic_write(proc / EXIT, {"code": int(code), "reason": reason, "t_epoch": int(clock())})
