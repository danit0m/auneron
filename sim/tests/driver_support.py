"""Montagem do Driver contra o FakeProduct (testes estaticos, sem Docker)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sim.driver.agenda import load_agenda
from sim.driver.agenda import load_manifest
from sim.driver.evidence import EvidenceLog
from sim.driver.http import DriverHttp
from sim.driver.params import Params
from sim.driver.params import load_params
from sim.driver.runner import Driver
from sim.driver.runner import virtual_utc
from sim.driver.sessions import PersonaSessions
from sim.driver.state import StateStore
from sim.tests.conftest import SCENARIOS
from sim.tests.fake_product import FakeProduct

SCENARIO = "nh-small-v2-s340001"
PERSONAS = ("sim-faturamento", "sim-receber", "sim-cobranca-1", "sim-cobranca-2", "sim-cobranca-3",
            "sim-gerente-fin", "sim-coordenador-fin")


class NoSleep:
    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def write_inputs(tmp_path: Path, agenda_path: Path | None = None, version_id: int = 7) -> tuple:
    agenda_path = agenda_path or (SCENARIOS / SCENARIO / "public_agenda.json")
    data = agenda_path.read_bytes()
    manifest = {
        "schema": "sim.driver.manifest.v1", "scenario_id": json.loads(data)["scenario_id"], "d0": "2026-01-05",
        "days": 180, "public_agenda_sha256": hashlib.sha256(data).hexdigest(),
        "mark_paid_version_id": version_id, "personas": list(PERSONAS),
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    manifest_path = tmp_path / "driver_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return agenda_path, manifest_path


def make_driver(tmp_path: Path, fake: FakeProduct | None = None, *, sleep=None, agenda_path=None,
                version_id: int = 7, home: Path | None = None, ops_filter=None, params_override=None):
    fake = fake or FakeProduct(version_id=version_id)
    sleep = sleep or NoSleep()
    agenda_file, manifest_file = write_inputs(tmp_path, agenda_path, version_id)
    manifest = load_manifest(manifest_file)
    ops = load_agenda(agenda_file, manifest)
    if ops_filter is not None:
        ops = [op for op in ops if ops_filter(op)]
    params = load_params()
    if params_override:
        params = Params(raw={**params.raw, **params_override}, sha256=params.sha256)
    home = home or (tmp_path / "state")
    store = StateStore(home / "driver_state.db", durable=False)
    evidence = EvidenceLog(home / "driver_evidence.jsonl", fsync=False)
    http = DriverHttp(fake, params["backend_url"], "k" * 64)
    sessions = PersonaSessions(http, store, {p: f"pw-{p}" for p in PERSONAS}, params["email_domain"],
                               int(params["session"]["relogin_margin_minutes"]))
    driver = Driver(params=params, ops=ops, manifest=manifest, http=http, sessions=sessions, store=store,
                    evidence=evidence, sleep=sleep)
    return driver, fake, store, evidence, sleep


def run_slots(driver, fake: FakeProduct, first: int = 0, last: int = 179, restart_note: str = "restart") -> list:
    """Executa os slots em ordem, mantendo o relogio virtual do fake e tratando
    as ops de harness como o orquestrador do lab faria."""
    results = []
    for day, at in driver.slot_keys():
        if not first <= day <= last:
            continue
        fake.now = virtual_utc(driver.d0, day, at, driver.offset)
        result = driver.run_slot(day, at)
        for seq in result["harness_ops"]:
            driver.mark_harness_done(seq, restart_note)
        results.append(result)
    return results
