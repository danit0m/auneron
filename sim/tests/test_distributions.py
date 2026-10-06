"""
T-3 -- distribuicoes dentro da tolerancia.
T-4 -- todo case_id com o minimo de instancias; toda expectativa referencia
sujeito existente.
T-10 -- capacidade e ausencias das personas respeitadas.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal


def test_t3_profiles_and_segments(scenario, inputs):
    spec = inputs.variant(scenario.variant)
    customers = scenario.world["customers"]
    assert len(customers) == spec["customers"]
    profiles = Counter(c["profile"] for c in customers)
    for profile, expected in spec["profiles"].items():
        assert abs(profiles[profile] - expected) <= 1, profile
    segments = Counter(c["segment"] for c in customers)
    for segment, expected in spec["segments"].items():
        share = Decimal(segments[segment]) / len(customers)
        target = Decimal(expected) / spec["customers"]
        assert abs(share - target) <= Decimal("0.02"), segment


def test_t3_drift_counts(scenario, inputs):
    spec = inputs.variant(scenario.variant)
    drifts = Counter(
        (c["profile"], c["drift"]["to"], c["drift"]["from_day"])
        for c in scenario.world["customers"] if c["drift"]
    )
    for rule in spec["drift"]:
        assert drifts[(rule["from"], rule["to"], rule["from_day"])] == rule["count"]


def test_t3_tickets_inside_segment_ranges(scenario, inputs):
    segments = inputs.population["segments"]
    by_customer = {c["cust_ref"]: c["segment"] for c in scenario.world["customers"]}
    for title in scenario.world["titles"]:
        lo, hi = segments[by_customer[title["cust_ref"]]]["ticket"]
        assert Decimal(lo) <= Decimal(title["valor"]) <= Decimal(hi), title["rec_ref"]


def test_t3_opening_portfolio(scenario, inputs):
    spec = inputs.variant(scenario.variant)["opening"]
    opening = [t for t in scenario.world["titles"] if t["opening"]]
    overdue = [t for t in opening if t["due_history"][0]["due_day"] < 0]
    assert len(opening) == spec["to_due"] + spec["overdue"]
    assert len(overdue) == spec["overdue"]
    assert all(t["issue"]["day"] == 0 for t in opening)


def test_t4_case_coverage(scenario, inputs):
    minimum = inputs.variant(scenario.variant)["min_case_instances"]
    instances = scenario.oracle["case_instances"]
    assert set(instances) == set(inputs.cases["cases"])
    below = {case: len(refs) for case, refs in instances.items() if len(refs) < minimum}
    assert not below


def test_t4_expectations_reference_existing_subjects(scenario):
    titles = {t["rec_ref"] for t in scenario.world["titles"]}
    customers = {c["cust_ref"] for c in scenario.world["customers"]}
    ids = []
    for item in scenario.oracle["expectations"]:
        ids.append(item["exp_id"])
        subject = item["subject"]
        if "rec_ref" in subject:
            assert subject["rec_ref"] in titles
        if "cust_ref" in subject:
            assert subject["cust_ref"] in customers
        assert 0 <= item["day"] <= 179
    assert ids == [f"E-{n:06d}" for n in range(1, len(ids) + 1)]


def test_t4_calibration_pending_within_r13(scenario):
    items = scenario.oracle["expectations"]
    pending = sum(1 for e in items if e["semantics"] == "calibration_pending")
    assert pending * 100 <= len(items) * 3


def test_t10_collector_capacity(scenario, inputs):
    capacity = {
        p["user"]: p["contacts_per_day"] for p in inputs.company["personas"]
        if p["function"] == "collections"
    }
    per_day = Counter(
        (op["day"], op["actor"]) for op in scenario.agenda["ops"] if op["op"] == "record_assessment"
    )
    for (day, actor), count in per_day.items():
        assert count <= capacity[actor], (day, actor, count)


def test_t10_personas_respect_calendar_and_absences(scenario, inputs):
    cal = inputs.calendar
    personas = {p["user"]: p for p in inputs.company["personas"]}
    for op in scenario.agenda["ops"]:
        if op["actor"] == "harness":
            continue
        assert cal.is_business(op["day"]), op
        for absence in personas[op["actor"]].get("absences", []):
            assert not absence["from_day"] <= op["day"] <= absence["to_day"], op


def test_t10_only_provisioned_personas_act(scenario, inputs):
    allowed = {
        p["user"] for p in inputs.company["personas"]
        if p["function"] not in ("provisioning_only", "system_principal")
    } | {"harness"}
    assert {op["actor"] for op in scenario.agenda["ops"]} <= allowed
