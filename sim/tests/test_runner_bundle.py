"""
SIM-1.5F -- bundle de cenario (NH ou fixture) sem mudar o esquema fechado do RIM, `declare` por bundle, P14 no preflight,
CLI de `growth-model` e `contamination-scan`, e a fronteira do candidato 1.5F.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from sim.runner import bundle as bundle_mod
from sim.runner import cli
from sim.runner import declare
from sim.runner import lifecycle
from sim.runner import nh_phases
from sim.runner import rim as rim_mod
from sim.runner.errors import LifecycleError
from sim.runner.errors import PreflightFailed
from sim.tests.conftest import SIM_ROOT
from sim.tests.runner_support import good_preflight_facts
from sim.tests.runner_support import make_rim
from sim.tests.runner_support import setup_run

NH_ORACLE_V22 = "331edf25f82edd51bc3cd97d39ef23fba10c264ee4475ca4790795e6077107ff"


def fake_git(monkeypatch):
    monkeypatch.setattr(declare, "git_out", lambda repo, *a: "c" * 40 if a[0] == "rev-parse" and ":" not in a[-1] else "t" + a[-1])


def declared_for(bundle, tmp_path, **over):
    base = tmp_path / "base.yml"
    base.write_text("a", encoding="utf-8")
    override = tmp_path / "over.yml"
    override.write_text("b", encoding="utf-8")
    args = dict(repo=tmp_path, bundle=bundle, base_image_digest="python@sha256:" + "b" * 64,
                lab_image_ids={"base": "i", "backend": "i", "postgres": "i"}, compose_files={"base": base, "override": override},
                quiescence_sha256="a" * 64, driver_params_sha256="b" * 64, plan_sha256="c" * 64, preregistration_sha256="d" * 64,
                authority_invariants_sha256="e" * 64, growth_model_sha256=None)
    args.update(over)
    return declare.build_declared(**args)


def observed_for(d):
    return {"driver_manifest_sha256": "f" * 64, "driver_manifest_agenda_sha256": d["agenda_sha256"], "compose_resolved_sha256": "0" * 64,
            "driver_image": {"tree": d["trees"]["sim_driver"], "base_digest": d["base_image_digest"]},
            "collector_image": {"tree": d["trees"]["sim_collector"], "base_digest": d["base_image_digest"]}}


# ------------------------------------------------------------------------------------------------- bundles
def test_nh_bundle_points_at_the_frozen_artifacts_and_is_a_real_run():
    bundle = bundle_mod.nh_bundle()
    assert bundle.kind == "RUN" and bundle.scenario_id == "nh-small-v2-s340001" and bundle.oracle_version == "v2.2"
    assert bundle.sha("oracle") == NH_ORACLE_V22 and bundle.derived_from_oracle_sha256.startswith("88ff8313")
    assert bundle.timeline == declare.NH_TIMELINE and bundle.sha("agenda").startswith("b1c855c1")


def test_sr1_bundle_is_a_rehearsal_generated_and_verified(tmp_path):
    bundle = bundle_mod.sr1_bundle(tmp_path / "b", scale_permille=300)
    assert bundle.kind == "REHEARSAL" and bundle.scenario_id.startswith("sim-harness-sr1") and not bundle.scenario_id.startswith("nh")
    assert bundle.timeline["floor_day"] == 14 and bundle.timeline["restart_day"] == 17 and bundle.timeline["last_day"] == 20
    assert bundle.sha("oracle") == bundle.derived_from_oracle_sha256
    (tmp_path / "b" / "oracle.json").write_bytes(b"{}")
    with pytest.raises(bundle_mod.BundleError, match="adulterado"):
        bundle_mod.sr1_bundle(tmp_path / "b", generate=False)


def test_bundle_factory_knows_only_nh_and_sr1(tmp_path):
    assert bundle_mod.get_bundle("nh").name == "nh"
    with pytest.raises(bundle_mod.BundleError, match="exige um diretorio"):
        bundle_mod.get_bundle("sr1")
    with pytest.raises(bundle_mod.BundleError, match="desconhecido"):
        bundle_mod.get_bundle("outro", tmp_path)


def test_scenario_id_selects_the_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(nh_phases, "bundles_root", lambda: tmp_path)
    assert nh_phases.bundle_for_scenario("nh-small-v2-s340001").name == "nh"
    assert nh_phases.bundle_for_scenario("sim-harness-sr1-s151001").name == "sr1"


# ------------------------------------------------------------------------------------------------- declare por bundle
def test_the_declared_rim_works_for_both_bundles_with_the_same_closed_schema(monkeypatch, tmp_path):
    fake_git(monkeypatch)
    nh = declared_for(bundle_mod.nh_bundle(), tmp_path)
    sr1 = declared_for(bundle_mod.sr1_bundle(tmp_path / "b", 300), tmp_path)
    assert set(nh) == set(sr1) and nh["scenario_id"] != sr1["scenario_id"]
    assert nh["oracle_sha256"] == NH_ORACLE_V22 and sr1["timeline"]["restart_day"] == 17
    assert rim_mod.rim12(rim_mod.build_rim("RUN", {**nh, "growth_model_sha256": "9" * 64}, observed_for(nh))) != \
        rim_mod.rim12(rim_mod.build_rim("REHEARSAL", sr1, observed_for(sr1)))


def test_a_run_rim_with_the_default_limits_is_enforce_and_a_rehearsal_may_report(monkeypatch, tmp_path):
    fake_git(monkeypatch)
    assert declared_for(bundle_mod.nh_bundle(), tmp_path)["params"]["balance_mode"] == "enforce"
    report = declared_for(bundle_mod.sr1_bundle(tmp_path / "b", 300), tmp_path, limits={**declare.DEFAULT_LIMITS, "balance_mode": "report"})
    assert rim_mod.build_rim("REHEARSAL", report, observed_for(report))["declared"]["params"]["balance_mode"] == "report"


# ------------------------------------------------------------------------------------------------- P14 no preflight
@pytest.mark.parametrize("key,value", [("volumes_created_after_previous_terminal", False), ("driver_state_present", True),
                                       ("collector_state_present", True), ("business_rows", 1), ("marker_rows", 2),
                                       ("previous_runs_non_terminal", True)])
def test_p14_failure_aborts_the_preflight_with_zero_business_cost(tmp_path, key, value):
    with pytest.raises(PreflightFailed, match="P14"):
        setup_run(tmp_path, facts_mutator=lambda facts: facts["P14"].__setitem__(key, value))


def test_p14_is_a_required_fact_group_for_every_preflight(tmp_path):
    with pytest.raises(PreflightFailed, match="fatos ausentes"):
        setup_run(tmp_path, facts_mutator=lambda facts: facts.pop("P14"))
    assert "P14" in __import__("sim.runner.preflight", fromlist=["REQUIRED"]).REQUIRED


# ------------------------------------------------------------------------------------------------- lifecycle
def test_balance_divergence_is_only_legal_while_running():
    open_event = ("OPENED", {"timeline": {"last_day": 2}})
    state = lifecycle.replay([open_event, ("PREFLIGHT_PASSED", {})])
    with pytest.raises(LifecycleError, match="proibido em PREFLIGHTED"):
        state.apply("BALANCE_DIVERGENCE", {})
    running = lifecycle.replay([open_event, ("PREFLIGHT_PASSED", {}), ("D0_AUTHORIZED", {"token_sha256": "t"}), ("RUN_STARTED", {})])
    running.apply("BALANCE_DIVERGENCE", {"day": 0})
    running.apply("ABORTED", {})
    with pytest.raises(LifecycleError):
        running.apply("BALANCE_DIVERGENCE", {"day": 1})


# ------------------------------------------------------------------------------------------------- CLI offline
def test_cli_contamination_scan_reports_markers_and_honours_the_allow_list(tmp_path, capsys):
    (tmp_path / "ok.txt").write_text("limpo", encoding="utf-8")
    assert cli.main(["contamination-scan", "--root", str(tmp_path)]) == 0
    capsys.readouterr()
    (tmp_path / "growth_model.json").write_text('{"source": "SIM-HARNESS-SR1 run"}', encoding="utf-8")
    assert cli.main(["contamination-scan", "--root", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["hits"][0]["path"] == "growth_model.json"
    assert cli.main(["contamination-scan", "--root", str(tmp_path), "--allow", "growth_model.json"]) == 0


def test_cli_growth_model_requires_a_completed_rehearsal(tmp_path, capsys):
    run = setup_run(tmp_path)
    assert cli.main(["growth-model", "--run-id", run.run_id, "--runs-root", str(run.registry.root)]) == 3
    assert "COMPLETED" in capsys.readouterr().out


def test_rehearsal_cli_generates_verifies_and_scans(tmp_path, capsys):
    from sim.rehearsal import cli as rehearsal_cli

    assert rehearsal_cli.main(["generate", "--out", str(tmp_path / "b"), "--scale", "300"]) == 0
    assert "bundle_sha256" in capsys.readouterr().out
    assert rehearsal_cli.main(["verify", "--dir", str(tmp_path / "b")]) == 0
    capsys.readouterr()
    assert rehearsal_cli.main(["scan", "--root", str(tmp_path / "b")]) == 1                # a fixture CARREGA os marcadores
    assert json.loads(capsys.readouterr().out)["hits"]
    (tmp_path / "b" / "world.json").write_bytes(b"{}")
    assert rehearsal_cli.main(["verify", "--dir", str(tmp_path / "b")]) == 1


# ------------------------------------------------------------------------------------------------- fronteira do 1.5F
BASE_COMMIT = "0d353766fbcbb9a85dbbfb7eab3cf36b035649b6"
FROZEN = ("sim/driver", "sim/collector", "sim/stack", "sim/live", "sim/scenarios", "sim/company", "sim/generator",
          "sim/oracle", "sim/evaluator")


def test_the_1_5f_candidate_changes_no_frozen_component_nor_the_evaluator_nor_the_oracle():
    if shutil.which("git") is None:
        pytest.skip("git indisponivel")
    from sim.tests import freeze_support as fs

    repo = SIM_ROOT.parent
    if not fs.require_commit(repo, BASE_COMMIT):
        pytest.skip("commit base do 1.5F ausente (clone raso)")
    # componentes congelados do SIM: igualdade exata com a base (arvore de trabalho e indice)
    assert fs.frozen_component_diff(repo, BASE_COMMIT, FROZEN) == []
    # SIM-FREEZE-IMPL-01: backend/frontend deixaram de ser comparados por igualdade absoluta com a base; o que se
    # prova e que nenhum commit do SIM, desde a base, altera o produto (e as arvores da base sao os literais).
    assert fs.provenance_violations(repo, {"base_1_5f": BASE_COMMIT}) == []
    assert [str(v) for v in fs.mixed_commit_violations(repo, BASE_COMMIT)] == []


def test_the_1_5f_cli_never_emits_a_d0_authorization_for_a_real_run(tmp_path, capsys):
    from sim.runner import runner as runner_mod
    from sim.runner.declare import GIB
    from sim.runner.registry import Registry

    rim = make_rim("RUN", growth="9" * 64)
    registry = Registry(tmp_path / "runs")
    info, ledger = runner_mod.open_new_run(registry, rim, purpose="FIRST")
    runner_mod.run_preflight(ledger, registry, rim, good_preflight_facts(rim), projected_growth_bytes=GIB)
    code = cli.main(["authorize-rehearsal", "--run-id", info["run_id"], "--runs-root", str(registry.root), "--by", "PO",
                     "--reference", "tentativa"])
    assert code == 3 and "exclusiva do PO" in capsys.readouterr().out
    assert not (info["run_dir"] / "D0_AUTHORIZATION.json").exists()


def test_cli_growth_model_from_a_completed_fixture_rehearsal_is_validated_and_registered(tmp_path, capsys):
    from sim.rehearsal import schedule as S
    from sim.runner import growth
    from sim.tests import test_runner_e2e as E2E
    from sim.tests.fixture_support import build_fixture_world
    from sim.tests.runner_support import good_growth

    counter = {"n": 0}

    def growing():
        counter["n"] += 1
        g = good_growth(db=100_000_000 + counter["n"] * 5_000_000, vhdx=10 * (2 ** 30) + counter["n"] * 20_000_000)
        g.pop("log_bytes")
        return g

    world = build_fixture_world(tmp_path, 300)
    run, live, nh = E2E.start(tmp_path, world, timeline=(S.LAST_DAY, S.FLOOR_DAY, S.RESTART_DAY), growth=growing)
    assert nh.run()["state"] == "COMPLETED"
    out = tmp_path / "gm.json"
    assert cli.main(["growth-model", "--run-id", run.run_id, "--runs-root", str(run.registry.root), "--out", str(out)]) == 0
    report = json.loads(capsys.readouterr().out)
    model = json.loads(out.read_bytes())
    assert growth.validate_growth_model(model) and report["growth_model_sha256"] == growth.model_sha256(model)
    assert model["source"]["run_id"] == run.run_id and model["period"]["samples"] == 21
    assert run.registry.runs()[run.run_id]["annotations"][-1]["type"] == "GROWTH_MODEL"


# ------------------------------------------------------------------------------------------------- C-7: pacote do NH sem marcadores
def test_a_real_run_package_with_a_fixture_marker_is_flagged_and_the_growth_model_lineage_is_the_only_exception(tmp_path):
    from sim.runner import harness_side

    (tmp_path / "inputs").mkdir()
    (tmp_path / "driver").mkdir()
    (tmp_path / "driver" / "driver_evidence.jsonl").write_text('{"cliente": "Padaria"}', encoding="utf-8")
    assert harness_side.contamination_problems(tmp_path, "RUN") == []
    (tmp_path / "inputs" / "growth_model.json").write_text('{"source": {"run_id": "RUN-x", "note": "SIM-HARNESS-SR1"}}', encoding="utf-8")
    assert harness_side.contamination_problems(tmp_path, "RUN") == []                     # linhagem autorizada
    (tmp_path / "driver" / "driver_evidence.jsonl").write_text('{"cliente": "[SIM-HARNESS-SR1] 0001"}', encoding="utf-8")
    assert harness_side.contamination_problems(tmp_path, "RUN")
    assert harness_side.contamination_problems(tmp_path, "REHEARSAL") == []               # o ensaio carrega os marcadores


def test_finalize_of_a_real_run_invalidates_on_contamination_and_a_rehearsal_does_not(tmp_path):
    from sim.runner.live import LiveServices

    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "f.txt").write_text("sr1-0001@sim-harness-sr1.example.com", encoding="utf-8")
    ports = type("P", (), {"finalize_extra": staticmethod(lambda target: {"violations": []})})()
    real = LiveServices(run_dir=tmp_path, run_id="r", plan={"items": []}, agenda_ops=[], timeline={}, ports=ports, baseline={},
                        expected_containers={}, expected_networks={}, barrier_hard_ms=1, kind="RUN")
    assert real.finalize()["violations"][0]["class"] == "FIXTURE_CONTAMINATION"
    rehearsal = LiveServices(run_dir=tmp_path, run_id="r", plan={"items": []}, agenda_ops=[], timeline={}, ports=ports, baseline={},
                             expected_containers={}, expected_networks={}, barrier_hard_ms=1, kind="REHEARSAL")
    assert rehearsal.finalize()["violations"] == []
    from sim.runner import taxonomy

    assert taxonomy.terminal_state("FIXTURE_CONTAMINATION") == "INVALID"
