"""
SIM-1.5G.3 -- identidade de processo (PID + data de criacao) e VIGILIA DURAVEL.

O L2 (tentativa 1) mostrou que "o processo nao existe agora" nao prova QUANDO ele terminou. Aqui a propria tarefa (a sonda,
que sobrevive a sessao do assistente) observa os alvos declarados em `watch.json` a cada batimento e grava em `watch.jsonl`
(append + fsync) o INTERVALO em que cada alvo deixou de existir: ultima observacao viva e primeira observacao encerrada. Nao se
alega precisao maior que o periodo de observacao.

Regras (fail-closed):
* identidade = (pid, data de criacao em FILETIME) lida por `GetProcessTimes`; PID reutilizado (outra criacao) = "pid_reused";
* `UNKNOWN` (acesso negado, plataforma sem a API, erro) NUNCA conta como termino;
* a descoberta do CLI iniciador exige EXATAMENTE um `claude.exe` com `--output-format stream-json` entre os ancestrais; o app
  compartilhado (sem essas opcoes) e excluido e registrado; ambiguidade => `WatchError` (nenhuma tarefa e registrada).

Nos testes o acesso ao SO e injetado (`query`, `snapshot`); nenhum processo e encerrado por este modulo.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.runner.errors import RunnerError

WATCH_JSON, WATCH_LOG = "watch.json", "watch.jsonl"
SCHEMA = 2
ALIVE, ABSENT, UNKNOWN = "ALIVE", "ABSENT", "UNKNOWN"
ENDED_HOW = ("absent", "pid_reused")
OBSERVED_EVERY_S = 60
MAX_DEPTH = 16
ERROR_ACCESS_DENIED, ERROR_INVALID_PARAMETER = 5, 87
CLI_MARKERS = ("--output-format", "stream-json")
RESUME_RE = re.compile(r"--resume[= ]([0-9A-Fa-f-]{36})")
ROLES = ("launcher", "initiator", "helper")


class WatchError(RunnerError):
    pass


# ---------------------------------------------------------------------------------------------------------------------
# acesso ao SO (injetavel)
# ---------------------------------------------------------------------------------------------------------------------
def filetime_iso(filetime: int) -> str:
    return (datetime(1601, 1, 1) + timedelta(microseconds=filetime // 10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def query_native(pid) -> dict:
    """-> {state: ALIVE|ABSENT|UNKNOWN, created: FILETIME|None, error?}. ABSENT so com prova (PID inexistente ou tempo de saida
    gravado); acesso negado ou qualquer outra falha => UNKNOWN."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return {"state": UNKNOWN, "created": None, "error": "pid invalido"}
    if sys.platform != "win32":
        return {"state": UNKNOWN, "created": None, "error": "plataforma sem GetProcessTimes"}
    import ctypes
    import ctypes.wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = w.HANDLE
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.GetProcessTimes.argtypes = [w.HANDLE, ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME),
                                  ctypes.POINTER(w.FILETIME)]
    k.CloseHandle.argtypes = [w.HANDLE]
    handle = k.OpenProcess(0x1000, False, pid)                                  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        error = ctypes.get_last_error()
        if error == ERROR_INVALID_PARAMETER:
            return {"state": ABSENT, "created": None, "error": "OpenProcess: PID inexistente"}
        return {"state": UNKNOWN, "created": None, "error": f"OpenProcess falhou ({error})"}
    try:
        created, exited, kernel, user = w.FILETIME(), w.FILETIME(), w.FILETIME(), w.FILETIME()
        if not k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return {"state": UNKNOWN, "created": None, "error": f"GetProcessTimes falhou ({ctypes.get_last_error()})"}
        made = (created.dwHighDateTime << 32) | created.dwLowDateTime
        done = (exited.dwHighDateTime << 32) | exited.dwLowDateTime
        if done:                                                                  # tempo de saida gravado: o processo ja terminou
            return {"state": ABSENT, "created": made, "error": "processo encerrado (handle ainda aberto)"}
        return {"state": ALIVE, "created": made}
    finally:
        k.CloseHandle(handle)


