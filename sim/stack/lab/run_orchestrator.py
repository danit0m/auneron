"""
Orquestrador de execucao do HARNESS (Design Freeze V1/V1.1 sec. 7-8, E-3): a
ordem do tempo e do mundo pertence ao harness, nunca ao Driver.

Por slot (dia, hora):  clock -> exporta refs -> Collector(pre_slot) -> Driver
-> [op de harness: restart + barreira extraordinaria] -> Collector(post_slot)
Por dia: slots ate 18:00 -> clock 18:00 -> barreira diaria -> Collector
(after_daily_barrier) -> slots posteriores a 18:00.
D14: ativacao do floor ANTES do primeiro slot do dia + barreira extraordinaria
+ `after_floor_barrier`. Fim: Collector(end_of_run).

Tudo acontece por `Hooks` injetaveis: nenhum Docker aqui (a camada fina de
`docker exec` fica fora deste modulo e so e exercitada nos gates live).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

SNAPSHOT_AT = "18:00"
FLOOR_DAY = 14
RESTART_BARRIER = "after_restart_barrier"
FLOOR_BARRIER = "after_floor_barrier"


class OrchestrationError(RuntimeError):
    pass


@dataclass
class Hooks:
    clock_set: Callable[[int, str], None]
    driver_slots: Callable[[], list]
    driver_run_slot: Callable[[int, str], dict]
    driver_export_refs: Callable[[], dict]
    driver_mark_harness_done: Callable[[int, str], None]
    collector_load_refs: Callable[[dict], None]
    collector_trigger: Callable[[str, int, str], dict]
    collector_mark_barrier: Callable[[str], None]
    daily_barrier: Callable[[int], dict]
    restart_backend: Callable[[], dict]
    restart_barrier: Callable[[], dict]
    floor_activation: Callable[[], dict]


class RunOrchestrator:
    def __init__(self, hooks: Hooks, *, floor_day: int = FLOOR_DAY, last_day: int = 179) -> None:
        self.hooks = hooks
        self.floor_day = floor_day
        self.last_day = last_day
        self.events: list = []

    def _log(self, *event) -> None:
        self.events.append(tuple(event))

    def _refs(self) -> None:
        self.hooks.collector_load_refs(self.hooks.driver_export_refs())

    def _slot(self, day: int, at: str) -> None:
        h = self.hooks
        h.clock_set(day, at)
        self._log("clock", day, at)
        self._refs()
        h.collector_trigger("pre_slot", day, at)
        self._log("collect", "pre_slot", day, at)
        result = h.driver_run_slot(day, at)
        self._log("driver", day, at)
        for seq in result.get("harness_ops", []):
            h.restart_backend()
            self._log("restart", day, at)
            h.restart_barrier()
            h.collector_mark_barrier(RESTART_BARRIER)
            self._log("barrier", RESTART_BARRIER, day)
            h.driver_mark_harness_done(seq, "restart_backend")
        self._refs()
        h.collector_trigger("post_slot", day, at)
        self._log("collect", "post_slot", day, at)

    def run_day(self, day: int, slots: list) -> None:
        h = self.hooks
        if day == self.floor_day:
            h.floor_activation()
            h.collector_mark_barrier(FLOOR_BARRIER)
            self._log("barrier", FLOOR_BARRIER, day)
        ordered = sorted(slots)
        for at in [a for a in ordered if a <= SNAPSHOT_AT]:
            self._slot(day, at)
        h.clock_set(day, SNAPSHOT_AT)
        self._log("clock", day, SNAPSHOT_AT)
        h.daily_barrier(day)
        self._log("barrier", "daily", day)
        self._refs()
        h.collector_trigger("after_daily_barrier", day, SNAPSHOT_AT)
        self._log("collect", "after_daily_barrier", day, SNAPSHOT_AT)
        for at in [a for a in ordered if a > SNAPSHOT_AT]:
            self._slot(day, at)

    def run(self, first_day: int = 0, last_day: int | None = None) -> list:
        h = self.hooks
        last = self.last_day if last_day is None else last_day
        by_day: dict = {}
        for day, at in h.driver_slots():
            by_day.setdefault(day, []).append(at)
        for day in range(first_day, last + 1):
            self.run_day(day, by_day.get(day, []))
        if last == self.last_day:
            self._refs()
            h.clock_set(last, "23:59")
            h.collector_trigger("end_of_run", last, "23:59")
            self._log("collect", "end_of_run", last, "23:59")
        return self.events
