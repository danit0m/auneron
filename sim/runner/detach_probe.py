"""
Sonda de aceitacao DETACH-1 (SIM-1.5F C.3). O processo-sonda roda PELO MESMO CAMINHO do run (WMI) e coleta, de dentro,
os fatos que decidem se o mecanismo e aceitavel; o juiz (`judge_detach1`) e puro. Os fatos "o lancador terminou e o
processo continua vivo" sao do MONITOR (fora do processo-sonda).

Nos testes nada disto roda de verdade: as portas sao injetadas. DETACH-1 (WMI) foi executado e REPROVOU; o DETACH-2
(Task Scheduler, `collect2`/`judge_detach2`) e candidato e so roda com autorizacao do PO.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from sim.runner import detach
from sim.runner import host
from sim.runner import sched
from sim.runner import termination
from sim.runner import watch

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


# ---------------------------------------------------------------------------------------------------------------------
# DETACH-2 (SIM-1.5G): sonda incremental + juiz de 12 criterios + orquestracao (lancar / monitorar / encerrar)
# ---------------------------------------------------------------------------------------------------------------------
CRITERIA2 = (
    "D2-01_task_as_requested", "D2-02_scheduler_parentage", "D2-03_same_session", "D2-04_isolated_interpreter",
    "D2-05_docker_git_path", "D2-06_keep_awake", "D2-07_heartbeat_continuous", "D2-08_survives_launcher",
    "D2-09_stop_exit_clean", "D2-10_no_trigger_restart_parallel", "D2-11_removed_clean_evidence", "D2-12_session_end_continuity",
)
LEVELS = {"L1": {"required_s": 900, "survive_s": 840, "profile": "PROBE_L1", "cap_s": 1500},
          "L2": {"required_s": 2700, "survive_s": 600, "profile": "PROBE_L2", "cap_s": 6000}}   # L2: teto 4200 -> 6000 s (PT2H mantido)
MAX_GAP_S = 15
# Evidencias HISTORICAS (anteriores a 1.5G.3, sem vigilia duravel) reconhecidas de forma VERIFICAVEL: run_id -> sha256 do launch.json
# final, conferido contra o `evidence_manifest.json` preservado de cada tentativa. So elas podem usar o caminho legado de D2-08/D2-12.
# Evidencia nova (launch.json com `evidence_schema` 2) nunca cai aqui; evidencia sem schema e fora desta lista nao e julgavel.
LEGACY_EVIDENCE = {
    "DETACH2-PROBE-L1-20261008T191935Z": "61533cbd4a49d06e2ff98f5a5fa9250021548522c1caba0e6d7208109f4e1c42",    # L1-B01 (FAIL 7/11)
    "DETACH2-PROBE-L1-20261008T204909Z": "f5794f6ddd094b359b79cb6dbaaa749c74254573303074b3c45f0f8105bfd429",    # L1-B02 (PASS 11/11)
}
EVIDENCE_V2, EVIDENCE_LEGACY = "v2", "legacy-1"
DENSE_SAMPLES, DENSE_MIN, DENSE_PAUSE_S = 20, 8, 0.5     # amostragem densa depois da 2a partida (janelas transitorias)
BEAT_EVERY_S = 5
FACTS, FACTS_LOG, BEATS = "facts.json", "facts.jsonl", "beats.jsonl"
MONITOR_JSON, VERDICT_JSON, EVIDENCE_MANIFEST = "detach2_monitor.json", "detach2_verdict.json", "evidence_manifest.json"


def _atomic_json(path: Path, doc: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class FactsWriter:
    """Fatos gravados INCREMENTALMENTE (licao do DETACH-1): cada fase atualiza `facts.json` e acrescenta a `facts.jsonl`."""

    def __init__(self, proc: Path) -> None:
        self.proc, self.facts = Path(proc), {}

    def record(self, phase: str, **values) -> None:
        self.facts.update(values)
        self.facts["phase"] = phase
        _atomic_json(self.proc / FACTS, self.facts)
        with (self.proc / FACTS_LOG).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"phase": phase, **values}, sort_keys=True) + "\n")


def _venv_cfg(prefix: str) -> dict:
    try:
        text = (Path(prefix) / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return {}
    return {k.strip(): v.strip() for k, _, v in (line.partition("=") for line in text.splitlines()) if k.strip()}


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(str(path))) if path else ""


def collect2(proc: Path, *, interactive_session: int | None, cap_s: int, ports: dict | None = None, sleep=time.sleep,
             clock=time.time) -> dict:
    """Roda DENTRO do processo da tarefa. Grava fatos a cada fase, um batimento a cada 5 s (`heartbeat.json` + `beats.jsonl`),
    observa os alvos de `watch.json` (vigilia duravel), obedece ao STOP e grava `exit.json`. Se for morto no meio, os fatos ja
    gravados permanecem.

    SIM-1.5G.3: o motivo (STOP/TIMEOUT/EXCEPTION) e o codigo vem da tabela unica de `termination`; qualquer excecao grava
    `exit.json` EXCEPTION (sem sobrescrever um termino ja gravado) e e relancada; falha ao gravar o `exit.json` nunca vira
    sucesso (codigo reservado EVIDENCE_FAILURE). O codigo do processo fica em `facts['exit_code']` (usado por `main`)."""
    ports, proc = ports or {}, Path(proc)
    writer = FactsWriter(proc)
    launch_doc = detach.read_launch(proc) or {}
    expected = launch_doc.get("python")
    run = ports.get("run") or _run
    which = ports.get("which") or __import__("shutil").which
    prefix = (ports.get("prefix") or (lambda: (sys.prefix, sys.base_prefix, sys.executable)))()
    query = ports.get("query") or watch.query_native
    terminal = {"written": False}
    try:
        writer.record("start", pid=os.getpid(), session_id=(ports.get("session") or _session_id)(), interactive_session=interactive_session,
                      interpreter={"prefix": prefix[0], "base_prefix": prefix[1], "executable": prefix[2],
                                   "expected_prefix": str(Path(expected).parent.parent) if expected else None,
                                   "include_system_site_packages": _venv_cfg(prefix[0]).get("include-system-site-packages")},
                      launch_json_read=bool(launch_doc), cap_s=cap_s, t_start=int(clock()),
                      lineage=(ports.get("lineage") or (lambda: watch.self_lineage(query)))())
        imported = False
        try:
            import sim  # noqa: F401

            imported = True
        except ImportError:
            pass
        writer.record("import", import_sim=imported)
        writer.record("job", job=(ports.get("job") or _job_state)())
        writer.record("docker", docker=list(run(["docker", "version", "--format", "{{.Server.Version}}"])))
        writer.record("git", git=list(run(["git", "--version"])), path={"docker": which("docker") is not None, "git": which("git") is not None})
        writer.record("keep_awake", keep_awake=bool((ports.get("keep_awake") or host.keep_awake)()))
        hb = host.HeartbeatWriter(proc / detach.HEARTBEAT, lambda: {"probe": "detach2"}, interval_s=BEAT_EVERY_S, clock=clock)
        stop_flag = host.StopFlag(proc / detach.STOP)
        watcher = watch.Watcher.open(proc, query=query, clock=clock, period_s=BEAT_EVERY_S,
                                     **({"write": ports["watch_write"]} if "watch_write" in ports else {}))
        seq, started = 0, clock()

        def beat() -> None:
            nonlocal seq
            doc = hb.beat()
            seq += 1
            with (proc / BEATS).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"seq": seq, "t": doc["t_epoch"], "pid": doc["pid"]}) + "\n")
            if watcher is not None:
                watcher.step()

        beat()
        writer.record("heartbeat", heartbeat_written=(proc / detach.HEARTBEAT).exists(), watch_armed=watcher is not None)
        last_beat = clock()
        while not stop_flag.requested and clock() - started < cap_s:
            sleep(1)
            if clock() - last_beat >= BEAT_EVERY_S:
                beat()
                last_beat = clock()
        obeyed = stop_flag.requested
        beat()
        reason = "STOP" if obeyed else "TIMEOUT"
        code = termination.exit_code_for(reason)
        failure = detach.write_terminal(proc, code, reason, clock)
        terminal["written"] = failure is None
        if failure is not None:                                   # a causa (STOP/TIMEOUT) fica em `exit_reason`; o processo falha
            code = termination.EXIT_CODES["EVIDENCE_FAILURE"]
        writer.record("exit", stop_obeyed=bool(obeyed), exit_reason=reason, exit_code=code, exit_write_error=failure,
                      watch_write_errors=watcher.write_errors if watcher is not None else 0)
    except BaseException as error:                                # noqa: BLE001 - registra o termino e relanca
        if not terminal["written"]:
            secondary = detach.write_terminal(proc, termination.EXIT_CODES["EXCEPTION"], "EXCEPTION", clock,
                                              detail=f"{type(error).__name__}: {error}")
            if secondary:
                print(f"SECUNDARIO: exit.json nao gravado ({secondary})", file=sys.stderr)
        raise
    return writer.facts


def beat_series(proc: Path) -> list:
    path = Path(proc) / BEATS
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def beat_stats(beats: list, *, after: int | None = None, cross: int | None = None) -> dict:
    """Maior intervalo, primeira/ultima batida, e (opcional) continuidade atravessando o instante `cross`."""
    times = [b["t"] for b in beats]
    gaps = [b - a for a, b in zip(times, times[1:])]
    stats = {"count": len(times), "first_t": times[0] if times else None, "last_t": times[-1] if times else None,
             "max_gap_s": max(gaps) if gaps else None, "duration_s": (times[-1] - times[0]) if times else 0}
    if after is not None and times:
        stats["survived_after_s"] = times[-1] - after
    if cross is not None:
        before, later = [t for t in times if t <= cross], [t for t in times if t > cross]
        stats["crosses"] = bool(before and later)
        stats["gap_at_cross_s"] = (later[0] - before[-1]) if before and later else None
    return stats


def judge_detach2(facts: dict, monitor: dict, level: str = "L1") -> dict:
    """Juiz PURO (12 criterios binarios; fato ausente = reprovado). L1 PASS = D2-01..D2-11; L2 exige tambem D2-12.
    -> {failed, passed, not_proven, verdict}. Em L1, D2-12 e NOT_PROVEN (nao e reprova, mas nao autoriza a afirmacao)."""
    spec = LEVELS[level]
    ok = {}
    interp = facts.get("interpreter") or {}
    engine = monitor.get("engine") or {}
    schedule = monitor.get("schedule") or {}
    tree = monitor.get("engine_tree") or []
    # D2-02: medido na TABELA DE PROCESSOS do SO (cmd.exe filho do servico Schedule com a linha de comando da tarefa),
    # independente do heartbeat; o heartbeat so corrobora que a sonda esta dentro da arvore.
    ok["D2-01_task_as_requested"] = (monitor.get("registered_verified") is True and monitor.get("xml_sha256") is not None
                                     and str(monitor.get("task", "")).startswith("\\AuneronSim\\"))
    ok["D2-02_scheduler_parentage"] = (str(engine.get("name", "")).lower() == "cmd.exe" and engine.get("pid") is not None
                                       and schedule.get("pid") not in (None, 0) and str(schedule.get("name", "")).lower() == "svchost.exe"
                                       and engine.get("ppid") == schedule.get("pid") and monitor.get("heartbeat_pid_in_tree") is True
                                       and monitor.get("launcher_pid") is not None and monitor.get("launcher_pid") not in tree
                                       and engine.get("session") is not None and engine.get("session") == facts.get("interactive_session"))
    ok["D2-03_same_session"] = (facts.get("session_id") is not None and facts.get("session_id") == facts.get("interactive_session"))
    ok["D2-04_isolated_interpreter"] = (bool(interp.get("prefix")) and _norm(interp.get("prefix")) == _norm(interp.get("expected_prefix"))
                                        and _norm(interp.get("prefix")) != _norm(interp.get("base_prefix"))
                                        and str(interp.get("include_system_site_packages", "")).lower() == "false"
                                        and facts.get("import_sim") is True and facts.get("launch_json_read") is True)
    docker, git, path = facts.get("docker") or (None, ""), facts.get("git") or (None, ""), facts.get("path") or {}
    ok["D2-05_docker_git_path"] = (docker[0] == 0 and bool(docker[1]) and git[0] == 0 and path.get("docker") is True
                                   and path.get("git") is True)
    ok["D2-06_keep_awake"] = facts.get("keep_awake") is True and facts.get("heartbeat_written") is True
    hb = monitor.get("beats") or {}
    version = monitor.get("evidence_version")                  # v2 (vigilia duravel) | legacy-1 (historica verificavel) | None
    v2, legacy = version == EVIDENCE_V2, version == EVIDENCE_LEGACY
    wm = monitor.get("watch") or {}
    watch_sound = (v2 and wm.get("armed") is True and wm.get("integrity_ok") is True and facts.get("watch_armed") is True
                   and facts.get("watch_write_errors") == 0)
    ok["D2-07_heartbeat_continuous"] = (hb.get("max_gap_s") is not None and hb["max_gap_s"] <= MAX_GAP_S
                                        and hb.get("duration_s", 0) >= spec["required_s"])
    # D2-08: v2 mede a sobrevivencia a partir do PRIMEIRO instante em que a PROPRIA tarefa viu o lancador encerrado (nao da hora de
    # inicio do monitor); limiares inalterados. Evidencia nova incompleta NUNCA cai no caminho legado.
    lw = monitor.get("launcher_watch") or {}
    durable_08 = (watch_sound and lw.get("how") in watch.ENDED_HOW and isinstance(lw.get("first_seen_ended_t"), int)
                  and hb.get("survived_durable_s") is not None and hb["survived_durable_s"] >= spec["survive_s"])
    legacy_08 = legacy and hb.get("survived_after_s") is not None and hb["survived_after_s"] >= spec["survive_s"]
    ok["D2-08_survives_launcher"] = (monitor.get("launcher_alive") is False and monitor.get("probe_alive_before_stop") is True
                                     and (durable_08 or legacy_08))
    exit_doc = monitor.get("exit") or {}
    term = monitor.get("termination") or {}
    ok["D2-09_stop_exit_clean"] = (facts.get("stop_obeyed") is True and exit_doc.get("code") == 0 and exit_doc.get("reason") == "STOP"
                                   and monitor.get("exit_code_txt") == 0 and monitor.get("task_state_after") == sched.STATE_READY
                                   and monitor.get("last_result_after") == 0 and facts.get("exit_write_error") is None
                                   and (legacy or (v2 and term.get("class") == "STOP_CLEAN" and term.get("scheduler_checked") is True)))
    inst = monitor.get("instances") or {}
    ok["D2-10_no_trigger_restart_parallel"] = (monitor.get("triggers") == 0 and monitor.get("restart_on_failure") is False
                                               and inst.get("second_start_attempted") is True and inst.get("engines_max") == 1
                                               and inst.get("engines_min_while_alive") == 1
                                               and len(inst.get("distinct_engine_pids") or []) == 1
                                               and inst.get("start_records") == 1 and len(inst.get("beat_pids") or []) == 1
                                               and (inst.get("dense_samples_after_second_start") or 0) >= DENSE_MIN
                                               and inst.get("sample_errors") == 0)
    ok["D2-11_removed_clean_evidence"] = (monitor.get("removal_verified") is True and monitor.get("orphan_processes") == 0
                                          and monitor.get("evidence_manifest_ok") is True and monitor.get("audit_ok") is True
                                          and monitor.get("folder_clean") is True and not monitor.get("audit_write_failures"))
    cross = monitor.get("session_end") or {}
    legacy_12 = (legacy and cross.get("t") is not None and cross.get("crosses") is True
                 and cross.get("gap_at_cross_s") is not None and cross["gap_at_cross_s"] <= MAX_GAP_S)
    # D2-12 v2 (fail-closed): o fim do CLI iniciador e FATO DURAVEL da tarefa (intervalo explicito, PID + criacao); batimentos antes e
    # depois sem lacuna; >= survive_s de batimentos DEPOIS do fim; STOP depois do fim por solicitante FORA da cadeia iniciadora;
    # os mesmos processos da sonda (PID + criacao) do inicio ao fim. Qualquer fato ausente = nao provado.
    durable_12 = (watch_sound and cross.get("source") == "watch" and cross.get("role") == "initiator" and cross.get("how") in watch.ENDED_HOW
                  and isinstance(cross.get("last_seen_alive_t"), int) and isinstance(cross.get("first_seen_ended_t"), int)
                  and cross.get("period_s") is not None and cross.get("crosses") is True
                  and cross.get("gap_at_cross_s") is not None and cross["gap_at_cross_s"] <= MAX_GAP_S
                  and cross.get("survived_after_end_s") is not None and cross["survived_after_end_s"] >= spec["survive_s"]
                  and cross.get("stop_after_end") is True and cross.get("requester_outside_initiator") is True
                  and cross.get("probe_lineage_unchanged") is True)
    ok["D2-12_session_end_continuity"] = legacy_12 or durable_12
    failed = [c for c in CRITERIA2 if not ok[c] and (c != "D2-12_session_end_continuity" or level == "L2")]
    not_proven = ["D2-12_session_end_continuity"] if level == "L1" else []
    passed = [c for c in CRITERIA2 if ok[c]]
    return {"level": level, "failed": failed, "passed": passed, "not_proven": not_proven,
            "verdict": "PASS" if not failed else "FAIL"}


def probe_name(level: str, clock=time.time) -> str:
    return f"DETACH2-PROBE-{level}-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(int(clock())))


def evidence_version(proc) -> str | None:
    """Versao VERIFICAVEL da evidencia: `v2` (launch.json com `evidence_schema` 2, `watch.json` cujo hash confere com o do launch.json
    e com UM `WATCH_ARMED` da trilha) ou `legacy-1` (run_id + sha256 do launch.json na lista historica). Qualquer outra coisa => None
    (nao julgavel): evidencia nova incompleta ou adulterada nunca escolhe o caminho legado."""
    proc = Path(proc)
    try:
        raw = (proc / detach.LAUNCH).read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    if "evidence_schema" in doc:
        if doc.get("evidence_schema") != 2 or not doc.get("watch_sha256") or watch.watch_sha256(proc) != doc["watch_sha256"]:
            return None
        try:
            armed = [r for r in sched.Audit(proc, proc, doc["run_id"]).records() if r.get("event") == "WATCH_ARMED"]
        except (ValueError, UnicodeDecodeError, sched.SchedError, KeyError, OSError):
            return None
        return EVIDENCE_V2 if len(armed) == 1 and (armed[0].get("payload") or {}).get("sha256") == doc["watch_sha256"] else None
    return EVIDENCE_LEGACY if LEGACY_EVIDENCE.get(doc.get("run_id")) == hashlib.sha256(raw).hexdigest() else None


def probe_launch(runs_root, name: str, *, level: str, python: str, repo: str, interactive_session: int, run=None,
                 alive=host.pid_alive, sleep=time.sleep, clock=time.time, wait_s: int = 60, anchors_dir=None,
                 initiator: dict | None = None, excluded: list | None = None, helpers=(), query=None, launcher_pid: int | None = None) -> dict:
    """Lanca a sonda pelo Task Scheduler (UMA tarefa) e TERMINA. O processo-sonda e o `detach_probe --detach2`.

    A vigilia duravel e ARMADA antes do registro: alvo `launcher` (este processo) sempre; `initiator` (CLI da sessao iniciadora,
    descoberto por `watch.discover_initiator`) e OBRIGATORIO no L2 (sem ele nenhuma tarefa e registrada); `helpers` so para controles."""
    spec = LEVELS[level]
    if level == "L2" and initiator is None:
        raise detach.DetachError("L2 exige a identidade do CLI da sessao iniciadora (descoberta ausente): nenhuma tarefa foi registrada")
    query = query or watch.query_native
    targets = [watch.make_target("launcher", launcher_pid or os.getpid(), query)]
    if initiator is not None:
        targets.append(initiator)
    targets += [watch.make_target("helper", int(pid), query) for pid in helpers]
    data, sha = watch.build_watch(targets, excluded or [], armed_t=int(clock()))
    proc = detach.proc_dir(runs_root, name)
    cli_args = f'--detach2 --proc-dir "{proc}" --interactive-session {int(interactive_session)} --cap-s {spec["cap_s"]}'
    return detach.launch_detached(runs_root, name, python=python, repo=repo, cli_args=cli_args, env={"PYTHONUTF8": "1", "PYTHONPATH": repo},
                                  ledger_state="PREFLIGHTED", python_info={"executable": python, "role": "detach2-probe", "level": level},
                                  profile=spec["profile"], run=run, alive=alive, sleep=sleep, clock=clock, wait_s=wait_s,
                                  anchors_dir=anchors_dir, module="sim.runner.detach_probe",
                                  watch={"data": data, "sha256": sha,
                                         "targets": [{k: t[k] for k in ("role", "pid", "created")} for t in targets]})


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sample(run, schedule_pid: int, proc, stats: dict, alive_now: bool):
    """Uma foto da tabela de processos do SO; contabiliza instancias (motores) sem depender do heartbeat."""
    stats["samples"] += 1
    try:
        snap = sched.task_instances(sched.process_table(run, schedule_pid), schedule_pid, str(proc))
    except sched.SchedError:
        stats["sample_errors"] += 1
        return None
    count = len(snap["engines"])
    stats["engines_max"] = max(stats["engines_max"], count)
    stats["distinct_engine_pids"].update(e["pid"] for e in snap["engines"])
    if alive_now:
        stats["engines_min_while_alive"] = count if stats["engines_min_while_alive"] is None else min(stats["engines_min_while_alive"], count)
    return snap


def _second_start(run, name, schedule_pid, proc, stats, audit, sleep, alive):
    """2a partida (deve ser IGNORADA: IgnoreNew) seguida de amostragem densa da tabela de processos."""
    sched.start(run, name)
    stats["second_start_attempted"] = True
    audit.append("SECOND_START_ATTEMPT", {})
    for _ in range(DENSE_SAMPLES):
        pid = (detach.read_heartbeat(proc) or {}).get("pid")
        _sample(run, schedule_pid, proc, stats, bool(pid) and alive(pid))
        stats["dense_samples_after_second_start"] += 1
        sleep(DENSE_PAUSE_S)


def _start_records(proc: Path) -> int:
    path = Path(proc) / FACTS_LOG
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if '"phase": "start"' in line)


class _SafeAudit:
    """Auditoria que NUNCA derruba o monitor: falha ao gravar vira registro em `failures` (D2-11 reprova) e a causa fica visivel."""

    def __init__(self, audit) -> None:
        self.audit, self.failures = audit, []

    def append(self, event: str, payload: dict | None = None):
        try:
            return self.audit.append(event, payload)
        except OSError as error:
            self.failures.append({"event": event, "error": f"{type(error).__name__}: {error}"[:160]})
            return None

    def records(self) -> list:
        try:
            return self.audit.records()
        except (ValueError, UnicodeDecodeError, OSError):
            return []

    def verify(self) -> dict:
        return self.audit.verify()


def _link(entry: dict) -> dict:
    return {"pid": entry.get("pid"), "created": entry.get("created"), "name": entry.get("name")}


def probe_monitor(runs_root, name: str, *, level: str, run=None, alive=host.pid_alive, sleep=time.sleep, clock=time.time,
                  poll_s: int = 10, session_end_t: int | None = None, anchors_dir=None, stop_wait_s: int = 90,
                  query=None, snapshot=None) -> dict:
    """Processo NOVO (depois do fim do lancador): observa de fora, tenta uma 2a partida, pede STOP e coleta os fatos do monitor.
    Parentesco e instancias vem da TABELA DE PROCESSOS (nao do COM `GetInstances`, vazio no host real, nem do heartbeat).

    SIM-1.5G.3: o instante do fim do lancador e do CLI iniciador vem da VIGILIA duravel da propria tarefa (`watch.jsonl`), nao da hora
    de inicio deste monitor; `session_end_t` (hora informada pelo operador) so vale para evidencia historica `legacy-1`. A
    auditoria so registra `STOPPED` se um STOP foi pedido e o `exit.json` apareceu."""
    run = run or sched.run_default
    query, snapshot = query or watch.query_native, snapshot or watch.snapshot_native
    spec, proc = LEVELS[level], detach.proc_dir(runs_root, name)
    anchors = anchors_dir or (Path(runs_root) / sched.ANCHOR_DIR)
    audit = _SafeAudit(sched.Audit(proc, anchors, name, clock))
    trail = audit.verify()
    records = audit.records()
    by_event = {r["event"]: r for r in records}
    launch_doc = detach.read_launch(proc) or {}
    version = evidence_version(proc)
    launcher_pid = (by_event.get("STARTED") or {}).get("payload", {}).get("launcher_pid")
    who = sched.identity(run)
    schedule_pid = who.get("schedule_pid")
    first_seen_dead_t = None if launcher_pid is None or alive(launcher_pid) else int(clock())
    launcher_alive = bool(launcher_pid) and alive(launcher_pid)
    stats = {"samples": 0, "sample_errors": 0, "engines_max": 0, "engines_min_while_alive": None, "distinct_engine_pids": set(),
             "second_start_attempted": False, "dense_samples_after_second_start": 0}
    t0 = clock()
    while True:
        beats = beat_series(proc)
        bstats = beat_stats(beats)
        pid = (detach.read_heartbeat(proc) or {}).get("pid")
        if not pid or not alive(pid):
            break
        _sample(run, schedule_pid, proc, stats, True)
        if bstats["duration_s"] >= spec["required_s"]:
            break
        if not stats["second_start_attempted"] and bstats["duration_s"] >= min(120, spec["required_s"] // 3):
            _second_start(run, name, schedule_pid, proc, stats, audit, sleep, alive)
        if clock() - t0 > spec["cap_s"] + 300:
            break
        sleep(poll_s)
    pid = (detach.read_heartbeat(proc) or {}).get("pid")
    probe_alive_before_stop = bool(pid) and alive(pid)
    state_running = sched.info(run, name, want_xml=True)
    chain = sched.process_chain(run, pid) if probe_alive_before_stop else []
    beats_now = beat_series(proc)
    heartbeat = detach.read_heartbeat(proc) or {}
    if not stats["second_start_attempted"] and probe_alive_before_stop:
        _second_start(run, name, schedule_pid, proc, stats, audit, sleep, alive)
        pid = (detach.read_heartbeat(proc) or {}).get("pid")
        probe_alive_before_stop = bool(pid) and alive(pid)                       # a janela densa pode ter durado: reavalia
    final = _sample(run, schedule_pid, proc, stats, probe_alive_before_stop) if probe_alive_before_stop else None
    engines = (final or {}).get("engines") or []
    engine = engines[0] if len(engines) == 1 else {}
    engine_tree = engine.get("tree", [])
    facts_now = _read_json(proc / FACTS) or {}
    lineage_ok = watch.lineage_unchanged(facts_now.get("lineage"), query) if probe_alive_before_stop else None
    stop_t, requester = None, []
    if probe_alive_before_stop:
        try:
            requester = [_link(c) for c in watch.ancestors(os.getpid(), query=query, snapshot=snapshot)]
        except watch.WatchError:
            requester = []
        stop_t = int(clock())
        detach.request_stop(proc, "DETACH-2 acceptance: controlled STOP")
        audit.append("STOP_REQUESTED", {"t": stop_t, "requester": requester})
        waited = 0
        while not (proc / detach.EXIT).exists() and waited < stop_wait_s:
            sleep(1)
            waited += 1
        waited = 0
        while detach.read_exit_code(proc) is None and waited < 30:          # o `cmd` grava DEPOIS de o Python terminar
            sleep(1)
            waited += 1
    after = sched.info(run, name)
    exit_doc = detach.read_exit(proc)
    exit_code_txt = detach.read_exit_code(proc)
    elapsed = int(clock()) - int(launch_doc.get("t_launch") or clock())
    termination_doc = termination.classify_termination(exit_doc, exit_code_txt, after.get("last_result"), audit.records(), elapsed_s=elapsed,
                                                       limit_s=_limit_seconds(launch_doc.get("limit")))
    if probe_alive_before_stop and exit_doc:
        audit.append("STOPPED", {"exit": exit_doc, "termination": termination_doc["class"]})
    elif probe_alive_before_stop:
        audit.append("STOP_NOT_CONFIRMED", {"exit": exit_doc, "termination": termination_doc["class"]})
    else:                                                                       # nenhum STOP pedido: a trilha nao afirma um STOP
        audit.append("PROBE_ENDED_BEFORE_MONITOR", {"exit": exit_doc, "termination": termination_doc["class"]})
    parsed = sched.parse_task_xml(state_running.get("xml") or "<Task/>") if state_running.get("found") else {"triggers": None, "tags": []}
    bstats = beat_stats(beats_now, after=first_seen_dead_t, cross=session_end_t if version == EVIDENCE_LEGACY else None)
    session_end = ({"t": session_end_t, "crosses": bstats.get("crosses"), "gap_at_cross_s": bstats.get("gap_at_cross_s")}
                   if session_end_t is not None and version == EVIDENCE_LEGACY else None)
    wsum, launcher_watch = None, None
    if version == EVIDENCE_V2:
        durable = _durable_facts(proc, beat_series(proc), stop_t=stop_t, requester=requester, lineage_ok=lineage_ok, hint=session_end_t)
        wsum, launcher_watch, session_end = durable["watch"], durable["launcher_watch"], durable["session_end"]
        if durable["survived_durable_s"] is not None:
            bstats["survived_durable_s"] = durable["survived_durable_s"]
    monitor = {
        "level": level, "task": launch_doc.get("task"), "xml_sha256": launch_doc.get("xml_sha256"),
        "registered_verified": "EXPORT_VERIFIED" in by_event, "evidence_version": version,
        "schedule": {k: (final or {}).get("schedule", {}).get(k) if (final or {}).get("schedule") else None for k in ("pid", "name", "session")},
        "engine": {k: engine.get(k) for k in ("pid", "ppid", "name", "session")} if engine else {},
        "engine_tree": engine_tree, "heartbeat_pid_in_tree": bool(pid) and pid in engine_tree,
        "chain": chain, "launcher_pid": launcher_pid, "launcher_alive": launcher_alive,
        "first_seen_launcher_dead_t": first_seen_dead_t, "probe_alive_before_stop": probe_alive_before_stop,
        "heartbeat_age_s": max(int(clock()) - int(heartbeat.get("t_epoch", 0)), 0) if heartbeat else None, "beats": bstats,
        "triggers": parsed.get("triggers"), "restart_on_failure": "RestartOnFailure" in parsed.get("tags", []),
        "instances": {"engines_max": stats["engines_max"], "engines_min_while_alive": stats["engines_min_while_alive"],
                      "distinct_engine_pids": sorted(stats["distinct_engine_pids"]), "samples": stats["samples"],
                      "sample_errors": stats["sample_errors"], "second_start_attempted": stats["second_start_attempted"],
                      "dense_samples_after_second_start": stats["dense_samples_after_second_start"],
                      "start_records": _start_records(proc), "beat_pids": sorted({b.get("pid") for b in beats_now})},
        "exit": exit_doc, "exit_code_txt": exit_code_txt, "termination": termination_doc,
        "task_state_after": after.get("state"), "last_result_after": after.get("last_result"),
        "audit_check": {"ok": trail["ok"], "problems": trail["problems"]}, "audit_write_failures": list(audit.failures),
        "watch": wsum, "launcher_watch": launcher_watch, "stop": {"t": stop_t, "requester": requester},
        "session_end": session_end,
    }
    _atomic_json(proc / MONITOR_JSON, monitor)
    return monitor


def _durable_facts(proc, beats_final: list, *, stop_t, requester: list, lineage_ok, hint) -> dict:
    """Tudo o que a v2 deriva da VIGILIA duravel + batimentos + identidade do solicitante: calculado dos ARQUIVOS (monitor, finish e
    replay usam esta mesma funcao; o monitor gravado nao e confiado)."""
    wsum = watch.summarize(proc)
    launcher = wsum["ended"].get("launcher")
    survived = (beats_final[-1]["t"] - launcher["first_seen_ended_t"]
                if launcher and isinstance(launcher.get("first_seen_ended_t"), int) and beats_final else None)
    initiator = next((t for t in (watch.read_watch(proc) or {}).get("targets", []) if t.get("role") == "initiator"), None)
    session_end, ended = None, wsum["ended"].get("initiator")
    if initiator is not None:
        session_end = {"source": "watch", "role": "initiator", "how": (ended or {}).get("how"),
                       "last_seen_alive_t": (ended or {}).get("last_seen_alive_t"), "first_seen_ended_t": (ended or {}).get("first_seen_ended_t"),
                       "period_s": (ended or {}).get("period_s"), "unknown_observations": (ended or {}).get("unknown_observations"),
                       "crosses": None, "gap_at_cross_s": None, "survived_after_end_s": None, "stop_after_end": None,
                       "requester_outside_initiator": watch.outside(requester, initiator) if requester else None,
                       "probe_lineage_unchanged": lineage_ok, "operator_hint_session_end_t": hint}
        if ended and isinstance(ended.get("last_seen_alive_t"), int) and isinstance(ended.get("first_seen_ended_t"), int) and beats_final:
            cross = beat_stats(beats_final, after=ended["first_seen_ended_t"], cross=ended["last_seen_alive_t"])
            session_end.update(crosses=cross.get("crosses"), gap_at_cross_s=cross.get("gap_at_cross_s"),
                               survived_after_end_s=cross.get("survived_after_s"),
                               stop_after_end=bool(stop_t is not None and stop_t > ended["first_seen_ended_t"]))
    return {"watch": {k: wsum[k] for k in ("armed", "integrity_ok", "records", "corrupt_lines", "observing", "period_s")},
            "launcher_watch": launcher, "survived_durable_s": survived, "session_end": session_end}


def _refresh_from_evidence(proc, monitor: dict) -> dict:
    """Monitor gravado + versao da evidencia e (v2) campos da vigilia RECALCULADOS dos arquivos: adulterar `watch.jsonl`, `watch.json`
    ou os batimentos depois do monitor reprova em vez de ser julgado pelo que o monitor gravou."""
    version = evidence_version(proc)
    out = {**monitor, "evidence_version": version}
    if version == EVIDENCE_V2:
        stop = monitor.get("stop") or {}
        previous = monitor.get("session_end") or {}
        durable = _durable_facts(proc, beat_series(proc), stop_t=stop.get("t"), requester=stop.get("requester") or [],
                                 lineage_ok=previous.get("probe_lineage_unchanged"), hint=previous.get("operator_hint_session_end_t"))
        out["watch"], out["launcher_watch"], out["session_end"] = durable["watch"], durable["launcher_watch"], durable["session_end"]
        beats = dict(monitor.get("beats") or {})
        beats["survived_durable_s"] = durable["survived_durable_s"]
        out["beats"] = beats
    return out


def _limit_seconds(limit) -> int | None:
    match = sched.LIMIT_RE.match(str(limit or ""))
    if not match or not any(match.groups()):
        return None
    return int(match.group(1) or 0) * 3600 + int(match.group(2) or 0) * 60


def evidence_manifest(proc: Path) -> dict:
    proc = Path(proc)
    files = {p.relative_to(proc).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(proc.rglob("*"))
             if p.is_file() and p.name not in (EVIDENCE_MANIFEST,) and not p.name.endswith(".tmp")}
    return files


REQUIRED_EVIDENCE = ("launch.json", "facts.json", "beats.jsonl", "sched_audit.jsonl", MONITOR_JSON, "evidence_pre_removal/manifest.json")
REQUIRED_EVIDENCE_V2 = (watch.WATCH_JSON, watch.WATCH_LOG)


def verify_evidence(proc: Path) -> bool:
    """Evidencias minimas presentes (v2: tambem `watch.json`/`watch.jsonl`) e o manifesto pre-remocao confere (hash por arquivo)."""
    proc = Path(proc)
    required = REQUIRED_EVIDENCE + (REQUIRED_EVIDENCE_V2 if (proc / detach.LAUNCH).is_file() and "evidence_schema" in (detach.read_launch(proc) or {}) else ())
    if any(not (proc / rel).is_file() for rel in required):
        return False
    folder = proc / sched.EVIDENCE_DIR
    manifest = _read_json(folder / "manifest.json") or {}
    if not manifest:
        return False
    return all((folder / n).is_file() and hashlib.sha256((folder / n).read_bytes()).hexdigest() == h for n, h in manifest.items())


def probe_finish(runs_root, name: str, *, level: str, run=None, alive=host.pid_alive, clock=time.time, anchors_dir=None) -> dict:
    """Preserva as evidencias, remove a tarefa (verificado), confere orfaos/trilha/manifesto e julga. Nao apaga evidencia."""
    run = run or sched.run_default
    proc = detach.proc_dir(runs_root, name)
    anchors = anchors_dir or (Path(runs_root) / sched.ANCHOR_DIR)
    audit = sched.Audit(proc, anchors, name, clock)
    monitor = _read_json(proc / MONITOR_JSON)
    if monitor is None:
        raise detach.DetachError("monitor ausente: execute `sched-probe --phase monitor` antes de encerrar")
    removal_ok, write_failures = True, list(monitor.get("audit_write_failures") or [])
    try:
        removal = sched.remove_verified(run, name, proc, audit)
    except sched.RemovalNotVerified as error:
        removal, removal_ok = {"error": str(error)}, False
    except OSError as error:                                              # a trilha/evidencia nao gravou: nunca sucesso (causa preservada)
        removal, removal_ok = {"error": f"{type(error).__name__}: {error}"[:200]}, False
        write_failures.append({"event": "remove_verified", "error": removal["error"]})
    try:
        found = sched.find_orphans(run, name, alive=alive)
        orphans, excluded, orphan_count = found["orphans"], found["excluded"], len(found["orphans"])
    except sched.SchedError:
        orphans, excluded, orphan_count = [], [], None                      # fail-closed: arvore do inspetor indeterminada
    folder = sched.list_folder(run)
    trail = audit.verify()
    manifest = evidence_manifest(proc)
    (proc / EVIDENCE_MANIFEST).write_bytes(json.dumps(manifest, sort_keys=True).encode("utf-8"))
    evidence_ok = verify_evidence(proc)
    final = {**_refresh_from_evidence(proc, monitor), "removal_verified": removal_ok and (removal.get("removed") or removal.get("already_absent")),
             "orphan_processes": orphan_count, "folder_clean": not folder.get("tasks"), "audit_ok": trail["ok"],
             "evidence_manifest_ok": evidence_ok, "audit_write_failures": write_failures}
    facts = _read_json(proc / FACTS) or {}
    verdict = judge_detach2(facts, final, level)
    doc = {"verdict": verdict, "removal": removal, "orphans": orphans, "inspector_tree_excluded": excluded,
           "evidence_files": len(manifest)}
    _atomic_json(proc / VERDICT_JSON, doc)
    return doc


def judge_evidence_dir(proc, level: str, observed: dict | None = None) -> dict:
    """REPLAY: julga de novo uma pasta `.proc` preservada. A versao da evidencia e recalculada (v2 ou historica verificavel; senao
    nao julgavel). Itens medidos so no encerramento original (orfaos, pasta do Scheduler) vem de `observed`, nunca inventados."""
    proc = Path(proc)
    monitor = _read_json(proc / MONITOR_JSON)
    if monitor is None:
        raise detach.DetachError("monitor ausente: nada a julgar")
    trail = sched.Audit(proc, Path(proc).parent / sched.ANCHOR_DIR, proc.name[: -len(detach.PROC_SUFFIX)]).verify()
    records = sched.Audit(proc, proc, proc.name[: -len(detach.PROC_SUFFIX)]).records()
    removed = any(r.get("event") == "UNREGISTERED" and (r.get("payload") or {}).get("verified_absent") for r in records)
    final = {**_refresh_from_evidence(proc, monitor), "removal_verified": removed, "audit_ok": trail["ok"],
             "evidence_manifest_ok": verify_evidence(proc), "orphan_processes": None, "folder_clean": None, **(observed or {})}
    return judge_detach2(_read_json(proc / FACTS) or {}, final, level)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="sim.runner.detach_probe")
    parser.add_argument("--proc-dir", required=True)
    parser.add_argument("--interactive-session", type=int, required=True)
    parser.add_argument("--detach2", action="store_true", help="sonda DETACH-2 (Task Scheduler): fatos incrementais")
    parser.add_argument("--cap-s", type=int, default=1500)
    args = parser.parse_args(argv)
    if args.detach2:
        facts = collect2(Path(args.proc_dir), interactive_session=args.interactive_session, cap_s=args.cap_s)
        return process_exit_code(facts)
    collect(Path(args.proc_dir), interactive_session=args.interactive_session)           # DETACH-1 (WMI, reprovado): inalterado
    return 0


def process_exit_code(facts) -> int:
    """Codigo do PROCESSO da sonda DETACH-2 = o gravado em `exit.json` (tabela unica). Sem codigo valido => EXCEPTION (nunca 0)."""
    code = facts.get("exit_code") if isinstance(facts, dict) else None
    if isinstance(code, int) and not isinstance(code, bool) and code in termination.EXIT_CODES.values():
        return code
    return termination.EXIT_CODES["EXCEPTION"]


if __name__ == "__main__":
    raise SystemExit(main())
