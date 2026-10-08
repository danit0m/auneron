"""
SIM-1.5F -- modelo de crescimento (emenda C), projecao de disco com parada controlada (emenda D), fatos de crescimento
no registro diario, P14 no preflight e o gate do NH `RUN` (so abre com modelo validado e espaco suficiente).
"""

from __future__ import annotations

import copy
import json

import pytest

from sim.runner import daily
from sim.runner import disk
from sim.runner import growth
from sim.runner import preflight
from sim.runner import runner as runner_mod
from sim.runner.declare import DEFAULT_DISK_POLICY
from sim.runner.declare import GIB
from sim.runner.errors import DiskGuardError
from sim.runner.errors import PreflightFailed
from sim.tests.runner_support import good_day_facts
from sim.tests.runner_support import good_growth
from sim.tests.runner_support import good_preflight_facts
from sim.tests.runner_support import make_rim
from sim.tests.runner_support import make_runner
from sim.tests.runner_support import mutated
from sim.tests.runner_support import services_for
from sim.tests.runner_support import setup_run

NH = {"ops": 4987, "accounts": 880, "days": 180}
SLOTS = [(0, "09:00"), (1, "09:00"), (2, "11:00"), (3, "10:00")]


def make_days(tmp_path, n=21, *, ops_step=250, db_step=10_000_000, vhdx_step=40_000_000, free_start=40 * GIB, mutate=None):
    run_dir = tmp_path / "run"
    (run_dir / "days").mkdir(parents=True, exist_ok=True)
    prev, records = daily.GENESIS, []
    for day in range(n):
        facts = good_day_facts(ops_through_day=ops_step * (day + 1))
        facts["growth"] = good_growth(db=100_000_000 + db_step * day, vhdx=10 * GIB + vhdx_step * day)
        facts["growth"]["state_bytes"] = {"driver": 1_000_000 + 100_000 * day, "collector": 2_000_000 + 300_000 * day}
        facts["growth"]["evidence_bytes"] = 3_000_000 + 1_000_000 * day
        facts["growth"]["log_bytes"] = {"backend": 5_000_000, "driver": 100_000}
        facts["disk"]["free_bytes"] = free_start - 50_000_000 * day
        if mutate:
            mutate(day, facts)
        record = daily.build_day_record(run_id="RUN-x-A01", day=day, facts=facts, prev_hash=prev, violations=[], warnings=[])
        (run_dir / "days" / f"D{day:03d}.json").write_bytes(json.dumps(record, sort_keys=True).encode())
        prev = record["hash"]
        records.append(record)
    return run_dir, records


def build(tmp_path, **kwargs):
    run_dir, _ = make_days(tmp_path, **{k: v for k, v in kwargs.items() if k in ("n", "mutate", "ops_step", "db_step", "vhdx_step")})
    return growth.build_growth_model(run_dir, nh_targets=NH, real_hours=kwargs.get("real_hours", 3.0), source_run_id="RUN-x-A01",
                                     package_digest="d" * 64)


# ------------------------------------------------------------------------------------------------- o modelo
def test_the_model_declares_samples_unit_period_assumptions_margin_and_limitations(tmp_path):
    model = build(tmp_path)
    assert model["unit"] == "bytes" and model["status"] == "ESTIMATE" and model["capacity_proof"] is False
    assert model["period"]["samples"] == 21 and model["period"]["virtual_days"] == 20 and model["period"]["ops"] == 5000
    assert model["safety_margin_permille"] == 1500 and model["assumptions"] and model["limitations"]
    assert any("nao e prova de capacidade" in l for l in model["limitations"])
    assert model["source"] == {"run_id": "RUN-x-A01", "package_digest": "d" * 64}
    assert set(model["components"]) == set(growth.COMPONENTS)


def test_the_projection_is_the_larger_of_the_three_rates_times_the_margin(tmp_path):
    model = build(tmp_path, real_hours=3.0)
    db = model["components"]["db"]
    by_ops = db["delta"] * NH["ops"] // model["period"]["ops"]
    by_days = db["delta"] * NH["days"] // model["period"]["virtual_days"]
    by_hours = int(db["delta"] * growth.NH_REAL_HOURS / 3.0)
    assert db["projected_nh_increment_bytes"] == max(by_ops, by_days, by_hours) and by_days > by_ops
    host = model["host"]
    assert model["projected_growth_bytes"] == max(host["estimate_by_components_bytes"], host["estimate_by_vhdx_bytes"]) * 1500 // 1000
    assert model["projected_growth_bytes"] > 0