def snapshot_native() -> dict:
    """{pid: {ppid, name}} da tabela de processos (Toolhelp32). Falha => WatchError (quem chama decide fechar)."""
    if sys.platform != "win32":
        raise WatchError("snapshot de processos indisponivel nesta plataforma")
    import ctypes
    import ctypes.wintypes as w

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", w.LONG),
                    ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = w.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    k.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    k.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    k.CloseHandle.argtypes = [w.HANDLE]
    handle = k.CreateToolhelp32Snapshot(0x2, 0)                                    # TH32CS_SNAPPROCESS
    if not handle or handle == w.HANDLE(-1).value:
        raise WatchError(f"CreateToolhelp32Snapshot falhou ({ctypes.get_last_error()})")
    table = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        ok = k.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            table[int(entry.th32ProcessID)] = {"ppid": int(entry.th32ParentProcessID), "name": str(entry.szExeFile)}
            ok = k.Process32NextW(handle, ctypes.byref(entry))
    finally:
        k.CloseHandle(handle)
    return table


# ---------------------------------------------------------------------------------------------------------------------
# identidade, ancestrais e avaliacao de alvos (puros dado `query`/`snapshot`)
# ---------------------------------------------------------------------------------------------------------------------
def identity_of(pid, query=query_native) -> dict | None:
    """{pid, created, iso} so se o processo esta ALIVE com data de criacao conhecida; senao None."""
    found = query(pid)
    if found.get("state") != ALIVE or not isinstance(found.get("created"), int):
        return None
    return {"pid": pid, "created": found["created"], "iso": filetime_iso(found["created"])}


def ancestors(pid, *, query=query_native, snapshot=snapshot_native, max_depth: int = MAX_DEPTH) -> list:
    """[proprio, pai, avo, ...] com {pid, ppid, name, created, iso}. Interrompe (sem inventar) quando: pai ausente da tabela, ciclo,
    pai nao identificavel ou pai criado DEPOIS do filho (PID reutilizado). Cada elo tem identidade lida pela API."""
    table = snapshot()
    chain, seen, current, child_created = [], set(), pid, None
    while current and current not in seen and len(chain) < max_depth:
        seen.add(current)
        ident = identity_of(current, query)
        entry = table.get(current)
        if ident is None or entry is None:
            break
        if child_created is not None and ident["created"] > child_created:
            break                                                             # um pai nunca e mais novo que o filho
        chain.append({"pid": current, "ppid": entry["ppid"], "name": entry["name"], "created": ident["created"], "iso": ident["iso"]})
        child_created, current = ident["created"], entry["ppid"]
    return chain


def evaluate(target: dict, query=query_native) -> tuple:
    """-> (ALIVE, None, detail) | ("ENDED", "absent"|"pid_reused", detail) | (UNKNOWN, None, detail). UNKNOWN nunca e termino."""
    found = query(target["pid"])
    state = found.get("state")
    if state == ABSENT:
        return "ENDED", "absent", found.get("error") or "ausente"
    if state != ALIVE or not isinstance(found.get("created"), int):
        return UNKNOWN, None, found.get("error") or "estado nao determinado"
    if found["created"] != target["created"]:
        return "ENDED", "pid_reused", f"criacao {found['created']} != {target['created']}"
    return ALIVE, None, ""


def make_target(role: str, pid: int, query=query_native, **extra) -> dict:
    if role not in ROLES:
        raise WatchError(f"papel de alvo desconhecido: {role!r}")
    ident = identity_of(pid, query)
    if ident is None:
        raise WatchError(f"nao foi possivel ler a identidade (PID + criacao) do alvo {role} PID {pid}: o registro da tarefa e bloqueado")
    return {"role": role, "pid": pid, "created": ident["created"], "iso": ident["iso"], **extra}


