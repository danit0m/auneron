"""
sim.scenario.v2 -- T-1..T-10 reaplicados (SIM-1.3 Design Freeze V1, 13).
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from decimal import Decimal

import pytest

from sim.generator.agenda import PUBLIC_OP_KEYS_V2
from sim.generator.agenda import PublicAgendaReader
from sim.generator.agenda import SeparationViolation
from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.generator.cli import DEFAULT_SEED
from sim.generator.cli import generate_v2
from sim.generator.timeline import to_minutes
from sim.oracle.schema import WORLD_ONLY_KEYS
from sim.oracle.schema import validate_scenario

from sim.tests.conftest import SCENARIOS
from sim.tests.conftest import SIM_ROOT

ROLE_OF = {
    "sim-faturamento": "analyst", "sim-receber": "analyst",
    "sim-cobranca-1": "analyst", "sim-cobranca-2": "analyst", "sim-cobranca-3": "analyst",
    "sim-gerente-fin": "manager", "sim-coordenador-fin": "manager",
}


# T-1 ---------------------------------------------------------------------
def test_v2_t1_same_seed_same_bytes(generated_v2):
    again = generate_v2("nh-small", DEFAULT_SEED)[1]
    first = generated_v2["nh-small"].files
    assert {k: sha256_bytes(v) for k, v in first.items()} == {k: sha256_bytes(v) for k, v in again.items()}


def test_v2_t1_independent_of_python_hash_seed():
    outputs = []
    for hash_seed in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=hash_seed)
        result = subprocess.run(
            [sys.executable, "-m", "sim.generator.cli", "hashes", "--variant", "nh-small",
             "--scenario-version", "v2"],
            cwd=SIM_ROOT.parent, env=env, capture_output=True, text=True, timeout=600,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
    assert "nh-small-v2-s340001" in outputs[0]


def test_v2_t1_frozen_artifacts_match_regeneration(scenario_v2):
    frozen = SCENARIOS / scenario_v2.scenario_id
    for name, data in scenario_v2.files.items():
        assert (frozen / name).read_bytes() == data, name
    assert validate_scenario(frozen)["status"] == "VALID"


def test_v2_t1_manifest_binds_inputs_and_lineage(scenario_v2, inputs_v2):
    manifest = scenario_v2.manifest
    assert manifest["schema_version"] == "sim.scenario.v2"
    assert manifest["inputs"] == dict(sorted(inputs_v2.hashes.items()))
    assert manifest["supersedes_for_execution"] == [scenario_v2.scenario_id.replace("-v2", "")]
    assert manifest["world_seed_shared_with_v1"] is True
    for name in ("public_agenda.json", "world.json", "oracle.json"):
        assert manifest["artifacts"][name] == sha256_bytes(scenario_v2.files[name])


# T-2 / T-3 ----------------------------------------------------------------
def test_v2_t2_population_identical_to_v1(generated, generated_v2):
    for variant in ("nh-small", "nh-standard"):
        v1 = generated[variant].world["customers"]
        v2 = generated_v2[variant].world["customers"]
        assert v1 == v2


def test_v2_t3_distributions(scenario_v2, inputs_v2):
    spec = inputs_v2.variant(scenario_v2.variant)
    customers = scenario_v2.world["customers"]
    profiles = Counter(c["profile"] for c in customers)
    for profile, expected in spec["profiles"].items():
        assert abs(profiles[profile] - expected) <= 1
    segments = inputs_v2.population["segments"]
    by_customer = {c["cust_ref"]: c["segment"] for c in customers}
    for title in scenario_v2.world["titles"]:
        lo, hi = segments[by_customer[title["cust_ref"]]]["ticket"]
        assert Decimal(lo) <= Decimal(title["valor"]) <= Decimal(hi)


# T-4 ---------------------------------------------------------------------
def test_v2_t4_case_coverage(scenario_v2, inputs_v2):
    minimum = inputs_v2.variant(scenario_v2.variant)["min_case_instances"]
    instances = scenario_v2.oracle["case_instances"]
    assert set(instances) == set(inputs_v2.cases["cases"])
    assert {"C-RACE-1", "C-AUTH-1", "C-AUTH-2", "C-DEAD-1"} <= set(instances)
    assert not {case: len(r) for case, r in instances.items() if len(r) < minimum}


def test_v2_t4_subjects_and_ids(scenario_v2):
    titles = {t["rec_ref"] for t in scenario_v2.world["titles"]}
    ids = []
    for item in scenario_v2.oracle["expectations"]:
        ids.append(item["exp_id"])
        if "rec_ref" in item["subject"]:
            assert item["subject"]["rec_ref"] in titles
        assert 0 <= item["day"] <= 179
        assert item["kind"] in scenario_v2.oracle["kind_sources"]
    assert ids == [f"E-{n:06d}" for n in range(1, len(ids) + 1)]


def test_v2_t4_calibration_pending_within_r13(scenario_v2):
    items = scenario_v2.oracle["expectations"]
    pending = sum(1 for e in items if e["semantics"] == "calibration_pending")
    assert pending * 100 <= len(items) * 3


# T-5 / T-6 ----------------------------------------------------------------
def _key(instant):
    return (instant["day"], to_minutes(instant["at"]))


def test_v2_t5_chronology(scenario_v2):
    for title in scenario_v2.world["titles"]:
        issue = _key(title["issue"])
        if title["fact_at"]:
            assert _key(title["fact_at"]) > issue
        if title["evidence_at"]:
            assert _key(title["evidence_at"]) >= _key(title["fact_at"])
        if title["reconciled_at"]:
            assert _key(title["reconciled_at"]) >= _key(title["evidence_at"])
        if title["reported_at"]:
            assert title["reconciled_at"] is not None
            assert _key(title["reported_at"]) > _key(title["reconciled_at"])
    previous = (-1, -1)
    for op in scenario_v2.agenda["ops"]:
        current = (op["day"], to_minutes(op["at"]))
        assert current >= previous
        previous = current


def test_v2_t6_calendar_and_methods(scenario_v2, inputs_v2, inputs):
    cal = inputs_v2.calendar
    assert cal.non_business_days == inputs.calendar.non_business_days
    assert cal.iso(0) == "2026-01-05" and cal.iso(cal.last_day) == "2026-07-03"
    assert "Nao reproduz" in inputs_v2.calendar_raw["normative_notice"]
    for title in scenario_v2.world["titles"]:
        fact, evidence = title["fact_at"], title["evidence_at"]
        if fact is None or evidence is None:
            continue
        if title["method"] == "boleto":
            assert cal.is_business(evidence["day"]) and evidence["at"] == "07:00"
        elif title["method"] == "ted":
            assert cal.is_business(fact["day"])


# T-7 ---------------------------------------------------------------------
def test_v2_t7_separation(scenario_v2):
    text = scenario_v2.files["public_agenda.json"].decode("utf-8")
    for op in scenario_v2.agenda["ops"]:
        assert set(op) <= PUBLIC_OP_KEYS_V2
        assert not set(op) & WORLD_ONLY_KEYS
    for token in ("fact_at", "evidence_at", "reported_at", "reconciled_at", "case_instances",
                  "expectations", "PRODUCT_FINDING", "planned_decider", "violations"):
        assert token not in text, token
    for case_id in scenario_v2.oracle["case_instances"]:
        assert f'"{case_id}"' not in text, case_id


def test_v2_t7_reader_and_tamper(tmp_path):
    source = SCENARIOS / "nh-small-v2-s340001"
    reader = PublicAgendaReader(source)
    for forbidden in ("oracle.json", "world.json"):
        with pytest.raises(SeparationViolation):
            reader.open(forbidden)
    target = tmp_path / "s"
    shutil.copytree(source, target)
    agenda = json.loads((target / "public_agenda.json").read_bytes())
    agenda["ops"][0]["reported_at"] = {"day": 1, "at": "10:00"}
    (target / "public_agenda.json").write_bytes(canonical_bytes(agenda))
    assert validate_scenario(target)["status"] == "INVALID"


def test_v2_t7_harness_never_imports_the_product():
    for path in SIM_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not name.startswith(("app", "backend", "frontend")), (path, name)


# T-8 ---------------------------------------------------------------------
def test_v2_t8_marking(scenario_v2):
    for op in scenario_v2.agenda["ops"]:
        if op["op"] == "create_receivable":
            assert op["cliente"].startswith("[SIM] ")
            assert op["email"].endswith("@nova-horizonte.example.com")
            assert re.match(r"^\+55 00 0000-\d{4}$", op["whatsapp"])
    for name, data in scenario_v2.files.items():
        assert not re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", data.decode("utf-8")), name


# T-9 ---------------------------------------------------------------------
def test_v2_t9_nba_applied_rules_names():
    from sim.oracle.rules_v2 import nba_applied_rules

    assert nba_applied_rules(False, False, 9, 1) == ()
    assert nba_applied_rules(True, False, 9, 1) == ("mark_overdue_only_candidate",)
    assert nba_applied_rules(False, True, 9, 1) == ("escalate_to_human_only_candidate",)
    assert nba_applied_rules(True, True, 3, 1) == ("mark_overdue_precedence",)
    assert nba_applied_rules(True, True, 5, 1_500_000) == ("high_exposure_early_escalation",)
    assert nba_applied_rules(True, True, 46, 50_001) == ("prolonged_overdue_escalation",)
    assert nba_applied_rules(False, True, 9, 1, True) == (
        "escalate_to_human_only_candidate", "prior_effect_contradiction_review")


def test_v2_t9_corridor_execution_rules():
    from sim.oracle import rules_v2

    assert rules_v2.mark_paid_execution_result("aberto", "aberto") is None
    assert rules_v2.mark_paid_execution_result("atrasado", "aberto") == "expected_status_mismatch"
    assert rules_v2.mark_paid_execution_result("pago", "aberto") == "already_paid"
    assert rules_v2.mark_overdue_execution_result("aberto", 9, 9, 10) is None
    assert rules_v2.mark_overdue_execution_result("pago", 9, 9, 10) == "only_aberto_may_transition"
    assert rules_v2.mark_overdue_execution_result("aberto", 12, 9, 10) == "due_date_changed_after_approval"


# T-10 --------------------------------------------------------------------
def test_v2_t10_capacity_and_presence(scenario_v2, inputs_v2):
    cal = inputs_v2.calendar
    personas = {p["user"]: p for p in inputs_v2.company["personas"]}
    per_day = Counter(
        (op["day"], op["actor"]) for op in scenario_v2.agenda["ops"] if op["op"] == "record_assessment"
    )
    for (day, actor), count in per_day.items():
        assert count <= personas[actor]["contacts_per_day"]
    late = to_minutes("18:00")
    for op in scenario_v2.agenda["ops"]:
        if op["actor"] == "harness":
            continue
        assert op["actor"] in ROLE_OF, op["actor"]
        minute = to_minutes(op["at"])
        evening_execution = op["op"] == "execute_mark_paid" and (minute >= late or minute == 0)
        if not evening_execution:
            assert cal.is_business(op["day"]), op
        for absence in personas[op["actor"]].get("absences", []):
            assert not absence["from_day"] <= op["day"] <= absence["to_day"], op