def test_a_slower_real_run_raises_the_hourly_rate_and_never_lowers_the_estimate(tmp_path):
    fast = build(tmp_path / "a", real_hours=6.0)["projected_growth_bytes"]
    slow = build(tmp_path / "b", real_hours=0.5)["projected_growth_bytes"]
    assert slow >= fast


def test_the_model_is_canonical_and_its_hash_is_stable(tmp_path):
    model = build(tmp_path)
    assert growth.model_bytes(model) == growth.model_bytes(copy.deepcopy(model))
    assert growth.model_sha256(model) == growth.model_sha256(copy.deepcopy(model)) and len(growth.model_sha256(model)) == 64
    assert growth.per_virtual_day_bytes(model, 180) == model["projected_growth_bytes"] // 180


# ------------------------------------------------------------------------------------------------- dados ausentes/invalidos reprovam
@pytest.mark.parametrize("name,mutate", [
    ("db ausente", lambda d, f: f["growth"].pop("db_bytes") if d == 5 else None),
    ("db negativo", lambda d, f: f["growth"].update(db_bytes=-1) if d == 5 else None),
    ("db bool", lambda d, f: f["growth"].update(db_bytes=True) if d == 5 else None),
    ("vhdx ausente", lambda d, f: f["growth"].pop("vhdx_bytes") if d == 0 else None),
    ("estado sem collector", lambda d, f: f["growth"].update(state_bytes={"driver": 1}) if d == 3 else None),
    ("logs vazios", lambda d, f: f["growth"].update(log_bytes={}) if d == 7 else None),
    ("top_relations vazio", lambda d, f: f["growth"].update(top_relations=[]) if d == 2 else None),
    ("grupo growth ausente", lambda d, f: f.pop("growth") if d == 9 else None),
])
def test_missing_or_invalid_growth_data_fails_the_generation(tmp_path, name, mutate):
    run_dir, _ = make_days(tmp_path, mutate=mutate)
    with pytest.raises((growth.GrowthModelError, KeyError)):
        growth.build_growth_model(run_dir, nh_targets=NH, real_hours=3.0, source_run_id="r", package_digest="d")


def test_too_few_samples_a_broken_chain_and_flat_ops_are_refused(tmp_path):
    run_dir, _ = make_days(tmp_path / "few", n=10)
    with pytest.raises(growth.GrowthModelError, match="amostras insuficientes"):
        growth.build_growth_model(run_dir, nh_targets=NH, real_hours=3.0, source_run_id="r", package_digest="d")
    run_dir, _ = make_days(tmp_path / "chain")
    target = run_dir / "days" / "D007.json"
    target.write_bytes(target.read_bytes().replace(b'"db_bytes":', b'"db_bytes": 0, "x":', 1) if False else
                       json.dumps({**json.loads(target.read_bytes()), "run_id": "outro"}, sort_keys=True).encode())
    with pytest.raises(growth.GrowthModelError, match="cadeia diaria invalida"):
        growth.build_growth_model(run_dir, nh_targets=NH, real_hours=3.0, source_run_id="r", package_digest="d")
    run_dir, _ = make_days(tmp_path / "flat", ops_step=0)
    with pytest.raises(growth.GrowthModelError, match="nao executou operacoes"):
        growth.build_growth_model(run_dir, nh_targets=NH, real_hours=3.0, source_run_id="r", package_digest="d")


@pytest.mark.parametrize("targets,hours", [({**NH, "ops": 0}, 3.0), ({**NH, "days": -1}, 3.0), ({"ops": 1, "accounts": 1}, 3.0),
                                           (NH, 0), (NH, -2), (NH, "3")])
