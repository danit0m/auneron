"""
Ligacao do runner ao lab (SIM-1.5E C.1). Arquitetura em duas camadas para que a LOGICA seja testavel sem Docker:

* `LiveServices` (logica pura sobre PORTAS): monta os fatos do dia, os arquivos de evidencia incrementais, o tripwire
  de autoridade e o fechamento. Recebe as portas por injecao.
* `lab_ports(lab, ...)` (adaptadores finos de `docker exec`/SQL somente leitura): reutilizam `sim.live.env` e
  `sim.live.flow`. NESTE CHECKPOINT OS ADAPTADORES SO SAO EXERCITADOS COM DUPLOS; a 1a execucao real e o SR-1.

Nada aqui le o Oracle: os insumos dependentes do Oracle (plano, status esperados) chegam prontos de
`sim.runner.harness_side`.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Callable

from sim.generator.canonical import canonical_bytes
from sim.runner import authority
from sim.runner.runner import RunServices

GENESIS = "0" * 64
LOG_SERVICES = ("backend", "driver", "collector", "clock", "postgres")


def line_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


class IncrementalChain:
    """Consome linhas NOVAS de um JSONL encadeado (Driver ou Collector), verificando a continuidade do hash."""

    def __init__(self) -> None:
        self.count, self.tail = 0, GENESIS
        self.ok = True

    def consume(self, lines: list) -> list:
        """Linhas novas verificadas. Cadeia quebrada => `ok = False` (permanente) e NADA e aceito daquele ponto em diante."""
        records = []
        for raw in lines:
            record = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            if (not self.ok or record.get("prev") != self.tail or record.get("n") != self.count + 1
                    or record.get("hash") != line_hash(record)):
                self.ok = False
                break
            self.tail, self.count = record["hash"], self.count + 1
            records.append(record)
        return records


@dataclass
class Ports:
    """Tudo o que toca o lab. Cada porta e uma funcao simples; os testes passam duplos."""
    new_driver_lines: Callable[[int], list]              # (ja_lidas) -> novas linhas do driver_evidence.jsonl
    new_collector_lines: Callable[[int], list]           # (ja_lidas) -> novas linhas do collected.jsonl
    driver_status: Callable[[], dict]                    # {"counts":..., "in_flight": [...]}
    db_counts: Callable[[], dict]                        # {"accounts": n, "approvals": n, "work_items": n}
    containers: Callable[[], dict]                       # nome -> {"started_at","networks","running"}
    run_id_files: Callable[[], list]                     # run_id lido de /state/RUN_ID de cada container
    last_barrier: Callable[[], dict | None]              # evidencia da barreira diaria mais recente
    skew_ms: Callable[[], int]
    o1_ms: Callable[[], int]
    free_bytes: Callable[[], int]
    virtual_now: Callable[[], str]
    real_now: Callable[[], str]
    log_slice: Callable[[str], bytes]                    # servico -> logs desde o ultimo corte
    probe: Callable[[int, str], dict]
    preserve: Callable[[str, Path], dict]
    finalize_extra: Callable[[Path], dict]               # copia /state, proveniencia, DR-1 final... -> {"violations": [...]}
    growth: Callable[[], dict]                           # banco, VHDX, estado, evidencia, logs, maiores relacoes


@dataclass
class LiveServices:
    run_dir: Path
    run_id: str
    plan: dict
    agenda_ops: list
    timeline: dict
    ports: Ports
    baseline: dict                                        # contagens do produto antes do D0 (ex.: item da sonda S-9)
    expected_containers: dict                             # nome -> started_at esperado (backend muda depois do restart)
    expected_networks: dict
    barrier_hard_ms: int
    kind: str = "REHEARSAL"
    tripwire: authority.AuthorityTripwire = field(default_factory=authority.AuthorityTripwire)
    driver_chain: IncrementalChain = field(default_factory=IncrementalChain)
    collector_chain: IncrementalChain = field(default_factory=IncrementalChain)
    collected_ids: set = field(default_factory=set)
    status_lines_total: int = 0
    chain_lines_total: int = 0
    creates_ok: int = 0
    request_refs_ok: set = field(default_factory=set)
    work_item_refs_ok: set = field(default_factory=set)

    # -- helpers de arquivo ------------------------------------------------------------------------------------------------
    def _append_raw(self, rel: str, lines: list) -> None:
        """Copia FIEL das linhas do container (o hash encadeado vale para os bytes originais; podem ter floats)."""
        if not lines:
            return
        path = self.run_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            for raw in lines:
                data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
                handle.write(data.rstrip(b"\n") + b"\n")

    def _append(self, rel: str, rows: list) -> None:
        if not rows:
            return
        path = self.run_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            for row in rows:
                handle.write(canonical_bytes(row))

    # -- fatos do dia ---------------------------------------------------------------------------------------------------------
    def day_facts(self, day: int) -> dict:
        ports = self.ports
        driver_lines = ports.new_driver_lines(self.driver_chain.count)
        collector_lines = ports.new_collector_lines(self.collector_chain.count)
        driver_new = self.driver_chain.consume(driver_lines)
        collector_new = self.collector_chain.consume(collector_lines)
        self._append_raw("driver/driver_evidence.jsonl", driver_lines[:len(driver_new)])
        self._append_raw("collector/collected.jsonl", collector_lines[:len(collector_new)])
        status_rows = authority.derive_status_ledger(driver_new)
        chain_rows = authority.derive_authority_chain(driver_new)
        self._append("authority/status_ledger.jsonl", status_rows)
        self._append("authority/authority_chain.jsonl", chain_rows)
        self._count_successes(chain_rows)
        self.status_lines_total += len(status_rows)
        self.chain_lines_total += len(chain_rows)
        tripwire = self.tripwire.feed(chain_rows)
        self._append("authority/tripwire.jsonl", tripwire)

        status = ports.driver_status()
        counts = status.get("counts", {})
        ops = [op for op in self.agenda_ops if op["day"] <= day]
        expected_items = self._expected_items(day)
        got = [r for r in collector_new if self._belongs_to_day(r, day)]
        self.collected_ids |= {r["item_id"] for r in got}
        from sim.collector.collect import verify_records                           # leitura pura do plano (sem Oracle)

        problems = verify_records(self.plan, collector_new)
        barrier = ports.last_barrier()
        infra = self._infra(day)
        logs = self._logs(day)
        return {
            "time": {"real_open": ports.real_now(), "real_close": ports.real_now(), "virtual_open": ports.virtual_now(),
                     "virtual_close": ports.virtual_now(), "clock_skew_ms": ports.skew_ms(),
                     "barrier_total_ms": None if barrier is None else int(round(float(barrier.get("total_s", 0)) * 1000)),
                     "barrier_events_ok": bool(barrier and barrier.get("time_floor_satisfied") and barrier.get("stable_after_attempts")),
                     "barrier_hard_ms": self.barrier_hard_ms, "o1_ms": ports.o1_ms()},
            "driver": {"counts": counts, "in_flight": len(status.get("in_flight", [])), "agenda_ops_through_day": len(ops)},
            "collector": {"expected_items": len(expected_items), "collected_items": len(got),
                          "chain_ok": self.collector_chain.ok and self.driver_chain.ok,
                          "missing": len(expected_items - {r["item_id"] for r in got}), "problems": len(problems)},
            "balance": self._balance(day),
            "infra": infra,
            "disk": {"free_bytes": ports.free_bytes()},
            "growth": {**ports.growth(), "log_bytes": {s["service"]: s["bytes"] for s in logs}},
            "authority": {"status_lines": len(status_rows), "chain_lines": len(chain_rows), "tripwire": tripwire},
            "logs": logs,
        }

    def _belongs_to_day(self, record: dict, day: int) -> bool:
        trigger = record["trigger"]
        if trigger["kind"] == "end_of_run":
            return day == self.timeline["last_day"]
        return trigger["day"] == day

    def _expected_items(self, day: int) -> set:
        out = set()
        for item in self.plan["items"]:
            if item.get("channel", "http") != "http":
                continue
            if item["safe_at"] == "end_of_run":
                if day == self.timeline["last_day"]:
                    out.add(item["item_id"])
            elif item["day"] == day:
                out.add(item["item_id"])
        return out

    def _count_successes(self, op_results: list) -> None:
        """Sucessos factuais do Driver (2xx), sem Oracle: efeitos que o produto DEVE ter criado."""
        for row in op_results:
            status = row.get("http_status")
            if not (isinstance(status, int) and 200 <= status < 300 and row.get("outcome") in ("done", "reconciled")):
                continue
            if row.get("op") == "create_receivable":
                self.creates_ok += 1
            elif row.get("op") in ("request_mark_paid", "materialize_mark_overdue") and row.get("ref"):
                self.request_refs_ok.add(row["ref"])
                if row["op"] == "materialize_mark_overdue":
                    self.work_item_refs_ok.add(row["ref"])      # materializar mark_overdue tambem cria um work item
            elif row.get("op") == "materialize_escalation" and row.get("ref"):
                self.work_item_refs_ok.add(row["ref"])

    def _balance(self, day: int) -> dict:
        """Produto (SQL somente leitura) x sucessos do Driver. Rejeicoes esperadas (so o Oracle as conhece) nao entram
        nos sucessos, entao o balanco continua exato sem conhecimento secreto."""
        db = self.ports.db_counts()
        base = self.baseline
        return {"accounts": [db["accounts"] - base.get("accounts", 0), self.creates_ok],
                "approvals": [db["approvals"] - base.get("approvals", 0), len(self.request_refs_ok)],
                "work_items": [db["work_items"] - base.get("work_items", 0), len(self.work_item_refs_ok)]}

    def _infra(self, day: int) -> dict:
        containers = self.ports.containers()
        restarts = []
        topology_ok = True
        for name, expected_started in self.expected_containers.items():
            current = containers.get(name)
            if current is None or not current["running"]:
                restarts.append(f"{name}: nao esta rodando")
                continue
            backend_after_restart = name == "backend" and day >= self.timeline["restart_day"]
            if current["started_at"] != expected_started and not backend_after_restart:
                restarts.append(f"{name}: StartedAt mudou sem restart previsto")
            if set(current["networks"]) != set(self.expected_networks[name]):
                topology_ok = False
        run_ids = self.ports.run_id_files()
        return {"containers_ok": all(c["running"] for c in containers.values()) and not restarts,
                "unexpected_restarts": restarts, "topology_ok": topology_ok,
                "run_id_ok": bool(run_ids) and all(r == self.run_id for r in run_ids)}

    def _logs(self, day: int) -> list:
        out = []
        for service in LOG_SERVICES:
            data = self.ports.log_slice(service)
            rel = f"logs/{service}/D{day:03d}.log.gz"
            path = self.run_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = gzip.compress(data or b"", mtime=0)
            path.write_bytes(payload)
            out.append({"service": service, "path": rel, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)})
        return out

    # -- demais servicos -----------------------------------------------------------------------------------------------------------
    def finalize(self) -> dict:
        from sim.runner import harness_side

        extra = self.ports.finalize_extra(self.run_dir)
        violations = list(extra.get("violations", []))
        contaminated = harness_side.contamination_problems(self.run_dir, self.kind)
        if contaminated:
            violations.append({"class": "FIXTURE_CONTAMINATION", "detail": "; ".join(contaminated[:3])})
        return {"violations": violations, **{k: v for k, v in extra.items() if k != "violations"}}

    def preserve(self, failure_class: str) -> dict:
        return self.ports.preserve(failure_class, self.run_dir)

    def probe(self, day: int, when: str) -> dict:
        return self.ports.probe(day, when)

    def services(self, hooks, awake=None, orchestrator_factory=None) -> RunServices:
        return RunServices(hooks=hooks, day_facts=self.day_facts, finalize=self.finalize, preserve=self.preserve,
                           probe=self.probe, orchestrator_factory=orchestrator_factory, awake=awake)


__all__ = ["LiveServices", "Ports", "IncrementalChain", "LOG_SERVICES"]
