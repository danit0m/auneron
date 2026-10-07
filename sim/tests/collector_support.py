"""Orquestracao de teste: Driver + Collector contra o FakeProduct (imita o harness do lab)."""

from __future__ import annotations

import json
from pathlib import Path

from sim.collector.auth import ObserverSession
from sim.collector.chainlog import ChainLog
from sim.collector.client import GetOnlyClient
from sim.collector.client import TransportError as CollectorTransportError
from sim.collector.collect import Collector
from sim.oracle.collection_plan import compile_plan
from sim.tests.conftest import SCENARIOS
from sim.tests.conftest import SIM_ROOT
from sim.tests.driver_support import SCENARIO
from sim.tests.driver_support import make_driver
from sim.tests.fake_product import FakeProduct

ORACLE_V21 = SIM_ROOT / "oracle" / "artifacts" / f"{SCENARIO}-oracle-v2.1" / "oracle.json"
AGENDA = SCENARIOS / SCENARIO / "public_agenda.json"
OBSERVER = "sim-harness-observer"


def load_oracle() -> dict:
    return json.loads(ORACLE_V21.read_bytes())


def load_agenda_ops() -> list:
    return json.loads(AGENDA.read_bytes())["ops"]


def make_collector(tmp_path: Path, fake: FakeProduct, plan: dict, **kwargs):
    fake.transport_error = CollectorTransportError
    home = tmp_path / "collector"
    home.mkdir(parents=True, exist_ok=True)
    client = GetOnlyClient(fake, "http://sim-backend:8000", "k" * 64)
    session = ObserverSession(fake, "http://sim-backend:8000", "k" * 64, plan["observer_email"], "pw-observer",
                              home / "session.json")
    chain = ChainLog(home / "collected.jsonl", fsync=False)
    sleeps: list = []
    collector = Collector(plan, client, session, home, chain, sleep=sleeps.append, **kwargs)
    return collector, session, chain, sleeps


def export_refs(driver, collector) -> None:
    data = {"refs": driver.store.all_refs(), "due_days": driver.store.all_due()}
    (collector.home / "refs.json").write_text(json.dumps(data), encoding="utf-8")


def orchestrate(driver, fake: FakeProduct, collector, first: int = 0, last: int = 179, floor_day: int = 14,
                finish: bool | None = None) -> dict:
    """Ordem do harness: Collector(pre_slot) -> Driver -> Collector(post_slot) por slot;
    Collector(after_daily_barrier) ao fim de cada dia; barreira do floor em D14; end_of_run."""
    from sim.driver.runner import virtual_utc

    by_day: dict = {}
    for day, at in driver.slot_keys():
        by_day.setdefault(day, []).append(at)
    report = {"triggers": 0}
    for day in range(first, last + 1):
        for at in by_day.get(day, []):
            fake.now = virtual_utc(driver.d0, day, at, driver.offset)
            export_refs(driver, collector)
            collector.trigger("pre_slot", day, at)
            result = driver.run_slot(day, at)
            for seq in result["harness_ops"]:
                driver.mark_harness_done(seq, "restart")
            export_refs(driver, collector)
            collector.trigger("post_slot", day, at)
            report["triggers"] += 2
        fake.now = virtual_utc(driver.d0, day, "18:00", driver.offset)
        export_refs(driver, collector)
        if day == floor_day:
            collector.mark_barrier("after_floor_barrier")
        collector.trigger("after_daily_barrier", day, "18:00")
        report["triggers"] += 1
    if (last == 179) if finish is None else finish:
        collector.trigger("end_of_run", last, "23:59")
    return report
