"""
T-7 -- separacao do Oracle. Falha = cenario INVALID (Design Freeze v2, 8.5).
Tambem garante que nada no harness importa o produto (Oracle independente).
"""

from __future__ import annotations

import ast
import json
import shutil

import pytest

from sim.generator.agenda import PUBLIC_OP_KEYS
from sim.generator.agenda import PublicAgendaReader
from sim.generator.agenda import SeparationViolation
from sim.generator.canonical import canonical_bytes
from sim.oracle.schema import WORLD_ONLY_KEYS
from sim.oracle.schema import separation_violations
from sim.oracle.schema import validate_scenario

from sim.tests.conftest import SCENARIOS
from sim.tests.conftest import SIM_ROOT


def test_t7_agenda_has_only_public_fields(scenario):
    assert separation_violations(scenario.agenda, scenario.oracle, scenario.world) == []
    for op in scenario.agenda["ops"]:
        assert set(op) <= PUBLIC_OP_KEYS
        assert not set(op) & WORLD_ONLY_KEYS


def test_t7_agenda_leaks_no_oracle_or_world_values(scenario):
    text = scenario.files["public_agenda.json"].decode("utf-8")
    for token in ("fact_at", "evidence_at", "reported_at", "case_instances", "expectations",
                  "PRODUCT_FINDING", "calibration_pending", "profile"):
        assert token not in text, token
    for case_id in scenario.oracle["case_instances"]:
        assert f'"{case_id}"' not in text, case_id


def test_t7_reader_only_opens_public_files():
    reader = PublicAgendaReader(SCENARIOS / "nh-small-s340001")
    with reader.open("public_agenda.json") as handle:
        assert handle.read(1)
    for forbidden in ("oracle.json", "world.json", "../company/nova_horizonte/cases.yaml"):
        with pytest.raises(SeparationViolation):
            reader.open(forbidden)
    days = list(reader.iter_days())
    assert [day for day, _ in days] == list(range(180))
    assert all(op["day"] == day for day, ops in days for op in ops)


def test_t7_tampered_agenda_invalidates_scenario(tmp_path):
    target = tmp_path / "s"
    shutil.copytree(SCENARIOS / "nh-small-s340001", target)
    agenda = json.loads((target / "public_agenda.json").read_bytes())
    agenda["ops"][0]["profile"] = "P1"
    (target / "public_agenda.json").write_bytes(canonical_bytes(agenda))
    result = validate_scenario(target)
    assert result["status"] == "INVALID"
    with pytest.raises(SeparationViolation):
        PublicAgendaReader(target)


def test_t7_value_leak_invalidates_scenario(scenario):
    agenda = json.loads(scenario.files["public_agenda.json"])
    leaked = next(iter(scenario.oracle["case_instances"]))
    agenda["ops"][0]["code"] = leaked
    assert separation_violations(agenda, scenario.oracle, scenario.world)


def test_t7_frozen_scenarios_are_valid():
    for directory in sorted(SCENARIOS.iterdir()):
        assert validate_scenario(directory)["status"] == "VALID", directory.name


def test_t7_harness_never_imports_the_product():
    for path in SIM_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith(("app", "backend", "frontend")), (path, name)
