"""
Laco de 180 dias do `nh-small` (SIM-1.5E C.2). REUSA o `RunOrchestrator` e os `Hooks` ja provados: D14 (floor) e D90
(restart) continuam sendo exatamente os mecanismos versionados; este modulo nao reimplementa nenhum deles. Chama
`orchestrator.run(first_day=d, last_day=d)` UM DIA POR VEZ (API publica; o `end_of_run` dispara sozinho em
`d == last_day`), decora os hooks (parada do operador, guarda de dia, linha do tempo) e fecha cada dia com o
registro diario e o ledger. R0: nenhum resume; um run nao-terminal encontrado na partida e SELADO como `UNCLEAN_EXIT`.

Nada aqui conhece o Oracle: o que o runner recebe sao FATOS (`services.day_facts`) e julgamentos puros.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Callable

from sim.generator.canonical import canonical_bytes
from sim.runner import daily
from sim.runner import interlock
from sim.runner import package
from sim.runner import preflight as preflight_mod
from sim.runner import rim as rim_mod
from sim.runner.errors import DiskGuardError
from sim.runner.errors import LedgerError
from sim.runner.errors import OperatorStop
from sim.runner.errors import PreflightFailed
from sim.runner.errors import ResumeRefused
from sim.runner.errors import RunnerError
from sim.runner.host import LOCK_NAME
from sim.runner.host import RunLock
from sim.runner.host import StopFlag
from sim.runner.host import pid_alive
from sim.runner.ledger import RunLedger
from sim.runner.lifecycle import ABORTED
from sim.runner.lifecycle import COMPLETED
from sim.runner.lifecycle import LIVE
from sim.runner.lifecycle import PREFLIGHTED
from sim.runner.lifecycle import RUNNING
from sim.runner.registry import Registry
from sim.runner.taxonomy import terminal_state
from sim.runner.taxonomy import worst_state
from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.quiescence import HarnessError as BarrierHarnessError
from sim.stack.lab.run_orchestrator import Hooks
from sim.stack.lab.run_orchestrator import RunOrchestrator

HOOK_NAMES = ("clock_set", "driver_slots", "driver_run_slot", "driver_export_refs", "driver_mark_harness_done",
              "collector_load_refs", "collector_trigger", "collector_mark_barrier", "daily_barrier", "restart_backend",
              "restart_barrier", "floor_activation")


class DayGuardError(RunnerError):
    """Um hook tentou agir fora do dia corrente do ledger (pulo ou repeticao de dia)."""


class DayViolation(RunnerError):
    def __init__(self, violations: list) -> None:
        super().__init__("; ".join(f"{v['class']}: {v['detail']}" for v in violations))
        self.violations = violations


@dataclass
class RunServices:
    """Tudo o que o runner precisa do ambiente, injetado (testes usam duplos; o live liga ao lab)."""
    hooks: Hooks
    day_facts: Callable[[int], dict]
    finalize: Callable[[], dict]                      # -> {"violations": [{class, detail}], ...}
    preserve: Callable[[str], dict]
    probe: Callable[[int, str], dict]                 # -> {"clean": bool, ...} (DR-1)
    orchestrator_factory: Callable | None = None
    awake: Callable[[], bool] | None = None
    growth_per_day_bytes: int | None = None           # modelo de crescimento validado (RUN); None no ensaio


def classify_exception(exc: BaseException, hook: str | None) -> str:
    if isinstance(exc, OperatorStop):
        return "OPERATOR_STOP"
    if isinstance(exc, DiskGuardError):
        return "DISK_LOW"
    if hook in ("restart_backend", "restart_barrier"):
        return "RESTART_FAILURE"
    if hook == "floor_activation":
        return "FLOOR_FAILURE"
    if isinstance(exc, GuardViolation) and hook == "clock_set":
        return "CLOCK_VIOLATION"
    if isinstance(exc, BarrierHarnessError) and hook == "daily_barrier":
        return "BARRIER_FAILURE"
    if isinstance(exc, (BarrierHarnessError, DayGuardError)) or type(exc).__name__ in ("ExecError", "HarnessError"):
        return "HARNESS_ERROR"
    return "UNCLASSIFIED_EXCEPTION"


# ---------------------------------------------------------------------------------------------------------------------
# abertura e preflight
# ---------------------------------------------------------------------------------------------------------------------
def open_new_run(registry: Registry, rim: dict, *, purpose: str, parent_run_id: str | None = None,
                 reason: str | None = None, clock=time.time) -> tuple:
    """RUN OPEN: aloca o `run_id`, grava `rim.json`/`run_id.json` e abre o ledger (estado CREATED)."""
    info = registry.open_run(rim, purpose=purpose, parent_run_id=parent_run_id, reason=reason)
    run_dir = Path(info["run_dir"])
    (run_dir / "rim.json").write_bytes(rim_mod.canonical_rim_bytes(rim))
    (run_dir / "run_id.json").write_bytes(canonical_bytes({k: info[k] for k in ("run_id", "rim_sha256", "rim12", "attempt",
                                                                                 "kind", "purpose", "parent_run_id")}))
    ledger = RunLedger(run_dir, clock=clock)
    ledger.open({"run_id": info["run_id"], "rim_sha256": info["rim_sha256"], "kind": info["kind"],
                 "timeline": rim["declared"]["timeline"], "purpose": purpose, "parent_run_id": parent_run_id,
                 "attempt": info["attempt"]})
    return info, ledger


def run_preflight(ledger: RunLedger, registry: Registry, rim: dict, facts: dict, *, projected_growth_bytes=None) -> None:
    """CREATED -> PREFLIGHTED, ou ABORTED(PREFLIGHT_FAILED) (custo de negocio zero)."""
    mismatches = rim_mod.observed_mismatches(rim)
    if mismatches:
        ledger.append("RIM_VERIFIED", {"ok": False, "mismatches": mismatches})
    else:
        ledger.append("RIM_VERIFIED", {"ok": True})
    fails = preflight_mod.judge_preflight(facts, rim, projected_growth_bytes=projected_growth_bytes) if not mismatches else []
    if mismatches or fails:
        ledger.terminate("PREFLIGHT_FAILED", {"mismatches": mismatches, "failures": fails})
        registry.record_terminal(ledger.records[0]["payload"]["run_id"], ABORTED, "PREFLIGHT_FAILED", ledger.tail)
        raise PreflightFailed(json.dumps({"mismatches": mismatches, "failures": fails}, ensure_ascii=False))
    ledger.append("PREFLIGHT_PASSED", {"checks": sorted(preflight_mod.REQUIRED)})


def seal_unclean_exit(run_dir, registry: Registry, *, alive=pid_alive, clock=time.time) -> bool:
    """R0: run nao-terminal encontrado sem runner vivo => ABORTED(UNCLEAN_EXIT). Devolve True se selou."""
    ledger = RunLedger(run_dir, clock=clock)
    if ledger.state not in LIVE:
        return False
    lock = RunLock(run_dir, alive=alive)
    lock_path = Path(run_dir) / LOCK_NAME
    if lock_path.exists() and not lock.orphan():
        raise ResumeRefused("ha um runner vivo segurando RUN.LOCK: nada a selar")
    run_id = ledger.records[0]["payload"]["run_id"]
    ledger.append("ABORT_INTENT", {"class": "UNCLEAN_EXIT", "open_day": ledger.replay.open_day,
                                   "last_closed_day": ledger.replay.last_closed})
    ledger.terminate("UNCLEAN_EXIT", {"open_day": ledger.replay.open_day, "last_closed_day": ledger.replay.last_closed})
    if lock_path.exists():
        lock_path.unlink()
    registry.record_terminal(run_id, ABORTED, "UNCLEAN_EXIT", ledger.tail)
    return True


# ---------------------------------------------------------------------------------------------------------------------
# o runner
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class NhRunner:
    run_dir: Path
    rim: dict
    ledger: RunLedger
    registry: Registry
    services: RunServices
    stop: StopFlag = field(default_factory=StopFlag)
    lock: RunLock | None = None
    clock: Callable = time.time
    current_day: int | None = None
    current_hook: str | None = None
    timeline: list = field(default_factory=list)
    free_history: list = field(default_factory=list)
    divergence_days: list = field(default_factory=list)
    status: dict = field(default_factory=dict)

    # -- propriedades derivadas do RIM -------------------------------------------------------------------------------
    @property
    def run_id(self) -> str:
        return self.ledger.records[0]["payload"]["run_id"]

    @property
    def decl(self) -> dict:
        return self.rim["declared"]

    # -- guardas --------------------------------------------------------------------------------------------------------
    def _check_stop(self) -> None:
        if self.stop.requested:
            raise OperatorStop(self.stop.reason or "OPERATOR_STOP")

    def _wrap(self, hooks: Hooks) -> Hooks:
        wrapped = {}
        for name in HOOK_NAMES:
            wrapped[name] = self._wrap_one(name, getattr(hooks, name))
        return Hooks(**wrapped)

    def _wrap_one(self, name: str, fn: Callable) -> Callable:
        def inner(*args, **kwargs):
            self._check_stop()
            if name == "clock_set" and args and args[0] != self.current_day:
                raise DayGuardError(f"clock_set(dia {args[0]}) fora do dia corrente {self.current_day}")
            self.current_hook = name
            self.status.update({"hook": name, "day": self.current_day})
            started = time.monotonic()
            result = fn(*args, **kwargs)
            self.timeline.append({"day": self.current_day, "hook": name, "ms": int((time.monotonic() - started) * 1000)})
            if name == "floor_activation" and self.current_day == self.decl["timeline"]["floor_day"]:
                self.ledger.append("FLOOR_ARMED", {"day": self.current_day})
            if name == "restart_barrier":
                self.ledger.append("RESTART_DONE", {"day": self.current_day})
            self.current_hook = None
            return result
        return inner

    # -- partida (R0) -----------------------------------------------------------------------------------------------------
    def _guard_start(self) -> None:
        self.ledger.verify()
        interlock.verify_ledger_authorization(self.ledger, self.run_dir)
        state = self.ledger.state
        if state == RUNNING:
            alive = self.lock.alive if self.lock is not None else pid_alive
            sealed = seal_unclean_exit(self.run_dir, self.registry, alive=alive)
            raise ResumeRefused(f"R0: run {self.run_id} encontrado em RUNNING; sem resume (selado={sealed}). Use novo run_id.")
        if state != PREFLIGHTED:
            raise ResumeRefused(f"R0: run {self.run_id} em estado {state}: nada a executar")

    # -- laco -----------------------------------------------------------------------------------------------------------------
    def run(self) -> dict:
        self._guard_start()
        lock = self.lock or RunLock(self.run_dir)
        lock.acquire()
        try:
            return self._run_locked()
        finally:
            lock.release()

    def _run_locked(self) -> dict:
        timeline = self.decl["timeline"]
        last = timeline["last_day"]
        if self.services.awake is not None and self.services.awake() is not True:
            raise PreflightFailed("maquina nao mantida acordada")
        interlock.enter_running(self.ledger, self.run_dir, run_id=self.run_id,
                                rim_sha256=self.ledger.records[0]["payload"]["rim_sha256"], kind=self.rim["kind"])
        hooks = self._wrap(self.services.hooks)
        factory = self.services.orchestrator_factory or (
            lambda h: RunOrchestrator(h, floor_day=timeline["floor_day"], last_day=last))
        orchestrator = factory(hooks)
        probe_after = {timeline["floor_day"], timeline["restart_day"], last}
        try:
            self._probe(0, "pre_d0")
            for day in range(0, last + 1):
                self._check_stop()
                self.current_day = day
                self.status.update({"day": day, "hook": None, "seq": self.ledger.tail["seq"]})
                self.ledger.append("DAY_STARTED", {"day": day})
                orchestrator.run(first_day=day, last_day=day)
                if day in probe_after:
                    self._probe(day, "post_day")
                self._close_day(day)
            return self._finalize()
        except DayViolation as error:
            return self._terminate([v["class"] for v in error.violations], {"violations": error.violations})
        except Exception as error:                                                # noqa: BLE001 - classificado, nunca engolido
            return self._terminate([classify_exception(error, self.current_hook)],
                                   {"error": f"{type(error).__name__}: {error}", "hook": self.current_hook,
                                    "day": self.current_day})

    def _probe(self, day: int, when: str) -> None:
        result = self.services.probe(day, when)
        self.ledger.append("PROBE_DR1", {"day": day, "when": when, "clean": bool(result.get("clean"))})
        if result.get("clean") is not True:
            raise DayViolation([{"class": "ORACLE_ISOLATION_VIOLATION", "detail": f"sonda DR-1 ({when}, dia {day}) nao limpa"}])

    def _close_day(self, day: int) -> None:
        facts = self.services.day_facts(day)
        started = next(r for r in reversed(self.ledger.records) if r["event"] == "DAY_STARTED" and r["payload"]["day"] == day)
        if isinstance(facts.get("time"), dict):
            facts["time"]["real_open"] = started["t_real"]
            facts["time"]["real_close"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))
        d = self.decl
        violations, warnings, divergences = daily.judge_day(
            facts, disk_policy=d["disk_policy"], clock_skew_limit_ms=d["params"]["clock_skew_limit_ms"],
            o1_limit_ms=d["params"]["o1_limit_ms"], balance_mode=d["params"]["balance_mode"], free_history=self.free_history,
            remaining_days=d["timeline"]["last_day"] - day, growth_per_day_bytes=self.services.growth_per_day_bytes)
        days_dir = self.run_dir / "days"
        days_dir.mkdir(exist_ok=True)
        prev = daily.GENESIS
        if day > 0:
            prev = json.loads((days_dir / f"D{day - 1:03d}.json").read_bytes().decode("utf-8"))["hash"]
        try:
            record = daily.build_day_record(run_id=self.run_id, day=day, facts=facts, prev_hash=prev, violations=violations,
                                            warnings=warnings, divergences=divergences)
        except TypeError as error:                                    # fato nao serializavel (ex.: float): falha do instrumento
            violations = violations + [{"class": "HARNESS_ERROR", "detail": f"fatos do dia nao serializaveis: {error}"}]
            record = daily.build_day_record(run_id=self.run_id, day=day, facts={"unserializable": str(error)}, prev_hash=prev,
                                            violations=violations, warnings=warnings, divergences=divergences)
        (days_dir / f"D{day:03d}.json").write_bytes(canonical_bytes(record))
        if violations:
            raise DayViolation(violations)
        for warning in warnings:
            if warning.startswith("DISK_WARNING"):
                self.ledger.append("DISK_WARNING", {"day": day, "warning": warning})
        if divergences:
            self.divergence_days.append(day)
            self.ledger.append("BALANCE_DIVERGENCE", {"day": day, "divergences": divergences})
        self.free_history.append(facts["disk"]["free_bytes"])
        self.ledger.append("DAY_CLOSED", {"day": day, "hash": record["hash"]})

    def _finalize(self) -> dict:
        self.ledger.append("RUN_FINALIZING", {"balance_divergence_days": list(self.divergence_days)})
        outcome = self.services.finalize()
        if outcome.get("violations"):
            raise DayViolation(outcome["violations"])
        digest = package.seal(self.run_dir, run_id=self.run_id, ledger_tail=self.ledger.tail)
        self.ledger.append("PACKAGE_SEALED", {"digest": digest})
        verdict = package.verify(self.run_dir, expected_digest=digest)
        if not verdict["ok"]:
            raise DayViolation([{"class": "PACKAGE_VERIFY_FAILED", "detail": "; ".join(verdict["problems"])}])
        self.ledger.append("COMPLETED", {"digest": digest})
        self.registry.record_terminal(self.run_id, COMPLETED, None, self.ledger.tail, digest)
        return {"state": COMPLETED, "digest": digest, "run_id": self.run_id,
                "balance_divergence_days": list(self.divergence_days)}

    # -- terminais ------------------------------------------------------------------------------------------------------------
    def _terminate(self, classes: list, detail: dict) -> dict:
        state = worst_state(classes)
        primary = next(c for c in classes if terminal_state(c) == state)
        if self.ledger.state not in LIVE:
            raise LedgerError(f"terminate em estado {self.ledger.state}")
        self.ledger.append("ABORT_INTENT", {"class": primary, "classes": classes})
        try:
            preserved = self.services.preserve(primary)
        except Exception as error:                                                # noqa: BLE001 - a falha entra no ledger
            preserved = {"preservation_error": f"{type(error).__name__}: {error}"}
        self.ledger.terminate(primary, {"classes": classes, "preserved": preserved, **detail})
        digest = None
        try:
            digest = package.seal(self.run_dir, run_id=self.run_id, ledger_tail=self.ledger.tail, partial=True)
            self.ledger.append("PARTIAL_PACKAGE_SEALED", {"digest": digest})
        except Exception as error:                                                # noqa: BLE001
            self.ledger.append("PARTIAL_PACKAGE_SEALED", {"digest": None, "error": f"{type(error).__name__}: {error}"})
        self.registry.record_terminal(self.run_id, state, primary, self.ledger.tail, digest)
        return {"state": state, "class": primary, "run_id": self.run_id, "digest": digest}


__all__ = ["NhRunner", "RunServices", "open_new_run", "run_preflight", "seal_unclean_exit", "classify_exception",
           "DayViolation", "DayGuardError", "HOOK_NAMES"]