def test_invalid_targets_and_durations_are_refused(tmp_path, targets, hours):
    run_dir, _ = make_days(tmp_path)
    with pytest.raises(growth.GrowthModelError):
        growth.build_growth_model(run_dir, nh_targets=targets, real_hours=hours, source_run_id="r", package_digest="d")


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(capacity_proof=True), lambda m: m.update(status="PROOF"), lambda m: m.update(unit="MB"),
    lambda m: m.update(assumptions=[]), lambda m: m.update(limitations=[]), lambda m: m.update(safety_margin_permille=900),
    lambda m: m.update(projected_growth_bytes=1), lambda m: m["components"].pop("logs"), lambda m: m["period"].update(samples=3),
    lambda m: m["components"]["db"].update(projected_nh_increment_bytes=-5), lambda m: m.pop("host"),
    lambda m: m["host"].update(estimate_by_components_bytes=7),
])
def test_an_edited_or_inconsistent_model_is_refused_on_validation(tmp_path, mutate):
    model = build(tmp_path)
    mutate(model)
    with pytest.raises(growth.GrowthModelError):
        growth.validate_growth_model(model)


def test_float_or_unserializable_growth_facts_end_the_run_as_a_harness_error_not_an_unclassified_exception(tmp_path):
    run = setup_run(tmp_path)

    def facts(day):
        out = good_day_facts()
        out["growth"]["db_bytes"] = 1.5
        return out

    result = make_runner(run, services_for(run, slots=SLOTS, day_facts=facts)).run()
    assert (result["state"], result["class"]) == ("INVALID", "HARNESS_ERROR")
    assert daily.growth_problems({**good_growth(), "db_bytes": 1.5})


# ------------------------------------------------------------------------------------------------- NH so abre com modelo + espaco
def run_rim_with_model(model_hash):
    return make_rim("RUN", growth=model_hash)


def test_a_real_run_preflight_needs_a_validated_model_and_enough_space(tmp_path):
    model = build(tmp_path)
    rim = run_rim_with_model("9" * 64)
    facts = good_preflight_facts(rim)
    projected = model["projected_growth_bytes"]
    assert preflight.judge_preflight(facts, rim, projected_growth_bytes=projected) == []
    assert preflight.judge_preflight(facts, rim, projected_growth_bytes=None)[0]["check"] == "P12"       # sem modelo: nao abre
    big = mutated(facts, "P12.free_bytes", 20 * GIB)
    needed = disk.required_for_run(DEFAULT_DISK_POLICY, 15 * GIB, "RUN")
    assert needed > 20 * GIB
    assert preflight.judge_preflight(big, rim, projected_growth_bytes=15 * GIB)[0]["check"] == "P12"      # 19-20 GB nao bastam


def test_the_nh_does_not_open_with_the_current_19_gb_if_the_projection_is_large(tmp_path):
    policy = DEFAULT_DISK_POLICY
    assert disk.judge_preflight(19 * GIB, policy, 5 * GIB, "RUN").ok                  # 1,5 x 5 + 8 = 15,5 <= 19
    assert not disk.judge_preflight(19 * GIB, policy, 8 * GIB, "RUN").ok              # 1,5 x 8 + 8 = 20 > 19
    assert disk.judge_preflight(19 * GIB, policy, None, "REHEARSAL").ok               # o ensaio so exige o minimo absoluto


def test_load_growth_model_requires_the_file_and_its_hash_in_the_rim(tmp_path):
    from sim.runner import nh_phases

    model = build(tmp_path / "m")
    run_dir = tmp_path / "run2"
    (run_dir / "inputs").mkdir(parents=True)
    rim = run_rim_with_model(growth.model_sha256(model))
    with pytest.raises(PreflightFailed, match="sem growth_model.json"):
        nh_phases.load_growth_model(run_dir, rim)
    (run_dir / "inputs" / "growth_model.json").write_bytes(growth.model_bytes(model))
    assert nh_phases.load_growth_model(run_dir, rim)["projected_growth_bytes"] == model["projected_growth_bytes"]
    with pytest.raises(PreflightFailed, match="difere do hash"):
        nh_phases.load_growth_model(run_dir, run_rim_with_model("0" * 64))
    assert nh_phases.load_growth_model(run_dir, make_rim("REHEARSAL")) is None


