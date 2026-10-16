"""
Ledger do run (D-1.5E-3): JSONL append-only, hash encadeado, um unico escritor (o runner no host).

Cada linha: {seq, prev, t_real, t_virtual, event, payload, hash}; `hash = sha256(canonical(linha sem hash))`.
Detecta: ALTERACAO (hash), REORDENACAO/REMOCAO NO MEIO (encadeamento, `seq`) e TRUNCAMENTO DO FIM (arquivo-ancora
`ledger.anchor` gravado de forma atomica apos cada append, e a cauda replicada no registro do host quando o run
termina). A legalidade de cada evento vem do `lifecycle.Replay`: o ledger e a UNICA via para mudar o estado do run.
Eventos reservados (`OPENED`, `D0_AUTHORIZED`) so existem pelos metodos proprios.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.runner import lifecycle
from sim.runner.errors import LedgerError
from sim.runner.errors import LifecycleError
from sim.runner.taxonomy import terminal_state

GENESIS = "0" * 64
LEDGER_NAME = "ledger.jsonl"
ANCHOR_NAME = "ledger.anchor"


def line_hash(record: dict) -> str:
    return sha256_bytes(canonical_bytes({k: v for k, v in record.items() if k != "hash"}))


def _utc_iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def read_events(path: Path) -> list:
    """Le e VERIFICA a cadeia (hash, prev, seq). Nao olha a ancora nem o ciclo de vida."""
    records, tail = [], GENESIS
    if not Path(path).exists():
        return records
    for number, raw in enumerate(Path(path).read_bytes().split(b"\n"), start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw.decode("utf-8"))
        except ValueError as error:
            raise LedgerError(f"linha {number}: JSON invalido") from error
        if record.get("seq") != len(records) + 1:
            raise LedgerError(f"linha {number}: seq {record.get('seq')} != {len(records) + 1} (reordenacao ou remocao)")
        if record.get("prev") != tail:
            raise LedgerError(f"linha {number}: encadeamento quebrado (prev)")
        if record.get("hash") != line_hash(record):
            raise LedgerError(f"linha {number}: hash invalido (alteracao)")
        tail = record["hash"]
        records.append(record)
    return records


class RunLedger:
    def __init__(self, run_dir, *, clock=time.time) -> None:
        self.dir = Path(run_dir)
        self.path = self.dir / LEDGER_NAME
        self.anchor_path = self.dir / ANCHOR_NAME
        self.clock = clock
        self.dir.mkdir(parents=True, exist_ok=True)
        self.records = read_events(self.path)
        self.replay = self._replay()

    # -- estado ---------------------------------------------------------------------------------------------
    def _replay(self) -> lifecycle.Replay:
        return lifecycle.replay([(r["event"], r["payload"]) for r in self.records])

    @property
    def state(self) -> str | None:
        return self.replay.state

    @property
    def tail(self) -> dict:
        if not self.records:
            return {"seq": 0, "hash": GENESIS}
        return {"seq": self.records[-1]["seq"], "hash": self.records[-1]["hash"]}

    def events(self) -> list:
        return list(self.records)

    # -- escrita ----------------------------------------------------------------------------------------------
    def _append(self, event: str, payload: dict, t_virtual: str | None = None) -> dict:
        probe = _clone(self.replay)
        probe.apply(event, payload)                                  # levanta LifecycleError se ilegal; nada gravado
        record = {"seq": len(self.records) + 1, "prev": self.tail["hash"], "t_real": _utc_iso(self.clock()),
                  "t_virtual": t_virtual, "event": event, "payload": payload}
        record["hash"] = line_hash(record)
        data = canonical_bytes(record)
        with self.path.open("ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self.records.append(record)
        self.replay = probe
        self._write_anchor()
        return record

    def _write_anchor(self) -> None:
        tmp = self.anchor_path.with_suffix(".tmp")
        tmp.write_bytes(canonical_bytes(self.tail))
        os.replace(tmp, self.anchor_path)

    def append(self, event: str, payload: dict | None = None, *, t_virtual: str | None = None) -> dict:
        if event in lifecycle.RESERVED_EVENTS:
            raise LedgerError(f"{event} e reservado: use o metodo proprio")
        return self._append(event, dict(payload or {}), t_virtual)

    def open(self, payload: dict) -> dict:
        if self.records:
            raise LedgerError("ledger ja aberto")
        for key in ("run_id", "rim_sha256", "kind", "timeline"):
            if key not in payload:
                raise LedgerError(f"OPENED sem {key}")
        return self._append("OPENED", dict(payload))

    def authorize_d0(self, token_sha256: str, payload: dict | None = None) -> dict:
        """Chamado so por `interlock.enter_running` DEPOIS de validar o token."""
        return self._append("D0_AUTHORIZED", {**(payload or {}), "token_sha256": token_sha256})

    def terminate(self, failure_class: str, detail: dict | None = None) -> dict:
        """ABORTED ou INVALID conforme a taxonomia (classe desconhecida => erro)."""
        state = terminal_state(failure_class)
        return self._append(state, {"class": failure_class, **(detail or {})})

    # -- verificacao -------------------------------------------------------------------------------------------
    def verify(self) -> dict:
        """Cadeia + ancora + ciclo de vida. Levanta LedgerError/LifecycleError; devolve o resumo se integro."""
        records = read_events(self.path)
        lifecycle.replay([(r["event"], r["payload"]) for r in records])
        tail = ({"seq": records[-1]["seq"], "hash": records[-1]["hash"]} if records else {"seq": 0, "hash": GENESIS})
        warning = None
        if self.anchor_path.exists():
            anchor = json.loads(self.anchor_path.read_bytes().decode("utf-8"))
            if anchor["seq"] > tail["seq"]:
                raise LedgerError(f"truncamento: ancora em seq {anchor['seq']}, ledger termina em {tail['seq']}")
            if anchor["seq"] == tail["seq"] and anchor["hash"] != tail["hash"]:
                raise LedgerError("ancora e cauda divergem (ledger reescrito)")
            if anchor["seq"] < tail["seq"]:
                if tail["seq"] - anchor["seq"] > 1:
                    raise LedgerError("ledger adiantado da ancora em mais de um evento")
                warning = "anchor_lag"
        elif records:
            raise LedgerError("ledger sem ancora (arquivo removido)")
        return {"events": len(records), "tail": tail, "warning": warning}


def _clone(replay: lifecycle.Replay) -> lifecycle.Replay:
    return lifecycle.Replay(last_day=replay.last_day, state=replay.state, seen=set(replay.seen),
                            last_closed=replay.last_closed, open_day=replay.open_day,
                            abort_intent=replay.abort_intent)


__all__ = ["RunLedger", "read_events", "line_hash", "GENESIS", "LifecycleError"]
