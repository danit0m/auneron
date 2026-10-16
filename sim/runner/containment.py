"""
SIM-1.5G.4 -- contencao da arvore de processos do Task Scheduler e recuperacao segura (achado F-1 do L1-C).

Fato medido no L1-C: o limite do Scheduler encerra SO o `cmd.exe`; o launcher do venv e o interpretador Python seguem vivos e
ativos. O Job `KILL_ON_JOB_CLOSE` visto nos `facts.json` e o do launcher do venv (flags 0x3000, so o interpretador dentro,
`SILENT_BREAKAWAY_OK`): nao protege contra a morte do `cmd`. Arquitetura (B + C):

* B -- vigia cooperativa: o processo longo grava a SUA cadeia de identidade (`identity.json`: PID + criacao do interpretador, do
  launcher e do motor `cmd`) e uma thread observa o motor. `ABSENT`/`pid_reused` confirmado em 2 leituras => gatilho; `UNKNOWN`
  nunca dispara. No gatilho: `engine_lost.json`, caminho gracioso e, passada a graca, `exit.json` ENGINE_LOST (6) + saida dura;
* C -- recuperacao externa por identidade COMPROVADA: so processos do run (P1 = no ledger + criacao igual + marcador exato na linha
  de comando; P2 = filho vivo de P1/P2 com criacao >= a do pai) sao encerrados, SEMPRE por handle retido (o Windows nao recicla o
  PID enquanto o handle esta aberto), gracioso antes de forcado, forcado so com confirmacao explicita. P3 (so o marcador) e
  `UNKNOWN` sao relatados e BLOQUEIAM; nunca sao encerrados.

Regra de ouro: tarefa ausente, `Ready` ou `LastTaskResult 0x41306` NAO provam que o runner terminou. Estado do run = f(identidades).
Nos testes o SO e injetado (`ops`: `query`, `snapshot`, `open`); nenhum processo real e encerrado por eles.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from sim.runner import detach
from sim.runner import sched
from sim.runner import termination
from sim.runner import watch
from sim.runner.errors import RunnerError

IDENTITY_JSON, ENGINE_LOST_JSON, RECOVERY_DIR = "identity.json", "engine_lost.json", "recovery"
WATCH_INTERVAL_S, CONFIRMATIONS = 2, 2
PROBE_GRACE_S, NH_GRACE_S = 5, 120
TERMINATE_WAIT_S = 5
STATES = ("RUNNING", "ORPHANED_RUNNING", "SUPERVISION_LOST", "ENDED_CLEAN", "ENDED_UNCLEAN", "UNKNOWN")


class ContainmentError(RunnerError):
    pass


class ContainmentUnavailable(ContainmentError):
    """Politica de partida: o motor da tarefa nao pode ser verificado => o run NAO inicia (fail-closed)."""


def norm(text) -> str:
    return " ".join(str(text or "").split())


def native_ops():
    return watch.NativeOps


# ---------------------------------------------------------------------------------------------------------------------
# marcador de propriedade e ledger de identidade
# ---------------------------------------------------------------------------------------------------------------------
def owner_marker(proc, launch_doc=None) -> str | None:
    """Texto EXATO que a linha de comando de todo processo do run carrega. `owner_marker` do launch.json; senao (evidencia antiga)
    `--proc-dir "<proc>"` ou `--run-id <id>` achados na linha de comando gravada. Sem marcador => nenhum processo pode ser P1."""
    doc = detach.read_launch(Path(proc)) if launch_doc is None else launch_doc
    doc = doc or {}
    marker = doc.get("owner_marker")
    if isinstance(marker, str) and marker.strip():
        return norm(marker)
    cmd = norm(doc.get("cmdline"))
    token = norm(f'--proc-dir "{proc}"')
    if token in cmd:
        return token
    run_id = doc.get("run_id")
    if isinstance(run_id, str) and run_id and f"--run-id {run_id}" in cmd:
        return f"--run-id {run_id}"
    return None


def read_identity(proc) -> dict | None:
    try:
        doc = json.loads((Path(proc) / IDENTITY_JSON).read_bytes().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == 1 else None


def arm(proc, *, ops, cmdline_of, schedule_pid, pid: int | None = None, clock=time.time, marker: str | None = None) -> dict:
    """Grava `identity.json` (atomico) com a cadeia de identidade do PROPRIO processo e verifica o motor. Motor verificado = ancestral
    `cmd.exe` cujo pai e o servico Schedule gravado no launch e cuja linha de comando carrega o marcador do run. Falha ao gravar =>
    `OSError` (quem chama decide; nunca silencioso)."""
    proc, pid = Path(proc), os.getpid() if pid is None else pid
    marker = norm(marker) if marker else owner_marker(proc)
    reason, engine = None, None
    try:
        chain = watch.ancestors(pid, query=ops.query, snapshot=ops.snapshot)
        table = ops.snapshot()
    except watch.WatchError as error:
        chain, table, reason = [], {}, f"tabela de processos indisponivel: {error}"
    entries = []
    index = next((i for i, c in enumerate(chain) if i > 0 and str(c["name"]).lower() == "cmd.exe"), None)
    for i, link in enumerate(chain if index is None else chain[: index + 1]):
        role = "interpreter" if i == 0 else ("engine" if i == index else "launcher")
        entries.append({"role": role, "pid": link["pid"], "created": link["created"], "name": link["name"]})
    if reason is None:
        if index is None:
            reason = "nenhum ancestral cmd.exe (motor nao identificado)"
        else:
            engine = entries[-1]
            parent_pid = (table.get(engine["pid"]) or {}).get("ppid")
            parent = table.get(parent_pid) or {}
            if not (isinstance(schedule_pid, int) and not isinstance(schedule_pid, bool) and parent_pid == schedule_pid
                    and str(parent.get("name", "")).lower() == "svchost.exe"):
                reason = "o pai do cmd nao e o servico Schedule gravado no launch"
            elif not marker:
                reason = "sem marcador de propriedade (owner_marker) para conferir o cmd"
            else:
                cmd = cmdline_of(engine["pid"])
                if not cmd or marker not in norm(cmd):
                    reason = "a linha de comando do cmd nao carrega o marcador do run"
    verified = reason is None
    me = entries[0] if entries else {"pid": pid, "created": None}
    doc = {"schema": 1, "proc_dir": str(proc), "marker": marker, "schedule_pid": schedule_pid, "chain": entries,
           "engine": engine if verified else None, "engine_verified": verified, "reason": reason,
           "written_by": {"pid": me["pid"], "created": me["created"]}, "t": int(clock())}
    detach._atomic_write(proc / IDENTITY_JSON, doc)
    return doc


# ---------------------------------------------------------------------------------------------------------------------
# B -- vigia cooperativa de perda do motor
# ---------------------------------------------------------------------------------------------------------------------
class Guard:
    """Observa o motor (PID + criacao). Gatilho: `confirmations` leituras CONSECUTIVAS de `ENDED` (ausente ou PID reutilizado).
    `UNKNOWN` nunca dispara nem acumula: apenas conta como alerta."""

    def __init__(self, engine: dict, *, ops, clock=time.time, interval_s: float = WATCH_INTERVAL_S, confirmations: int = CONFIRMATIONS,
                 on_lost=None) -> None:
        self.engine = {"pid": engine["pid"], "created": engine["created"]}
        self.ops, self.clock, self.interval_s, self.confirmations, self.on_lost = ops, clock, interval_s, confirmations, on_lost
        self.lost, self.unknown_observations, self.samples = None, 0, 0
        self._streak, self._first_t, self._how = 0, None, None
        self._stop, self._thread = threading.Event(), None

    def step(self) -> str:
        verdict, how, detail = watch.evaluate(self.engine, self.ops.query)
        self.samples += 1
        now = int(self.clock())
        if verdict == "ENDED":
            self._streak += 1
            self._first_t = self._first_t if self._first_t is not None else now
            self._how = how
            if self._streak >= self.confirmations and self.lost is None:
                self.lost = {"engine": dict(self.engine), "how": how, "first_seen_ended_t": self._first_t, "confirmed_t": now,
                             "confirmations": self._streak, "samples": self.samples, "detail": str(detail)[:120]}
                if self.on_lost:
                    self.on_lost(self.lost)
        else:
            self._streak, self._first_t = 0, None
            if verdict == watch.UNKNOWN:
                self.unknown_observations += 1
        return verdict

    def _run(self) -> None:
        while not self._stop.is_set() and self.lost is None:
            self.step()
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="engine-guard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


class Containment:
    """Guarda + politica: ao perder o motor grava `engine_lost.json`, pede o encerramento gracioso e, passada a graca, executa a saida dura.
    `graceful()` e `hard_exit()` sao do dono (sonda/NH)."""

    def __init__(self, proc, identity: dict, *, ops, graceful, hard_exit, grace_s: float, clock=time.time, interval_s: float = WATCH_INTERVAL_S,
                 confirmations: int = CONFIRMATIONS, timer=threading.Timer) -> None:
        self.proc, self.identity, self.graceful, self.hard_exit, self.grace_s = Path(proc), identity, graceful, hard_exit, grace_s
        self.timer_factory, self.evidence, self.write_error, self._finished = timer, None, None, False
        self.guard = Guard(identity["engine"], ops=ops, clock=clock, interval_s=interval_s, confirmations=confirmations, on_lost=self._on_lost)
        self._timer = None

    @property
    def lost(self) -> bool:
        return self.evidence is not None

    def _on_lost(self, evidence: dict) -> None:
        self.evidence = evidence
        try:
            detach._atomic_write(self.proc / ENGINE_LOST_JSON, {**evidence, "grace_s": int(self.grace_s)})
        except OSError as error:                                                 # a perda do motor nao pode depender de gravar o aviso
            self.write_error = f"{type(error).__name__}: {error}"[:200]
        try:
            self.graceful()
        finally:
            self._timer = self.timer_factory(self.grace_s, self._hard)
            self._timer.daemon = True
            self._timer.start()

    def _hard(self) -> None:
        if not self._finished:
            self.hard_exit()

    def start(self) -> None:
        self.guard.start()

    def finish(self) -> None:
        """O processo terminou normalmente: cancela a saida dura e para a vigia."""
        self._finished = True
        self.guard.stop()
        if self._timer is not None:
            self._timer.cancel()


def install(proc, *, ops, cmdline_of, schedule_pid, graceful, hard_exit, grace_s: float, applicable: bool = True, clock=time.time,
            require: bool = False, interval_s: float = WATCH_INTERVAL_S, pid: int | None = None):
    """Grava o ledger, verifica o motor e inicia a vigia. -> (identity|None, Containment|None).
    `applicable=False` (fora do Scheduler): nada a vigiar. `require=True` e motor nao verificado => `ContainmentUnavailable`
    (politica de partida fail-closed dos runs RUN/REHEARSAL)."""
    if not applicable:
        return None, None
    identity = arm(proc, ops=ops, cmdline_of=cmdline_of, schedule_pid=schedule_pid, clock=clock, pid=pid)
    if not identity["engine_verified"]:
        if require:
            raise ContainmentUnavailable(f"motor da tarefa nao verificavel: {identity['reason']}")
        return identity, None
    guard = Containment(proc, identity, ops=ops, graceful=graceful, hard_exit=hard_exit, grace_s=grace_s, clock=clock, interval_s=interval_s)
    guard.start()
    return identity, guard


# ---------------------------------------------------------------------------------------------------------------------
# C -- posse (P1/P2/P3), estados de liveness e encerramento por handle
# ---------------------------------------------------------------------------------------------------------------------
def ledger_entries(proc) -> list:
    """Identidades que o PROPRIO run gravou: `identity.json` (cadeia) e `facts.json:lineage` (sondas anteriores). Sem duplicatas."""
    out, seen = [], set()

    def add(role, entry):
        if not isinstance(entry, dict) or any(not isinstance(entry.get(k), int) or isinstance(entry.get(k), bool) for k in ("pid", "created")):
            return
        key = (entry["pid"], entry["created"])
        if key not in seen:
            seen.add(key)
            out.append({"role": role, "pid": entry["pid"], "created": entry["created"], "name": entry.get("name")})

    ident = read_identity(proc)
    chain = (ident or {}).get("chain")
    for link in (chain if isinstance(chain, list) else []):
        if isinstance(link, dict):
            add(link.get("role") or "chain", link)
    try:
        lineage = (json.loads((Path(proc) / "facts.json").read_bytes().decode("utf-8")) or {}).get("lineage") or {}
    except (OSError, ValueError, UnicodeDecodeError):
        lineage = {}
    for level in ("self", "parent", "grandparent"):
        add(level, lineage.get(level))
    return out


def enumerate_owned(proc, *, ops, table, marker: str | None, exclude=()) -> dict:
    """Processos do run, por identidade. `table`: linhas {pid, ppid, name, cmd} (CIM) ou None. Cada entrada: {pid, created, role, proof,
    state, ...}; `handles` retem os handles ate `close_owned`. UNKNOWN (identidade ilegivel, linha de comando indisponivel) vai em
    `unknown`; P3 (so o marcador) em `suspects`: nunca encerrados."""
    exclude = set(exclude)
    cache = {}

    def rows() -> dict:                                                         # a tabela (CIM) so e lida se algum processo do ledger estiver vivo
        if "by_pid" not in cache:
            data = table() if callable(table) else table
            cache["by_pid"] = {int(p["pid"]): p for p in (data or []) if isinstance(p.get("pid"), int)}
        return cache["by_pid"]

    entries, unknown, suspects, handles = [], [], [], {}

    def keep_handle(pid, handle):
        handles[pid] = handle

    for item in ledger_entries(proc):
        opened = ops.open(item["pid"])
        state = opened["state"]
        if state == watch.ABSENT:
            entries.append({**item, "proof": "P1", "state": "ABSENT", "how": "absent"})
            continue
        if state != watch.ALIVE:
            unknown.append({**item, "error": opened.get("error")})
            continue
        if opened["created"] != item["created"]:                                # PID reutilizado: o original terminou; o novo ocupante NAO e do run
            opened["handle"].close()
            entries.append({**item, "proof": "P1", "state": "ABSENT", "how": "pid_reused"})
            continue
        row = rows().get(item["pid"])
        cmd = None if row is None else row.get("cmd")
        if not marker or cmd is None:
            opened["handle"].close()
            unknown.append({**item, "error": "linha de comando/marcador indisponivel: posse nao comprovada"})
            continue
        if marker not in norm(cmd):
            opened["handle"].close()
            suspects.append({**item, "proof": "P3", "state": "ALIVE", "why": "identidade do ledger sem o marcador na linha de comando"})
            continue
        keep_handle(item["pid"], opened["handle"])
        entries.append({**item, "proof": "P1", "state": "ALIVE", "ppid": (row or {}).get("ppid")})
    # P2: filhos vivos de processos do run, com criacao >= a do pai (cadeia integra)
    try:
        snap = ops.snapshot()
    except watch.WatchError:
        snap = None
        unknown.append({"pid": None, "role": "snapshot", "error": "tabela de processos indisponivel: descendencia nao verificada"})
    known = ({e["pid"] for e in entries} | {s["pid"] for s in suspects}
             | {unknown_pid for unknown_pid in (u["pid"] for u in unknown) if unknown_pid is not None})
    frontier = [e for e in entries if e["state"] == "ALIVE"]
    while snap is not None and frontier:
        parent = frontier.pop()
        for pid, info in snap.items():
            if info["ppid"] != parent["pid"] or pid in known or pid in exclude:
                continue
            known.add(pid)
            opened = ops.open(pid)
            if opened["state"] == watch.ABSENT:
                continue
            if opened["state"] != watch.ALIVE:
                unknown.append({"pid": pid, "role": "descendant", "name": info.get("name"), "error": opened.get("error")})
                continue
            if opened["created"] < parent["created"]:                            # ppid obsoleto: nao e filho deste processo
                opened["handle"].close()
                suspects.append({"pid": pid, "created": opened["created"], "name": info.get("name"), "proof": "P3", "state": "ALIVE",
                                 "why": "criacao anterior a do suposto pai (ppid obsoleto)"})
                continue
            keep_handle(pid, opened["handle"])
            child = {"pid": pid, "created": opened["created"], "name": info.get("name"), "role": "descendant", "proof": "P2",
                     "state": "ALIVE", "ppid": parent["pid"]}
            entries.append(child)
            frontier.append(child)
    # P3: processos da tabela com o marcador que nao sao do run nem da arvore do inspetor
    owned_pids = {e["pid"] for e in entries if e["state"] == "ALIVE"}
    for pid, row in (rows().items() if "by_pid" in cache else ()):
        if pid in owned_pids or pid in exclude or pid in {s["pid"] for s in suspects}:
            continue
        if marker and marker in norm(row.get("cmd")) and str(row.get("name", "")).lower() in ("python.exe", "pythonw.exe", "cmd.exe"):
            opened = ops.query(pid)
            if opened.get("state") == watch.ALIVE:
                suspects.append({"pid": pid, "created": opened.get("created"), "name": row.get("name"), "proof": "P3", "state": "ALIVE",
                                 "why": "so o marcador na linha de comando (fora do ledger)"})
    return {"entries": entries, "unknown": unknown, "suspects": suspects, "handles": handles,
            "alive": [e for e in entries if e["state"] == "ALIVE" and e["proof"] in ("P1", "P2")]}


def close_owned(owned: dict) -> None:
    for handle in list(owned.get("handles", {}).values()):
        try:
            handle.close()
        except Exception:                                                         # noqa: BLE001 - fechar nunca mascara o resultado
            pass
    owned["handles"] = {}


def summary(owned: dict) -> dict:
    """Resumo SERIALIZAVEL (sem handles): usado por reconcile, P15, classificador e relatorios."""
    return {"alive": len(owned["alive"]), "unknown": len(owned["unknown"]), "suspects": len(owned["suspects"]),
            "alive_pids": [(e["pid"], e["created"], e["proof"]) for e in owned["alive"]],
            "unknown_detail": [{k: u.get(k) for k in ("pid", "role", "error")} for u in owned["unknown"]],
            "suspect_detail": [{k: s.get(k) for k in ("pid", "why")} for s in owned["suspects"]]}


def liveness_state(owned: dict, *, task_found: bool, engine_alive: bool | None, terminal_evidence: bool) -> str:
    """Estado do run, DERIVADO DE IDENTIDADES. Ausencia de tarefa, `Ready` ou 0x41306 nunca produzem ENDED sozinhos.
    `owned` pode ser o resultado de `enumerate_owned` ou de `summary`."""
    unknown = len(owned["unknown"]) if isinstance(owned.get("unknown"), list) else owned.get("unknown", 0)
    alive = len(owned["alive"]) if isinstance(owned.get("alive"), list) else owned.get("alive", 0)
    suspects = len(owned["suspects"]) if isinstance(owned.get("suspects"), list) else owned.get("suspects", 0)
    if unknown or suspects:
        return "UNKNOWN"
    if alive:
        if not task_found:
            return "SUPERVISION_LOST"
        return "RUNNING" if engine_alive else "ORPHANED_RUNNING"
    return "ENDED_CLEAN" if terminal_evidence else "ENDED_UNCLEAN"


def owned_summary(proc, run, *, ops=None, table_fn=None, exclude=None) -> dict:
    """Para reconcile/P15: enumera, resume e FECHA os handles. Sem ledger => nada pertence ao run (alive 0). Tabela indisponivel =>
    qualquer processo do ledger ainda vivo fica UNKNOWN (nunca 'terminado')."""
    ops = ops or native_ops()
    marker = owner_marker(proc)
    if not ledger_entries(proc):                                                # sem ledger nada pertence comprovadamente ao run
        return {"alive": 0, "unknown": 0, "suspects": 0, "alive_pids": [], "unknown_detail": [], "suspect_detail": []}

    def table():
        try:
            return table_fn() if table_fn else _table(proc, run)
        except sched.SchedError:
            return None

    excluded = exclude if exclude is not None else _inspector(ops)
    owned = enumerate_owned(proc, ops=ops, table=table, marker=marker, exclude=excluded)
    try:
        return summary(owned)
    finally:
        close_owned(owned)


def _inspector(ops) -> set:
    try:
        return {c["pid"] for c in watch.ancestors(os.getpid(), query=ops.query, snapshot=ops.snapshot)}
    except watch.WatchError:
        return {os.getpid()}


def _table(proc, run) -> list:
    ident = read_identity(proc) or {}
    launch = detach.read_launch(Path(proc)) or {}
    schedule_pid = ident.get("schedule_pid") or launch.get("schedule_pid") or sched.identity(run).get("schedule_pid")
    return sched.process_table(run, schedule_pid)


def terminate_owned(owned: dict, *, confirm: bool, sleep=time.sleep, wait_s: float = TERMINATE_WAIT_S) -> dict:
    """Encerra, POR HANDLE, so P1/P2 vivos, da folha para a raiz. Exige `confirm`; qualquer UNKNOWN/P3 bloqueia. Antes de cada
    encerramento reverifica a criacao lida pelo proprio handle."""
    result = {"terminated": [], "blocked": [], "skipped": []}
    if owned["unknown"] or owned["suspects"]:
        result["blocked"].append("identidade UNKNOWN ou processo P3 presente: nenhum encerramento forcado")
        return result
    if confirm is not True:
        result["blocked"].append("encerramento forcado exige confirmacao explicita")
        return result
    depth = {}

    def level(entry, guard=0):
        parent = next((e for e in owned["alive"] if e["pid"] == entry.get("ppid")), None)
        return 0 if parent is None or guard > 32 else 1 + level(parent, guard + 1)

    for entry in owned["alive"]:
        depth[entry["pid"]] = level(entry)
    for entry in sorted(owned["alive"], key=lambda e: -depth[e["pid"]]):
        handle = owned["handles"].get(entry["pid"])
        if handle is None:
            result["blocked"].append(f"PID {entry['pid']}: sem handle retido")
            continue
        if handle.created() != entry["created"]:
            result["blocked"].append(f"PID {entry['pid']}: criacao lida pelo handle difere do ledger")
            continue
        alive = handle.alive()
        if alive is None:
            result["blocked"].append(f"PID {entry['pid']}: estado indeterminado")
            continue
        if alive is False:
            result["skipped"].append(entry["pid"])
            continue
        if not handle.terminate():
            result["blocked"].append(f"PID {entry['pid']}: TerminateProcess falhou")
            continue
        waited = 0.0
        while handle.alive() is not False and waited < wait_s:
            sleep(0.2)
            waited += 0.2
        if handle.alive() is False:
            result["terminated"].append(entry["pid"])
        else:
            result["blocked"].append(f"PID {entry['pid']}: continua vivo apos o encerramento")
    return result


def snapshot_proc(proc, *, clock=time.time) -> dict:
    """Preserva os arquivos da pasta do run ANTES de qualquer acao de recuperacao: copia + manifesto (sha256). Falha => `OSError`."""
    proc = Path(proc)
    target = proc / RECOVERY_DIR / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(int(clock())))
    index = 1
    while target.exists():
        target = target.with_name(target.name.split("-")[0] + f"-{index}")
        index += 1
    target.mkdir(parents=True)
    manifest = {}
    for item in sorted(p for p in proc.iterdir() if p.is_file()):
        data = item.read_bytes()
        (target / item.name).write_bytes(data)
        manifest[item.name] = hashlib.sha256(data).hexdigest()
    (target / "manifest.json").write_bytes(json.dumps(manifest, sort_keys=True).encode("utf-8"))
    return {"dir": str(target), "files": len(manifest)}


def recover(proc, run, name: str, *, ops=None, table_fn=None, confirm: bool = False, grace_s: float = 20, sleep=time.sleep, clock=time.time,
            anchors_dir=None, exclude=None, request_stop: bool = True, preserve: bool = True) -> dict:
    """Recuperacao externa: preserva evidencia, enumera por identidade, gracioso, forcado (so `confirm`), verifica. NUNCA remove a
    tarefa. A evidencia e preservada ANTES de qualquer acao; a trilha so ganha eventos RECOVERY_* quando ha o que relatar (processo
    vivo, UNKNOWN ou P3). -> {blocked: [...], actions: [...], owned_alive_after, summary, snapshot}."""
    proc, ops = Path(proc), ops or native_ops()
    audit = sched.Audit(proc, anchors_dir or (proc.parent / sched.ANCHOR_DIR), name, clock)
    out = {"blocked": [], "actions": [], "snapshot": None}

    def note(event, payload=None):
        try:
            audit.append(event, payload or {})
            return True
        except OSError as error:
            out["blocked"].append(f"trilha nao gravou {event}: {error}")
            return False

    if preserve:
        try:
            out["snapshot"] = snapshot_proc(proc, clock=clock)
        except OSError as error:
            out["blocked"].append(f"evidencia nao preservada: {error}")
            out["owned_alive_after"] = None
            return out
    marker = owner_marker(proc)
    excluded = exclude if exclude is not None else _inspector(ops)

    def look():
        def table():
            try:
                return table_fn() if table_fn else _table(proc, run)
            except sched.SchedError:
                return None

        return enumerate_owned(proc, ops=ops, table=table, marker=marker, exclude=excluded)

    owned = look()
    out["summary"] = summary(owned)
    if not (owned["alive"] or owned["unknown"] or owned["suspects"]):
        close_owned(owned)
        out["owned_alive_after"] = 0
        return out
    try:
        if not (note("RECOVERY_STARTED", {"confirm": bool(confirm), "grace_s": int(grace_s)})
                and note("EVIDENCE_SNAPSHOT", out["snapshot"] or {"preserved_by": "caller"})):
            out["owned_alive_after"] = len(owned["alive"])
            return out
        for item in owned["alive"]:
            note("ORPHAN_FOUND", {"pid": item["pid"], "created": item["created"], "proof": item["proof"], "role": item.get("role")})
        if owned["unknown"] or owned["suspects"]:
            out["blocked"].append("identidade UNKNOWN ou processo P3: nada e encerrado")
            note("RECOVERY_BLOCKED", {"unknown": len(owned["unknown"]), "suspects": len(owned["suspects"])})
            out["owned_alive_after"] = len(owned["alive"])
            return out
        if request_stop and not (proc / detach.STOP).exists():
            try:
                detach.request_stop(proc, "recovery: orphan processes of the run")
                out["actions"].append("STOP")
                note("ORPHAN_STOP_REQUESTED", {"pids": [e["pid"] for e in owned["alive"]]})
            except (OSError, detach.DetachError) as error:
                out["blocked"].append(f"STOP nao gravado: {error}")
        waited = 0.0
        while waited < grace_s and any(owned["handles"].get(e["pid"]) and owned["handles"][e["pid"]].alive() for e in owned["alive"]):
            sleep(0.5)
            waited += 0.5
        still = [e for e in owned["alive"] if owned["handles"].get(e["pid"]) and owned["handles"][e["pid"]].alive() is not False]
        for item in owned["alive"]:
            if item not in still:
                note("ORPHAN_STOPPED", {"pid": item["pid"], "created": item["created"]})
        if still:
            owned["alive"] = still
            forced = terminate_owned(owned, confirm=confirm, sleep=sleep)
            out["actions"].append({"forced": forced})
            out["blocked"].extend(forced["blocked"])
            for pid in forced["terminated"]:
                note("ORPHAN_TERMINATED", {"pid": pid})
    finally:
        close_owned(owned)
    final = look()
    try:
        out["owned_alive_after"] = len(final["alive"])
        out["summary"] = summary(final)
        if final["alive"] or final["unknown"] or final["suspects"]:
            out["blocked"].append("restam processos do run vivos ou indeterminados apos a recuperacao")
            note("RECOVERY_BLOCKED", {"alive": len(final["alive"]), "unknown": len(final["unknown"]), "suspects": len(final["suspects"])})
        else:
            note("RECOVERY_DONE", {"owned_alive": 0})
    finally:
        close_owned(final)
    return out