# ------------------------------------------------------------------------------------------------- fatos de crescimento
def test_growth_facts_are_required_in_every_daily_record():
    assert "growth" in daily.REQUIRED and "log_bytes" in daily.REQUIRED["growth"]
    facts = good_day_facts()
    facts.pop("growth")
    violations, _, _ = daily.judge_day(facts, disk_policy=DEFAULT_DISK_POLICY, clock_skew_limit_ms=3000, o1_limit_ms=1000)
    assert violations[0]["class"] == "HARNESS_ERROR" and "growth" in violations[0]["detail"]
    bad = good_day_facts()
    bad["growth"]["db_bytes"] = None
    violations, _, _ = daily.judge_day(bad, disk_policy=DEFAULT_DISK_POLICY, clock_skew_limit_ms=3000, o1_limit_ms=1000)
    assert [v["class"] for v in violations] == ["HARNESS_ERROR"]


# ------------------------------------------------------------------------------------------------- projecao de disco
def test_projection_math_uses_the_observed_drop_or_the_model_whichever_is_larger():
    assert disk.project_free_at_end([50 * GIB, 40 * GIB], 5) is None                                  # < 3 pontos e sem modelo
    assert disk.project_free_at_end([50 * GIB, 40 * GIB, 30 * GIB], 2) == 10 * GIB
    assert disk.project_free_at_end([50 * GIB, 49 * GIB, 48 * GIB], 2, growth_per_day_bytes=3 * GIB) == 42 * GIB
    assert disk.project_free_at_end([30 * GIB], 3, growth_per_day_bytes=2 * GIB) == 24 * GIB
    assert disk.project_free_at_end([30 * GIB, 31 * GIB, 32 * GIB], 4) == 32 * GIB                      # espaco que cresce nao projeta queda
    with pytest.raises(DiskGuardError):
        disk.project_free_at_end([], 3)
    with pytest.raises(DiskGuardError):
        disk.project_free_at_end([1, -1], 3)


def test_a_projection_below_the_operational_reserve_stops_the_run_in_a_controlled_way_with_preservation(tmp_path):
    run = setup_run(tmp_path)
    frees = iter([50 * GIB, 30 * GIB, 10 * GIB, 9 * GIB])

    def facts(day):
        out = good_day_facts()
        out["disk"]["free_bytes"] = next(frees)
        return out

    result = make_runner(run, services_for(run, slots=SLOTS, day_facts=facts)).run()
    assert (result["state"], result["class"]) == ("ABORTED", "DISK_PROJECTION_EXCEEDED")
    assert run.calls["preserve"] == ["DISK_PROJECTION_EXCEEDED"]
    from sim.runner.ledger import RunLedger

    ledger = RunLedger(run.run_dir)
    assert ledger.replay.last_closed == 1 and ledger.state == "ABORTED"                  # parou NA PROJECAO, antes de faltar disco
    assert json.loads((run.run_dir / "days" / "D002.json").read_bytes())["violations"][0]["class"] == "DISK_PROJECTION_EXCEEDED"


def test_the_validated_model_triggers_the_projection_stop_from_day_zero(tmp_path):
    run = setup_run(tmp_path)
    services = services_for(run, slots=SLOTS)
    services.growth_per_day_bytes = 20 * GIB                                              # 3 dias restantes x 20 GB > 50 GB livres
    result = make_runner(run, services).run()
    assert (result["state"], result["class"]) == ("ABORTED", "DISK_PROJECTION_EXCEEDED")
    assert run.calls["preserve"] == ["DISK_PROJECTION_EXCEEDED"]


def test_a_healthy_projection_lets_the_run_complete(tmp_path):
    run = setup_run(tmp_path)
    services = services_for(run, slots=SLOTS)
    services.growth_per_day_bytes = 1 * GIB
    assert make_runner(run, services).run()["state"] == "COMPLETED"


def test_projection_class_is_aborted_not_invalid():
    from sim.runner import taxonomy

    assert taxonomy.terminal_state("DISK_PROJECTION_EXCEEDED") == "ABORTED"
    assert runner_mod.classify_exception(DiskGuardError("x"), None) == "DISK_LOW"


def test_projection_between_zero_and_the_operational_reserve_stops_the_run():
    policy = dict(DEFAULT_DISK_POLICY)
    reserve = policy["reserve_bytes"]
    assert disk.judge_projection(None, policy).level == disk.OK
    assert disk.judge_projection(reserve, policy).level == disk.OK
    assert disk.judge_projection(reserve + 1, policy).level == disk.OK
    for projected in (reserve - 1, reserve // 2, 1, 0, -5):
        verdict = disk.judge_projection(projected, policy)
        assert verdict.level == disk.ABORT and verdict.reasons, projected
