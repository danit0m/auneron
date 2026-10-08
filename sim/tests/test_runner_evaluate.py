"""
SIM-1.5F -- `evaluate` offline e digest FINAL dos resultados (emenda B): pre-condicoes fail-closed, resultados fora do
digest do pacote mas cobertos por um digest vinculado ao ledger, deteccao de adulteracao de QUALQUER resultado,
`surprises` contra o pre-registro, modo de balanco `report` sem aceite silencioso e o CLI.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from sim.runner import cli
from sim.runner import harness_side
from sim.runner import package
from sim.runner import results as results_mod
from sim.runner.ledger import RunLedger
from sim.tests import test_runner_e2e as E2E


def completed_run(tmp_path, **kwargs):
    world = E2E.build_world(tmp_path)
    run, live, nh = E2E.start(tmp_path, world, **kwargs)
    result = nh.run()
    assert result["state"] == "COMPLETED", RunLedger(run.run_dir).events()[-1]
    return run, world, result


# ------------------------------------------------------------------------------------------------- caminho feliz
def test_evaluation_is_offline_outside_the_package_seal_and_bound_to_the_ledger(tmp_path, monkeypatch):
    run, world, result = completed_run(tmp_path)

    def no_process(*a, **k):
        raise AssertionError("evaluate nao pode criar processo (nem tocar Docker)")

    monkeypatch.setattr(subprocess, "run", no_process)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    report = harness_side.evaluate_package(run.run_dir, registry=run.registry)
    monkeypatch.undo()
    ledger = RunLedger(run.run_dir)
    recorded = [r for r in ledger.events() if r["event"] == "EVALUATED"][0]["payload"]
    assert recorded["results_digest"] == report["results"]["digest"] and set(recorded["files"]) == set(results_mod.RESULT_FILES)
    assert package.verify(run.run_dir, expected_digest=result["digest"])["ok"]              # o selo original NAO mudou
    assert results_mod.verify_results(run.run_dir)["ok"]
    annotation = run.registry.runs()[run.run_id]["annotations"][-1]
    assert annotation["type"] == "EVALUATED" and annotation["results_digest"] == recorded["results_digest"]
    assert "VERIFIED" not in (run.run_dir / "claim.json").read_text(encoding="utf-8")


def test_the_final_digest_is_the_sorted_listing_plus_the_package_digest(tmp_path):
    run, world, result = completed_run(tmp_path)
    report = harness_side.evaluate_package(run.run_dir, registry=run.registry)
    listed = results_mod.entries(run.run_dir)
    assert results_mod.digest_of(listed, result["digest"]) == report["results"]["digest"]
    assert results_mod.digest_of(listed, "0" * 64) != report["results"]["digest"]          # vinculado ao digest do pacote


# ------------------------------------------------------------------------------------------------- adulteracao
@pytest.mark.parametrize("name", ["evaluation.json", "surprises.json", "claim.json"])
def test_tampering_with_any_result_file_is_detected(tmp_path, name):
    run, world, result = completed_run(tmp_path)
    harness_side.evaluate_package(run.run_dir, registry=run.registry)
    target = run.run_dir / name
    target.write_bytes(target.read_bytes().replace(b"{", b"{ ", 1))
    verdict = results_mod.verify_results(run.run_dir)
    assert not verdict["ok"] and any(name in p or "alterado" in p for p in verdict["problems"])


def test_tampering_with_the_manifest_the_digest_the_ledger_or_the_seal_is_detected(tmp_path):
    run, world, result = completed_run(tmp_path)
    harness_side.evaluate_package(run.run_dir, registry=run.registry)
    manifest = run.run_dir / results_mod.MANIFEST_NAME
    original_manifest = manifest.read_bytes()
    manifest.write_bytes(original_manifest.replace(b'"digest":"', b'"digest":"0', 1))
    assert not results_mod.verify_results(run.run_dir)["ok"]
    manifest.write_bytes(original_manifest)
    digest_file = run.run_dir / results_mod.DIGEST_NAME
    digest_file.write_text("0" * 64 + "\n", encoding="utf-8")
    assert any("results_digest.txt" in p for p in results_mod.verify_results(run.run_dir)["problems"])
    digest_file.write_text(json.loads(original_manifest)["digest"] + "\n", encoding="utf-8")
    assert results_mod.verify_results(run.run_dir)["ok"]
    assert not results_mod.verify_results(run.run_dir, registry_digest="1" * 64)["ok"]
    ledger_file = run.run_dir / "ledger.jsonl"
    original_ledger = ledger_file.read_bytes()
    ledger_file.write_bytes(original_ledger.replace(b"EVALUATED", b"EVALUATEX", 1))
    assert any("ledger invalido" in p for p in results_mod.verify_results(run.run_dir)["problems"])
    ledger_file.write_bytes(original_ledger)
    (run.run_dir / "collector" / "collected.jsonl").write_bytes(b"x")                       # quebra o selo do pacote original
    assert any("selo do pacote" in p for p in results_mod.verify_results(run.run_dir)["problems"])


def _forge_recorded_files(monkeypatch, change):
    """Isola UMA dimensao: o ledger e encadeado, entao a forja e feita na leitura do payload EVALUATED (so no teste)."""
    original = RunLedger.events

    def patched(self):
        records = original(self)
        for record in records:
            if record["event"] == "EVALUATED":
                record["payload"] = {**record["payload"], "files": change(dict(record["payload"]["files"]))}
        return records

    monkeypatch.setattr(RunLedger, "events", patched)


def test_the_per_file_sha_in_the_ledger_is_checked_on_its_own(tmp_path, monkeypatch):
    run, world, result = completed_run(tmp_path)
    harness_side.evaluate_package(run.run_dir, registry=run.registry)
    assert results_mod.verify_results(run.run_dir)["ok"]
    _forge_recorded_files(monkeypatch, lambda files: {**files, "surprises.json": "0" * 64})
    verdict = results_mod.verify_results(run.run_dir)
    assert not verdict["ok"] and any("surprises.json: sha256 difere do ledger" in p for p in verdict["problems"])


def test_the_digest_is_recomputed_from_the_current_files_on_its_own(tmp_path, monkeypatch):
    run, world, result = completed_run(tmp_path)
    harness_side.evaluate_package(run.run_dir, registry=run.registry)
    target = run.run_dir / "evaluation.json"
    target.write_bytes(target.read_bytes().replace(b"{", b"{ ", 1))
    now = {e["path"]: e["sha256"] for e in results_mod.entries(run.run_dir)}
    _forge_recorded_files(monkeypatch, lambda files: now)            # a lista por arquivo "concorda": so o digest denuncia
    verdict = results_mod.verify_results(run.run_dir)
    assert not verdict["ok"] and any("nao produzem o digest" in p for p in verdict["problems"])


def test_a_missing_result_file_makes_the_final_verification_fail(tmp_path):
    run, world, result = completed_run(tmp_path)
    harness_side.evaluate_package(run.run_dir, registry=run.registry)
    (run.run_dir / "surprises.json").unlink()
    verdict = results_mod.verify_results(run.run_dir)
    assert not verdict["ok"] and "ilegiveis" in verdict["problems"][0]


# ------------------------------------------------------------------------------------------------- pre-condicoes
def test_evaluation_is_refused_twice_and_verify_only_needs_an_evaluated_run(tmp_path):
    run, world, result = completed_run(tmp_path)
    with pytest.raises(harness_side.EvaluateRefused, match="exige um run avaliado"):
        harness_side.evaluate_package(run.run_dir, registry=run.registry, verify_only=True)
    harness_side.evaluate_package(run.run_dir, registry=run.registry)
    before = {n: (run.run_dir / n).read_bytes() for n in results_mod.RESULT_FILES}
    with pytest.raises(harness_side.EvaluateRefused, match="ja foi avaliado"):
        harness_side.evaluate_package(run.run_dir, registry=run.registry)
    assert before == {n: (run.run_dir / n).read_bytes() for n in results_mod.RESULT_FILES}      # nada sobrescrito


def test_verify_only_recomputes_deterministically_and_flags_a_changed_result(tmp_path):
    run, world, result = completed_run(tmp_path)
    harness_side.evaluate_package(run.run_dir, registry=run.registry)
    again = harness_side.evaluate_package(run.run_dir, registry=run.registry, verify_only=True)
    assert again["verified"] is True and all(again["deterministic"].values())
    target = run.run_dir / "evaluation.json"
    target.write_bytes(target.read_bytes() + b" ")
    assert harness_side.evaluate_package(run.run_dir, registry=run.registry, verify_only=True)["verified"] is False


def test_only_a_completed_run_is_evaluated(tmp_path):
    world = E2E.build_world(tmp_path)
    run, live, nh = E2E.start(tmp_path, world, db_counts=lambda: {"accounts": 99, "approvals": 0, "work_items": 0})
    result = nh.run()
    assert result["state"] == "INVALID"
    with pytest.raises(harness_side.EvaluateRefused, match="so um run COMPLETED"):
        harness_side.evaluate_package(run.run_dir, registry=run.registry)
    assert not (run.run_dir / "evaluation.json").exists()


@pytest.mark.parametrize("override", [{"oracle_sha256": "0" * 64}, {"plan_sha256": "1" * 64}, {"preregistration_sha256": "2" * 64}])
def test_inputs_must_match_the_rim_hashes_or_nothing_is_written(tmp_path, override):
    world = E2E.build_world(tmp_path)
    run, live, nh = E2E.start(tmp_path, world, overrides=override)
    assert nh.run()["state"] == "COMPLETED"
    with pytest.raises(harness_side.EvaluateRefused, match="diferente do RIM"):
        harness_side.evaluate_package(run.run_dir, registry=run.registry)
    assert not [n for n in results_mod.RESULT_FILES if (run.run_dir / n).exists()]
    assert "EVALUATED" not in RunLedger(run.run_dir).replay.seen


def test_a_tampered_package_blocks_the_evaluation(tmp_path):
    run, world, result = completed_run(tmp_path)
    (run.run_dir / "inputs" / "oracle.json").write_bytes(b"{}")
    with pytest.raises(harness_side.EvaluateRefused, match="pacote nao verifica"):
        harness_side.evaluate_package(run.run_dir, registry=run.registry)


# ------------------------------------------------------------------------------------------------- surprises
PREREG = {"by_semantics": {"fact": 90, "abstain": 2, "known_limit": 3, "calibration_pending": 1, "not_representable": 1},
          "known_limit_expectations": 5, "finding_expectations": 4}


def evaluation(**summary):
    base = {"CORRECT": 80, "CORRECT_ABSTENTION": 2, "KNOWN_LIMIT": 5, "CALIBRATION_PENDING": 1, "NOT_REPRESENTABLE": 1, "PRODUCT_FINDING": 4}
    base.update(summary)
    return {"summary": base}


def test_surprises_are_clean_only_when_every_class_matches_the_preregistration():
    clean = harness_side.compute_surprises(evaluation(), PREREG, balance_divergences=0, collection_problems=[])
    assert clean["clean"] is True and clean["surprises"] == []


@pytest.mark.parametrize("change,klass", [({"DIVERGENCE": 1}, "DIVERGENCE"), ({"HARNESS_ERROR": 1}, "HARNESS_ERROR"),
                                          ({"PRODUCT_FINDING": 5}, "PRODUCT_FINDING"), ({"KNOWN_LIMIT": 4}, "KNOWN_LIMIT"),
                                          ({"CALIBRATION_PENDING": 0}, "CALIBRATION_PENDING"), ({"NOT_REPRESENTABLE": 2}, "NOT_REPRESENTABLE"),
                                          ({"CORRECT_ABSTENTION": 3}, "CORRECT_ABSTENTION")])
def test_each_deviation_from_the_preregistration_is_reported_never_absorbed(change, klass):
    report = harness_side.compute_surprises(evaluation(**change), PREREG, balance_divergences=0, collection_problems=[])
    assert report["clean"] is False and klass in {s["class"] for s in report["surprises"]}


def test_balance_divergences_and_collection_problems_are_surprises():
    report = harness_side.compute_surprises(evaluation(), PREREG, balance_divergences=2, collection_problems=["x"])
    assert {s["class"] for s in report["surprises"]} == {"BALANCE_DIVERGENCE", "COLLECTION_PROBLEM"}


# ------------------------------------------------------------------------------------------------- modo `report`
def test_report_mode_records_the_divergence_everywhere_and_never_hides_it(tmp_path):
    world = E2E.build_world(tmp_path)
    run, live, nh = E2E.start(tmp_path, world, balance_mode="report",
                              db_counts=lambda: {"accounts": len(world.fake.accounts) + 1, "approvals": len(world.fake.approvals),
                                                 "work_items": len(world.fake.work_items)})
    result = nh.run()
    assert result["state"] == "COMPLETED" and result["balance_divergence_days"] == [0, 1, 2]
    events = [r["event"] for r in RunLedger(run.run_dir).events()]
    assert events.count("BALANCE_DIVERGENCE") == 3
    record = json.loads((run.run_dir / "days" / "D000.json").read_bytes())
    assert record["balance_divergences"] and "BALANCE_DIVERGENCE" in record["warnings"][0]
    report = harness_side.evaluate_package(run.run_dir, registry=run.registry)
    assert report["surprises"]["clean"] is False and "BALANCE_DIVERGENCE" in {s["class"] for s in report["surprises"]["surprises"]}
    assert report["claim"]["balance_divergences"] == 3 and "never silently accepted" in report["claim"]["text"]


def test_enforce_mode_turns_the_same_divergence_into_an_invalid_run(tmp_path):
    world = E2E.build_world(tmp_path)
    run, live, nh = E2E.start(tmp_path, world, balance_mode="enforce",
                              db_counts=lambda: {"accounts": len(world.fake.accounts) + 1, "approvals": len(world.fake.approvals),
                                                 "work_items": len(world.fake.work_items)})
    assert nh.run()["class"] == "MASS_BALANCE_MISMATCH"


def test_a_real_run_can_never_use_report_mode():
    from sim.runner import rim as rim_mod
    from sim.runner.errors import RimError
    from sim.tests.runner_support import declared
    from sim.tests.runner_support import observed

    d = declared(growth="9" * 64, balance_mode="report")
    with pytest.raises(RimError, match="exclusivo de REHEARSAL"):
        rim_mod.build_rim("RUN", d, observed(d))
    assert rim_mod.build_rim("REHEARSAL", d, observed(d))["kind"] == "REHEARSAL"
    bad = declared(balance_mode="quase")
    with pytest.raises(RimError, match="balance_mode invalido"):
        rim_mod.build_rim("REHEARSAL", bad, observed(bad))


# ------------------------------------------------------------------------------------------------- CLI
def test_cli_evaluate_verify_results_and_exit_codes(tmp_path, capsys):
    run, world, result = completed_run(tmp_path)
    root = str(run.run_dir.parent)
    assert cli.main(["evaluate", "--run-id", run.run_id, "--runs-root", root]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["ok"] is True and first["run_valid"] is True
    assert cli.main(["evaluate", "--run-id", run.run_id, "--runs-root", root]) == 1                    # ja avaliado
    assert "ja foi avaliado" in json.loads(capsys.readouterr().out)["refused"]
    assert cli.main(["evaluate", "--run-id", run.run_id, "--runs-root", root, "--verify-only"]) == 0
    capsys.readouterr()
    assert cli.main(["verify-results", "--run-id", run.run_id, "--runs-root", root]) == 0
    (run.run_dir / "evaluation.json").write_bytes(b"{}")
    assert cli.main(["verify-results", "--run-id", run.run_id, "--runs-root", root]) == 1


def test_the_evaluate_path_imports_nothing_that_touches_the_lab():
    import ast
    from pathlib import Path

    for name in ("harness_side.py", "results.py"):
        tree = ast.parse((Path(cli.__file__).parent / name).read_text(encoding="utf-8"))
        found = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        found |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not [m for m in found if m.startswith(("sim.live", "sim.stack", "sim.runner.lab_ports", "sim.runner.live",
                                                      "sim.runner.nh_phases", "sim.runner.detach")) or m == "subprocess"], name
