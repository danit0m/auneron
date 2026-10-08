"""
Registro do host (`runs/registry.jsonl`): aloca o `run_id`, impede runs concorrentes e guarda os terminais.

Encadeado por hash como o ledger. A tentativa `A<nn>` e MONOTONICA: 1 + numero de `OPENED` do mesmo RIM completo,
consumida no OPEN (um abort antes do D0 tambem gasta o numero) e NUNCA reutilizada, inclusive para runs
ABORTED/INVALID. O diretorio `runs/<run_id>/` e criado de forma exclusiva (`mkdir` falha se existir).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.runner import rim as rim_mod
from sim.runner.errors import RegistryError
from sim.runner.lifecycle import ABORTED
from sim.runner.lifecycle import COMPLETED
from sim.runner.lifecycle import INVALID

GENESIS = "0" * 64
PURPOSES = ("FIRST", "RETRY", "REPRODUCTION")
TERMINALS = (COMPLETED, ABORTED, INVALID)


def _line_hash(record: dict) -> str:
    return sha256_bytes(canonical_bytes({k: v for k, v in record.items() if k != "hash"}))


class Registry:
    def __init__(self, root, *, clock=time.time) -> None:
        self.root = Path(root)
        self.path = self.root / "registry.jsonl"
        self.lock_path = self.root / "registry.lock"
        self.clock = clock
        self.root.mkdir(parents=True, exist_ok=True)

    # -- leitura --------------------------------------------------------------------------------------------
    def events(self) -> list:
        records, tail = [], GENESIS
        if not self.path.exists():
            return records
        for number, raw in enumerate(self.path.read_bytes().split(b"\n"), start=1):
            if not raw.strip():
                continue
            record = json.loads(raw.decode("utf-8"))
            if record.get("seq") != len(records) + 1 or record.get("prev") != tail or record.get("hash") != _line_hash(record):
                raise RegistryError(f"registro corrompido na linha {number}")
            tail = record["hash"]
            records.append(record)
        return records

    def runs(self) -> dict:
        """run_id -> {opened, terminal, annotations}."""
        out: dict = {}
        for record in self.events():
            payload = record["payload"]
            if record["event"] == "OPENED":
                out[payload["run_id"]] = {"opened": payload, "terminal": None, "annotations": []}
            elif record["event"] == "TERMINAL":
                run = out.get(payload["run_id"])
                if run is None or run["terminal"] is not None:
                    raise RegistryError(f"TERMINAL invalido para {payload['run_id']}")
                run["terminal"] = payload
            elif record["event"] == "ANNOTATION":
                out[payload["run_id"]]["annotations"].append(payload)
        return out

    def state_of(self, run_id: str) -> str:
        run = self.runs().get(run_id)
        if run is None:
            raise RegistryError(f"run desconhecido: {run_id}")
        return run["terminal"]["state"] if run["terminal"] else "NON_TERMINAL"

    # -- escrita ----------------------------------------------------------------------------------------------
    def _append(self, event: str, payload: dict) -> dict:
        events = self.events()
        tail = events[-1]["hash"] if events else GENESIS
        record = {"seq": len(events) + 1, "prev": tail,
                  "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock())), "event": event, "payload": payload}
        record["hash"] = _line_hash(record)
        with self.path.open("ab") as handle:
            handle.write(canonical_bytes(record))
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def _lock(self):
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise RegistryError("registro em uso por outro alocador (registry.lock)") from error
        os.close(fd)

    def _unlock(self) -> None:
        try:
            os.remove(self.lock_path)
        except FileNotFoundError:
            pass

    def open_run(self, rim: dict, *, purpose: str, parent_run_id: str | None = None, reason: str | None = None) -> dict:
        """Aloca o `run_id` e cria o diretorio do run. Devolve {run_id, run_dir, attempt, rim_sha256, ...}."""
        rim_mod.validate_rim(rim)
        if purpose not in PURPOSES:
            raise RegistryError(f"purpose invalido: {purpose!r}")
        sha = rim_mod.rim_sha256(rim)
        prefix = sha[:12]
        self._lock()
        try:
            runs = self.runs()
            for run_id, run in runs.items():
                other = run["opened"]
                if other["rim12"] == prefix and other["rim_sha256"] != sha:
                    raise RegistryError(f"RIM_PREFIX_COLLISION: {prefix} (STOP, decisao do PO)")
                if run["terminal"] is None:
                    raise RegistryError(f"run nao terminal em aberto ({run_id}): um run por vez")
            previous = [rid for rid, run in runs.items() if run["opened"]["rim_sha256"] == sha]
            attempt = len(previous) + 1
            if attempt > 99:
                raise RegistryError("tentativa > 99 para o mesmo RIM (STOP)")
            self._check_purpose(runs, purpose, attempt, parent_run_id, reason, sha)
            run_id = rim_mod.make_run_id(sha, attempt)
            run_dir = self.root / run_id
            try:
                run_dir.mkdir(parents=False, exist_ok=False)
            except FileExistsError as error:
                raise RegistryError(f"diretorio do run ja existe ({run_id}): numero de tentativa nunca e reutilizado") from error
            payload = {"run_id": run_id, "rim_sha256": sha, "rim12": prefix, "attempt": attempt, "kind": rim["kind"],
                       "purpose": purpose, "parent_run_id": parent_run_id, "reason": reason}
            self._append("OPENED", payload)
        finally:
            self._unlock()
        return {**payload, "run_dir": run_dir}

    @staticmethod
    def _check_purpose(runs: dict, purpose: str, attempt: int, parent: str | None, reason: str | None, sha: str) -> None:
        if attempt == 1:
            if purpose != "FIRST" or parent is not None:
                raise RegistryError("primeira tentativa do RIM deve ser purpose=FIRST sem parent")
            return
        if purpose == "FIRST":
            raise RegistryError("o RIM ja tem tentativas: declare RETRY ou REPRODUCTION")
        if parent is None or parent not in runs or runs[parent]["opened"]["rim_sha256"] != sha:
            raise RegistryError("parent_run_id ausente ou de outro RIM")
        parent_run = runs[parent]
        if parent_run["terminal"] is None:
            raise RegistryError("parent ainda nao terminou")
        state = parent_run["terminal"]["state"]
        if purpose == "RETRY":
            if state not in (ABORTED, INVALID) or not reason:
                raise RegistryError("RETRY exige parent ABORTED/INVALID e a razao")
        elif state != COMPLETED:
            raise RegistryError("REPRODUCTION exige um run COMPLETED como referencia")

    def record_terminal(self, run_id: str, state: str, failure_class: str | None, ledger_tail: dict,
                        package_digest: str | None = None) -> dict:
        if state not in TERMINALS:
            raise RegistryError(f"estado terminal invalido: {state}")
        run = self.runs().get(run_id)
        if run is None or run["terminal"] is not None:
            raise RegistryError(f"terminal impossivel para {run_id}")
        return self._append("TERMINAL", {"run_id": run_id, "state": state, "class": failure_class,
                                         "ledger_tail": ledger_tail, "package_digest": package_digest})

    def record_annotation(self, run_id: str, kind: str, payload: dict | None = None) -> dict:
        if run_id not in self.runs():
            raise RegistryError(f"run desconhecido: {run_id}")
        return self._append("ANNOTATION", {"run_id": run_id, "type": kind, **(payload or {})})