def discover_initiator(chain: list, cmdline_of, query=query_native) -> tuple:
    """-> (alvo, excluidos). `chain` = ancestrais do lancador (`ancestors`); `cmdline_of(pid)` devolve a linha de comando.
    EXATAMENTE um `claude.exe` com `--output-format` e `stream-json` e o CLI da sessao iniciadora; qualquer `claude.exe` sem elas e o
    app compartilhado (excluido). Zero ou mais de um CLI, ou linha de comando ilegivel => `WatchError`."""
    candidates = [c for c in chain[1:] if str(c.get("name", "")).lower() == "claude.exe"]
    clis, shared = [], []
    for candidate in candidates:
        cmd = cmdline_of(candidate["pid"])
        if not isinstance(cmd, str) or not cmd:
            raise WatchError(f"linha de comando ilegivel para claude.exe PID {candidate['pid']}: descoberta ambigua")
        (clis if all(marker in cmd for marker in CLI_MARKERS) else shared).append((candidate, cmd))
    if len(clis) != 1:
        raise WatchError(f"descoberta da sessao iniciadora ambigua: {len(clis)} CLI(s) claude.exe entre os ancestrais (esperado 1)")
    candidate, cmd = clis[0]
    match = RESUME_RE.search(cmd)
    target = make_target("initiator", candidate["pid"], query, exe=candidate["name"], cmdline_sha256=hashlib.sha256(cmd.encode("utf-8")).hexdigest(),
                         resume_id=match.group(1) if match else None)
    excluded = [{"role": "shared_app", "pid": c["pid"], "created": c["created"], "reason": "claude.exe sem --output-format stream-json (compartilhado)"}
                for c, _ in shared]
    return target, excluded


def build_watch(targets: list, excluded: list, *, armed_t: int) -> tuple:
    """-> (bytes canonicos de watch.json, sha256). Exige 'launcher' e papeis validos, sem alvos repetidos."""
    seen, roles = set(), [t.get("role") for t in targets]
    for t in targets:
        key = (t.get("role"), t.get("pid"))
        if t.get("role") not in ROLES or key in seen or not isinstance(t.get("pid"), int) or not isinstance(t.get("created"), int):
            raise WatchError(f"alvo de vigilia invalido ou repetido: {t!r}")
        seen.add(key)
    if "launcher" not in roles:
        raise WatchError("a vigilia exige o alvo 'launcher'")
    doc = {"schema": SCHEMA, "armed_t": int(armed_t), "targets": list(targets), "excluded": list(excluded)}
    data = canonical_bytes(doc)
    return data, hashlib.sha256(data).hexdigest()


