"""
Processo DESTACADO do run (SIM-1.5F C / SIM-1.5G): monitor, parada e lancadores.

* Mecanismo PADRAO (SIM-1.5G, candidato ate o DETACH-2): Windows Task Scheduler (`sched.py`), via `launch_detached`.
* WMI (`launch`) foi REPROVADO no DETACH-1 (a arvore de processos morreu ~121 s depois de criada): permanece no codigo
  apenas como mecanismo rejeitado, DESABILITADO por padrao (`launch_detached(mechanism="wmi")` exige `allow_rejected`).

Nada neste modulo cria processo por conta propria nos testes: `run`, `alive` e `sleep` sao injetados.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.runner import sched
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


CMD_UNSAFE = re.compile(r'[%!^&|<>\r\n]')                 # metacaracteres do cmd: nunca entram no comando montado
EXIT_INT = re.compile(r'-?[0-9]{1,10}')


def build_cmd_arguments(python: str, cli_args: str, proc: Path, module: str = "sim.runner.cli") -> str:
    """Argumentos do `cmd` (identicos para WMI e Task Scheduler): redireciona stdout/stderr e grava o codigo de saida.

    `/v:on` + `!errorlevel!`: expansao no MOMENTO da execucao (depois do Python terminar) e nao no parse da linha; o
    redirecionamento vem ANTES do `echo` (`>arq echo N`), porque `echo 0> arq` seria lido como redirecionamento do handle 0
    (arquivo vazio). Sem `%`, `!`, `^`, `&`, `|`, `<`, `>` nos componentes (fail-closed)."""
    for label, value in (("python", python), ("cli_args", cli_args), ("module", module), ("proc", str(proc))):
        if CMD_UNSAFE.search(value):
            raise DetachError(f"caractere especial do cmd em {label}: o comando nao e montado")
    inner = f'"{python}" -m {module} {cli_args} 1>>"{proc / STDOUT}" 2>>"{proc / STDERR}"'
    return f'/d /v:on /s /c "{inner} & >"{proc / EXIT_CODE}" echo !errorlevel!"'


def build_cmdline(python: str, cli_args: str, proc: Path) -> str:
    """Linha de comando do processo destacado: o `cmd` redireciona stdout/stderr e grava o codigo de saida."""
    return "cmd.exe " + build_cmd_arguments(python, cli_args, proc)


def _atomic_write(path: Path, doc: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(canonical_bytes(doc))
    os.replace(tmp, path)


def launch(runs_root, run_id: str, *, python: str, repo: str, cli_args: str, env: dict, ledger_state: str,
           python_info: dict, run=subprocess.run, alive=pid_alive, sleep=time.sleep, clock=time.time, wait_s: int = 60) -> dict:
    """[REJEITADO no DETACH-1] Cria o processo via WMI. Um unico lancamento por run (`launch.json` e exclusivo). Use `launch_detached`."""
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


def _assess(proc: Path, ledger_state: str | None, *, alive=pid_alive, now=time.time) -> dict:
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


def assess(proc: Path, ledger_state: str | None, *, alive=pid_alive, now=time.time, task: dict | None = None) -> dict:
    """Como `_assess`; com `task` (info do Scheduler) acrescenta o estado da tarefa e sinaliza tarefa orfa (run terminal)."""
    result = _assess(proc, ledger_state, alive=alive, now=now)
    if task is not None:
        result["task"] = {k: task.get(k) for k in ("found", "state", "last_result", "instances")}
        if result["state"] == "TERMINAL" and task.get("found"):
            result["orphan_task"] = True
            result["action"] = "sched-reconcile"
        elif result["state"] in ("RUNNING", "STARTING") and not task.get("found"):
            result["state"], result["action"] = "UNEXPECTED_EXIT", "seal-unclean"
            result["note"] = "processo vivo mas sem tarefa registrada (divergencia com a trilha)"
    return result


def read_exit_code(proc: Path):
    """Codigo de saida gravado pelo `cmd` depois do Python terminar. Ausente, vazio, ilegivel ou nao-inteiro => None
    (nunca se infere do `exit.json`)."""
    try:
        text = (proc / EXIT_CODE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not EXIT_INT.fullmatch(text):
        return None
    value = int(text)
    return value if -2**31 <= value <= 2**32 - 1 else None


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


def launch_detached(runs_root, run_id: str, *, python: str, repo: str, cli_args: str, env: dict, ledger_state: str,
                    python_info: dict, mechanism: str = sched.MECHANISM, allow_rejected: bool = False, profile: str = "SR1",
                    run=None, alive=pid_alive, sleep=time.sleep, clock=time.time, wait_s: int = 60, anchors_dir=None,
                    module: str = "sim.runner.cli") -> dict:
    """Lancador padrao. `task_scheduler`: registra (XML deterministico, validado apos o registro), inicia e espera o heartbeat.
    `wmi` so com `allow_rejected=True` (mecanismo reprovado no DETACH-1)."""
    if mechanism == "wmi":
        if allow_rejected is not True:
            raise DetachError("WMI foi reprovado no DETACH-1 (REJECTED_BY_DETACH1): desabilitado por padrao")
        return launch(runs_root, run_id, python=python, repo=repo, cli_args=cli_args, env=env, ledger_state=ledger_state,
                      python_info=python_info, **({"run": run} if run else {}), alive=alive, sleep=sleep, clock=clock, wait_s=wait_s)
    if mechanism != sched.MECHANISM:
        raise DetachError(f"mecanismo desconhecido: {mechanism!r}")
    if ledger_state != "PREFLIGHTED":
        raise DetachError(f"so um run PREFLIGHTED e lancado (estado: {ledger_state})")
    if profile not in sched.LIMITS:
        raise DetachError(f"perfil de limite desconhecido: {profile!r}")
    run = run or sched.run_default
    try:
        who = sched.require_unelevated(sched.identity(run))
        name = sched.validate_name(run_id)
    except sched.SchedError as error:
        raise DetachError(str(error)) from error
    proc = proc_dir(runs_root, run_id)
    proc.mkdir(parents=True, exist_ok=True)
    launch_path = proc / LAUNCH
    arguments = build_cmd_arguments(python, cli_args, proc, module)
    limit = sched.LIMITS[profile]
    xml = sched.build_task_xml(name, who["user"], arguments, repo, limit)
    doc = {"run_id": run_id, "python": python, "python_info": python_info, "repo": repo, "cmdline": sched.CMD_EXE + " " + arguments,
           "env": dict(sorted(env.items())), "pid": None, "t_launch": int(clock()), "mechanism": sched.MECHANISM,
           "task": sched.FOLDER + name, "xml_sha256": sched.xml_sha256(xml), "limit": limit, "profile": profile, "user": who["user"]}
    try:
        fd = os.open(launch_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise DetachError("este run ja foi lancado (launch.json existe): relancar e retry com novo run_id") from error
    os.close(fd)
    _atomic_write(launch_path, doc)
    audit = sched.Audit(proc, anchors_dir or (Path(runs_root) / sched.ANCHOR_DIR), name, clock)
    aliases = [who["user"], who.get("sid", who["user"])]
    try:
        sched.register_verified(run, name, xml, user_aliases=aliases, arguments=arguments, workdir=repo, limit=limit, audit=audit)
        sched.start(run, name)
    except sched.SchedError as error:
        raise DetachError(f"Task Scheduler: {error}") from error
    audit.append("STARTED", {"launcher_pid": os.getpid()})
    deadline = clock() + wait_s
    while clock() < deadline:
        beat = read_heartbeat(proc)
        if beat is not None and alive(beat["pid"]):
            engine = []
            try:
                table = sched.process_table(run, who["schedule_pid"])
                engine = [e["pid"] for e in sched.task_instances(table, who["schedule_pid"], str(proc))["engines"]]
            except sched.SchedError as error:
                audit.append("ENGINE_LOOKUP_FAILED", {"error": str(error)[:200]})
            doc["pid"] = engine[0] if engine else beat["pid"]
            _atomic_write(launch_path, doc)
            audit.append("HEARTBEAT_SEEN", {"pid": beat["pid"], "engine_pids": engine})
            return {**doc, "heartbeat_pid": beat["pid"], "engine_pids": engine}
        sleep(1)
    audit.append("START_TIMEOUT", {"wait_s": wait_s})
    raise DetachError("o processo da tarefa nao escreveu heartbeat no prazo (nao esta saudavel; use sched-reconcile)")


def kill_task(proc: Path, *, confirm: bool, run=None, anchors_dir=None, clock=time.time) -> dict:
    """Fim FORCADO da instancia da tarefa (`Stop-ScheduledTask`): ultimo recurso, exige confirmacao. Nao remove a tarefa."""
    if confirm is not True:
        raise DetachError("kill exige --confirm (ultimo recurso; use stop)")
    doc = read_launch(proc)
    if doc is None or doc.get("mechanism") != sched.MECHANISM:
        raise DetachError("este run nao foi lancado pelo Task Scheduler")
    run = run or sched.run_default
    name = sched.validate_name(doc["run_id"])
    audit = sched.Audit(proc, anchors_dir or (Path(proc).parent / sched.ANCHOR_DIR), name, clock)
    sched.end(run, name)
    audit.append("FORCED_END", {"by": "operator", "confirm": True})
    after = sched.info(run, name)
    who = sched.identity(run)
    table = sched.process_table(run, who["schedule_pid"])
    count = len(sched.task_instances(table, who["schedule_pid"], str(proc))["engines"])
    _atomic_write(proc / KILLED, {"task": doc["task"], "state_after": after.get("state"), "instances_after": count})
    return {"task": doc["task"], "state_after": after.get("state"), "instances_after": count}


def tail(proc: Path, which: str = "stdout", lines: int = 50) -> list:
    name = {"stdout": STDOUT, "stderr": STDERR}[which]
    try:
        return (proc / name).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def write_exit(proc: Path, code: int, reason: str, clock=time.time) -> None:
    proc.mkdir(parents=True, exist_ok=True)
    _atomic_write(proc / EXIT, {"code": int(code), "reason": reason, "t_epoch": int(clock())})
