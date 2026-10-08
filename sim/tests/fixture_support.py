"""Montagem do mundo de teste da FIXTURE SR-1: bundle gerado em memoria/arquivo + Driver e Collector reais contra o produto falso."""

from __future__ import annotations

import json
from types import SimpleNamespace

from sim.collector.chainlog import ChainLog  # noqa: F401  (reexport para os testes)
from sim.oracle.collection_plan import compile_plan
from sim.rehearsal import generate as G
from sim.tests.collector_support import make_collector
from sim.tests.driver_support import make_driver
from sim.tests.fake_product import FakeProduct

OBSERVER_EMAIL = "sim-harness-observer@nova-horizonte.example.com"
D0 = "2026-01-05"


def build_fixture_world(tmp_path, scale: int = 300) -> SimpleNamespace:
    bundle_dir = tmp_path / "bundle"
    G.write_bundle(bundle_dir, scale_permille=scale)
    oracle = json.loads((bundle_dir / "oracle.json").read_bytes().decode("utf-8"))
    ops = json.loads((bundle_dir / "public_agenda.json").read_bytes().decode("utf-8"))["ops"]
    plan = compile_plan(oracle, ops, D0, -180, OBSERVER_EMAIL)
    fake = FakeProduct()
    driver, fake, store, evidence, _ = make_driver(tmp_path, fake, agenda_path=bundle_dir / "public_agenda.json")
    collector, _, chain, _ = make_collector(tmp_path, fake, plan)
    return SimpleNamespace(oracle=oracle, plan=plan, ops=ops, fake=fake, driver=driver, store=store, collector=collector,
                           chain=chain, evidence_path=tmp_path / "state" / "driver_evidence.jsonl", bundle_dir=bundle_dir)