def read_watch(proc) -> dict | None:
    try:
        return json.loads((Path(proc) / WATCH_JSON).read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None


def watch_sha256(proc) -> str | None:
    try:
        return hashlib.sha256((Path(proc) / WATCH_JSON).read_bytes()).hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------------------------------
# vigilia dentro da sonda
# ---------------------------------------------------------------------------------------------------------------------
def append_durable(path, doc: dict) -> None:
    line = json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


class Watcher:
    """Observa os alvos de `watch.json` e grava, durável, o intervalo de termino. Um `step()` por batimento (5 s)."""

    def __init__(self, proc, doc: dict, *, query=query_native, clock=time.time, period_s: int = 5, observed_every_s: int = OBSERVED_EVERY_S,
                 write=append_durable) -> None:
        self.proc, self.query, self.clock, self.period_s = Path(proc), query, clock, period_s
        self.observed_every_s, self.write = observed_every_s, write
        self.targets = [dict(t) for t in doc["targets"]]
        self.state = {self._key(t): {"ended": False, "last_alive": None, "unknown": 0, "in_unknown": False} for t in self.targets}
        self.started, self.last_observed, self.write_errors = False, None, 0

    @staticmethod
    def _key(target: dict) -> str:
        return f"{target['role']}:{target['pid']}"

    @classmethod
    def open(cls, proc, **kwargs):
        doc = read_watch(proc)
        return cls(proc, doc, **kwargs) if doc and doc.get("schema") == SCHEMA and doc.get("targets") else None

    def _record(self, doc: dict) -> None:
        try:
            self.write(self.proc / WATCH_LOG, doc)
        except OSError:
            self.write_errors += 1

    def step(self) -> None:
        now = int(self.clock())
        states = {}
        for target in self.targets:
            key, st = self._key(target), self.state[self._key(target)]
            if st["ended"]:
                states[key] = "ENDED"
                continue
            verdict, how, detail = evaluate(target, self.query)
            if verdict == ALIVE:
                st["last_alive"], st["in_unknown"] = now, False
            elif verdict == UNKNOWN:
                st["unknown"] += 1
                if not st["in_unknown"]:
                    st["in_unknown"] = True
                    self._record({"event": "UNKNOWN", "t": now, "role": target["role"], "pid": target["pid"], "detail": str(detail)[:120]})
            else:
                st["ended"] = True
                self._record({"event": "ENDED", "t": now, "role": target["role"], "pid": target["pid"], "created": target["created"],
                              "last_seen_alive_t": st["last_alive"], "first_seen_ended_t": now, "how": how,
                              "unknown_observations": st["unknown"], "period_s": self.period_s, "detail": str(detail)[:120]})
            states[key] = "ENDED" if st["ended"] else verdict
        if not self.started:
            self.started = True
            self.last_observed = now
            self._record({"event": "OBSERVING", "t": now, "period_s": self.period_s, "states": states})
        elif now - self.last_observed >= self.observed_every_s:
            self.last_observed = now
            self._record({"event": "OBSERVED", "t": now, "period_s": self.period_s, "states": states})


def summarize(proc) -> dict:
    """Resumo de `watch.jsonl` contra `watch.json`: por alvo, o intervalo de termino. `integrity_ok` exige arquivo legivel, linhas
    validas e registros ENDED que batem (pid + criacao) com o alvo declarado."""
    proc = Path(proc)
    doc = read_watch(proc)
    out = {"armed": bool(doc and doc.get("schema") == SCHEMA), "integrity_ok": False, "records": 0, "corrupt_lines": 0, "observing": False,
           "period_s": None, "targets": {}, "ended": {}}
    if not out["armed"]:
        return out
    declared = {(t["role"], t["pid"]): t for t in doc.get("targets", [])}
    try:
        lines = (proc / WATCH_LOG).read_bytes().decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return out
    mismatch = False
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            out["corrupt_lines"] += 1
            continue
        out["records"] += 1
        event = rec.get("event")
        if event == "OBSERVING":
            out["observing"], out["period_s"] = True, rec.get("period_s")
        elif event == "ENDED":
            target = declared.get((rec.get("role"), rec.get("pid")))
            if target is None or rec.get("created") != target["created"] or rec.get("how") not in ENDED_HOW:
                mismatch = True
                continue
            out["ended"].setdefault(rec["role"], {k: rec.get(k) for k in
                                                  ("pid", "created", "last_seen_alive_t", "first_seen_ended_t", "how", "unknown_observations", "period_s")})
        elif event in ("OBSERVED", "UNKNOWN"):
            pass
    out["targets"] = {f"{r}:{p}": {"role": r, "pid": p, "created": t["created"]} for (r, p), t in declared.items()}
    out["integrity_ok"] = bool(out["observing"] and out["corrupt_lines"] == 0 and not mismatch)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# identidades da propria sonda e do solicitante do STOP
# ---------------------------------------------------------------------------------------------------------------------
def self_lineage(query=query_native, snapshot=snapshot_native, pid: int | None = None, depth: int = 3) -> dict:
    """{self, parent, grandparent} (PID + criacao) do processo; ausente => None naquele nivel. Falha ao ler a tabela => tudo None."""
    pid = os.getpid() if pid is None else pid
    try:
        chain = ancestors(pid, query=query, snapshot=snapshot, max_depth=depth)
    except WatchError:
        chain = []
    names = ("self", "parent", "grandparent")
    out = {name: None for name in names}
    for name, link in zip(names, chain[:depth]):
        out[name] = {"pid": link["pid"], "created": link["created"], "name": link["name"]}
    return out


def lineage_unchanged(recorded: dict, query=query_native) -> bool | None:
    """True so se TODOS os niveis gravados seguem ALIVE com a mesma criacao; False se algum terminou/mudou; None se indeterminado."""
    entries = [e for e in (recorded or {}).values() if e]
    if not entries or not (recorded or {}).get("self"):
        return None
    unknown = False
    for entry in entries:
        verdict, _, _ = evaluate(entry, query)
        if verdict == "ENDED":
            return False
        unknown = unknown or verdict == UNKNOWN
    return None if unknown else True


def outside(chain: list, target: dict) -> bool:
    """True se NENHUM elo da cadeia e o alvo (pid + criacao)."""
    return not any(c.get("pid") == target.get("pid") and c.get("created") == target.get("created") for c in chain)
