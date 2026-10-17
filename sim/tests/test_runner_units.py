"""
SIM-1.5E -- unidades do plano de controle: autoridade (Tier 1/2, sem Oracle), adjudicacao offline, registro diario,
disco fail-closed, pacote selavel, claim, processo do host, declaracao do RIM e isolamento de imports.
"""

from __future__ import annotations

import ast
import gzip
import json
import re
from pathlib import Path

import pytest
import yaml

from sim.runner import authority
from sim.runner import claim as claim_mod
from sim.runner import daily
from sim.runner import declare
from sim.runner import disk
from sim.runner import harness_side
from sim.runner import host
from sim.runner import offline
from sim.runner import package
from sim.runner.declare import GIB
from sim.runner.errors import DiskGuardError
from sim.runner.errors import LockError
from sim.runner.errors import PackageError
from sim.runner.ledger import RunLedger
from sim.tests.collector_support import load_agenda_ops
from sim.tests.conftest import SIM_ROOT
from sim.tests.runner_support import good_day_facts
from sim.tests.runner_support import setup_run

RUNNER = SIM_ROOT / "runner"
POLICY = {"preflight_min_bytes": 15 * GIB, "warn_bytes": 10 * GIB, "abort_bytes": 6 * GIB, "reserve_bytes": 8 * GIB,
          "growth_factor_permille": 1500}


# ------------------------------------------------------------------------------------------------------- isolamento
def imports_of(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{alias.name}" for alias in node.names}
    return names


def test_only_the_host_side_harness_modules_read_the_oracle_and_none_uses_the_generator_beyond_canonical_json():
    readers = []
    for path in sorted(RUNNER.glob("*.py")):
        found = imports_of(path)
        if any(n == "sim.oracle" or n.startswith("sim.oracle.") for n in found):
            readers.append(path.name)
        generator = {n for n in found if n.startswith("sim.generator") and n != "sim.generator.canonical"
                     and not n.startswith("sim.generator.canonical.")}
        assert not generator, (path.name, generator)
    # lado do harness no host (mesma confianca de `sim.live`): le o Oracle para compilar o plano/pre-registro/status esperados;
    # nada disso entra em container (o plano e so roteamento) e os hashes sao proibidos na sonda DR-1
    assert readers == ["harness_side.py", "nh_phases.py"]


def test_the_authority_tripwire_and_derivations_take_no_oracle_truth_and_import_nothing_from_it():
    found = imports_of(RUNNER / "authority.py")
    assert not [n for n in found if n.startswith(("sim.oracle", "sim.generator", "sim.evaluator"))]
    assert authority.AuthorityTripwire().feed([]) == []                    # sem argumento de construcao
    import inspect

    assert list(inspect.signature(authority.AuthorityTripwire.__init__).parameters) == ["self"]
    assert list(inspect.signature(authority.AuthorityTripwire.feed).parameters) == ["self", "op_results"]
    text = (RUNNER / "authority.py").read_text(encoding="utf-8").lower()
    for word in ("expectation", "case_instances", "c-auth-2 =", "exp_id"):
        assert word not in text, word


def test_driver_and_collector_packages_still_import_nothing_from_the_runner_or_the_oracle():
    for package_dir in (SIM_ROOT / "driver", SIM_ROOT / "collector"):
        for path in package_dir.glob("*.py"):
            found = imports_of(path)
            assert not [n for n in found if n.startswith(("sim.runner", "sim.oracle", "sim.generator"))], path.name


def test_no_runner_module_changes_the_frozen_orchestrator_it_only_uses_its_public_api():
    text = "".join(p.read_text(encoding="utf-8") for p in RUNNER.glob("*.py"))
    assert "orchestrator.run(first_day=day, last_day=day)" in text
    assert "def run_day" not in text and "floor_activation()" not in text.replace("h.floor_activation()", "")


# ------------------------------------------------------------------------------------------------------- autoridade
def test_public_authority_constants_mirror_the_company_definition():
    company = yaml.safe_load((SIM_ROOT / "company" / "nova_horizonte" / "v2" / "company.yaml").read_text(encoding="utf-8"))
    for persona in company["personas"]:
        assert authority.PERSONA_AUTHORITY[persona["user"]] == frozenset(persona["authority"]), persona["user"]
    assert list(authority.CHAINS) == [company["authority_chains"]["standard"], company["authority_chains"]["reserve"]]


def res(op, actor, ref, status=200, seq=1, outcome="done"):
    return {"kind": "op_result", "seq": seq, "day": 1, "at": "10:00", "op": op, "actor": actor, "ref": ref,
            "http_status": status, "outcome": outcome}


def test_tripwire_accepts_both_public_chains_and_flags_each_success_outside_them():
    ok = authority.AuthorityTripwire()
    standard = [res("request_mark_paid", "sim-gerente-fin", "MP-1"), res("decide_mark_paid", "sim-coordenador-fin", "MP-1"),
                res("execute_mark_paid", "sim-gerente-fin", "MP-1")]
    reserve = [res("materialize_mark_overdue", "sim-coordenador-fin", "MO-1"), res("decide_mark_overdue", "sim-gerente-fin", "MO-1"),
               res("execute_mark_overdue", "sim-coordenador-fin", "MO-1")]
    assert ok.feed(standard + reserve) == []

    def kinds(*lines):
        return [v["violation"] for v in authority.AuthorityTripwire().feed(list(lines))]

    assert kinds(res("record_assessment", "sim-gerente-fin", None)) == [authority.V_UNAUTHORIZED]
    assert kinds(res("decide_mark_paid", "sim-cobranca-1", "MP-2")) == [authority.V_UNAUTHORIZED]
    assert authority.V_SEGREGATION in kinds(res("request_mark_paid", "sim-gerente-fin", "MP-3"),
                                            res("decide_mark_paid", "sim-gerente-fin", "MP-3"))
    assert authority.V_SEGREGATION in kinds(res("request_mark_paid", "sim-gerente-fin", "MP-4"),
                                            res("decide_mark_paid", "sim-coordenador-fin", "MP-4"),
                                            res("execute_mark_paid", "sim-coordenador-fin", "MP-4"))
    assert authority.V_CHAIN in kinds(res("request_mark_paid", "sim-coordenador-fin", "MP-5"),
                                      res("decide_mark_paid", "sim-gerente-fin", "MP-5"),
                                      res("execute_mark_paid", "sim-gerente-fin", "MP-5"))


def test_tripwire_ignores_rejections_because_expected_rejections_are_only_known_to_the_offline_adjudication():
    lines = [res("decide_mark_paid", "sim-gerente-fin", "MP-3", status=403, outcome="done"),
             res("execute_mark_paid", "sim-cobranca-1", "MP-3", status=409, outcome="reconciled")]
    assert authority.AuthorityTripwire().feed(lines) == []


def test_on_the_real_agenda_only_the_attempt_ops_would_be_accused_if_they_had_succeeded():
    flagged = authority.public_agenda_check(load_agenda_ops())
    assert flagged and {v["violation"] for v in flagged} <= {authority.V_SEGREGATION, authority.V_CHAIN}
    assert not [v for v in flagged if v["violation"] == authority.V_UNAUTHORIZED]
    assert len(flagged) < 20                                  # sao as poucas tentativas da agenda, nao o desenho legitimo


def test_tier_one_derivations_are_factual_and_keep_every_non_2xx():
    lines = [{"kind": "http", "n": 1, "seq": 5, "day": 1, "at": "10:00", "op": "decide_mark_paid", "actor": "a", "method": "POST",
              "path": "/x", "status": 403, "attempt": 1},
             {"kind": "http", "n": 2, "seq": 6, "day": 1, "at": "10:00", "op": "o", "actor": "a", "method": "GET", "path": "/y",
              "status": 200, "attempt": 1},
             {"kind": "http", "n": 3, "seq": 7, "day": 1, "at": "10:00", "op": "o", "actor": "a", "method": "GET", "path": "/y",
              "status": None, "attempt": 1},
             res("decide_mark_paid", "sim-gerente-fin", "MP-9", status=403)]
    assert [r["seq"] for r in authority.derive_status_ledger(lines)] == [5, 7]
    assert authority.derive_authority_chain(lines)[0]["http_status"] == 403


def test_offline_adjudication_recovers_protocol_noise_matches_expected_and_flags_the_rest():
    chain = [{"seq": 1, "http_status": 200}, {"seq": 2, "http_status": 403}, {"seq": 3, "http_status": 404}, {"seq": 4, "http_status": 200}]
    ledger = [{"seq": 1, "status": 401, "day": 1, "at": "10:00"},                 # relogin -> recuperado
              {"seq": 4, "status": 503, "day": 1, "at": "10:00"},                 # retry -> recuperado
              {"seq": 2, "status": 403, "day": 2, "at": "11:00"},                 # esperado (C-AUTH-2)
              {"seq": 3, "status": 404, "day": 3, "at": "12:00"}]                 # inexplicado
    report = offline.adjudicate_status(ledger, chain, [{"day": 2, "at": "11:00", "statuses": [403, 409]}])
    assert (report["recovered"], report["expected_matched"], report["ok"]) == (2, 1, False)
    assert [r["seq"] for r in report["unexplained"]] == [3] and report["expected_unmatched"] == []
    nothing = offline.adjudicate_status([], chain, [{"day": 2, "at": "11:00", "statuses": [409]}])
    assert nothing["ok"] and nothing["expected_unmatched"] == [[2, "11:00", [409]]]       # esperado que nao ocorreu: nao e anomalia aqui
    assert not offline.adjudicate_status([{"seq": 9, "status": 401, "day": 1, "at": "1"}], [{"seq": 9, "http_status": 401}], [])["ok"]


# ------------------------------------------------------------------------------------------------------- registro diario
def test_daily_judge_accepts_good_facts_and_reports_every_class_of_failure_together():
    assert daily.judge_day(good_day_facts(), disk_policy=POLICY, clock_skew_limit_ms=3000, o1_limit_ms=1000) == ([], [], [])
    facts = good_day_facts(time__o1_ms=2000, disk__free_bytes=5 * GIB, infra__topology_ok=False)
    violations, _, _ = daily.judge_day(facts, disk_policy=POLICY, clock_skew_limit_ms=3000, o1_limit_ms=1000)
    assert {v["class"] for v in violations} == {"O1_THRESHOLD_REVIEW_REQUIRED", "DISK_LOW", "TOPOLOGY_VIOLATION"}


def test_daily_judge_treats_a_barrier_without_evidence_as_a_failure():
    facts = good_day_facts(time__barrier_total_ms=None, time__barrier_events_ok=None)
    violations, _, _ = daily.judge_day(facts, disk_policy=POLICY, clock_skew_limit_ms=3000, o1_limit_ms=1000)
    assert [v["class"] for v in violations] == ["BARRIER_FAILURE"]


# ------------------------------------------------------------------------------------------------------- disco
def test_disk_measurement_failure_is_fail_closed():
    def broken(path):
        raise OSError("sem acesso")

    with pytest.raises(DiskGuardError, match="falha ao medir"):
        disk.measure_free(".", usage=broken)
    with pytest.raises(DiskGuardError, match="invalida"):
        disk.measure_free(".", usage=lambda p: type("U", (), {"free": -1})())
    assert disk.measure_free(".", usage=lambda p: type("U", (), {"free": 7})()) == 7


@pytest.mark.parametrize("free,level", [(50 * GIB, "OK"), (9 * GIB, "WARN"), (5 * GIB, "ABORT"), (None, "ABORT"), (-1, "ABORT"),
                                        (True, "ABORT")])
def test_disk_day_levels(free, level):
    assert disk.judge_day(free, POLICY).level == level


# ------------------------------------------------------------------------------------------------------- pacote e claim
def build_run_dir(tmp_path):
    run = setup_run(tmp_path)
    (run.run_dir / "days").mkdir()
    (run.run_dir / "days" / "D000.json").write_text("{}", encoding="utf-8")
    (run.run_dir / "driver").mkdir()
    (run.run_dir / "driver" / "driver_evidence.jsonl").write_text("a\nb\n", encoding="utf-8")
    return run


def test_package_seal_verifies_and_every_tamper_is_detected(tmp_path):
    run = build_run_dir(tmp_path)
    run.ledger.append("RUN_FINALIZING", {}) if False else None
    digest = package.seal(run.run_dir, run_id=run.run_id, ledger_tail=run.ledger.tail)
    assert package.verify(run.run_dir, expected_digest=digest) == {"ok": True, "problems": [], "digest": digest}
    with pytest.raises(PackageError, match="ja selado"):
        package.seal(run.run_dir, run_id=run.run_id, ledger_tail=run.ledger.tail)
    target = run.run_dir / "driver" / "driver_evidence.jsonl"
    original = target.read_bytes()
    target.write_bytes(b"a\nX\n")                                              # 1 byte alterado
    assert any("alterado" in p for p in package.verify(run.run_dir)["problems"])
    target.write_bytes(original)
    (run.run_dir / "extra.txt").write_text("novo", encoding="utf-8")
    assert any("nao listado" in p for p in package.verify(run.run_dir)["problems"])
    (run.run_dir / "extra.txt").unlink()
    target.unlink()
    assert any("ausente" in p for p in package.verify(run.run_dir)["problems"])
    target.write_bytes(original)
    assert package.verify(run.run_dir)["ok"]
    (run.run_dir / package.DIGEST_NAME).chmod(0o666)
    (run.run_dir / package.DIGEST_NAME).write_text("0" * 64 + "\n", encoding="utf-8")
    assert any("difere do digest" in p for p in package.verify(run.run_dir)["problems"])
    assert any("registro do host" in p for p in package.verify(run.run_dir, expected_digest="1" * 64)["problems"])


def test_package_requires_the_identity_files_unless_it_is_a_partial_package(tmp_path):
    empty = tmp_path / "run"
    empty.mkdir()
    with pytest.raises(PackageError, match="obrigatorios"):
        package.seal(empty, run_id="RUN-x", ledger_tail={"seq": 0, "hash": "0" * 64})
    package.seal(empty, run_id="RUN-x", ledger_tail={"seq": 0, "hash": "0" * 64}, partial=True)


def test_package_detects_a_ledger_that_no_longer_contains_the_tail_recorded_at_seal(tmp_path):
    run = build_run_dir(tmp_path)
    package.seal(run.run_dir, run_id=run.run_id, ledger_tail=run.ledger.tail)
    ledger_file = run.run_dir / "ledger.jsonl"
    ledger_file.write_bytes(ledger_file.read_bytes().split(b"\n")[0] + b"\n")          # volta a um prefixo valido
    assert any("cauda" in p for p in package.verify(run.run_dir)["problems"])
    assert RunLedger  # noqa: B018


def test_package_digest_is_the_sha256_of_the_sorted_path_listing(tmp_path):
    run = build_run_dir(tmp_path)
    entries = package.list_entries(run.run_dir)
    text = "".join(f"{e['sha256']} *{e['path']}\n" for e in sorted(entries, key=lambda e: e["path"]))
    import hashlib

    assert package.digest_of(entries) == hashlib.sha256(text.encode()).hexdigest()
    assert {e["path"] for e in entries} >= {"rim.json", "run_id.json", "days/D000.json"}
    assert "ledger.jsonl" not in {e["path"] for e in entries}


def test_claims_are_derived_from_state_and_never_contain_the_verification_phrase():
    cases = []
    for kind in ("RUN", "REHEARSAL"):
        for state in (None, "CREATED", "RUNNING", "ABORTED", "INVALID", "COMPLETED"):
            for evaluated in (False, True):
                for post in (False, True):
                    cases.append(claim_mod.claim_for(kind=kind, state=state, run_id="RUN-x-A01", oracle_version="v2.2",
                                                     evaluated=evaluated, invalidated_post_hoc=post))
    assert all(claim_mod.FORBIDDEN_PHRASE not in c["text"] for c in cases)
    by = lambda **k: claim_mod.claim_for(kind="RUN", run_id="RUN-x-A01", oracle_version="v2.2", **k)["level"]    # noqa: E731
    assert by(state="INVALID", evaluated=True) == "NO_CLAIM" and by(state="ABORTED", evaluated=False) == "NO_CLAIM"
    assert by(state="COMPLETED", evaluated=False) == "COMPLETED_NOT_EVALUATED"
    assert by(state="COMPLETED", evaluated=True) == "SIMULATION_RESULT"
    assert by(state="COMPLETED", evaluated=True, invalidated_post_hoc=True) == "NO_CLAIM"
    assert claim_mod.claim_for(kind="REHEARSAL", state="COMPLETED", run_id="r", oracle_version="v", evaluated=True)["level"] == "TECHNICAL_REHEARSAL"
    old = json.loads((SIM_ROOT / "scenarios" / "nh-small-v2-s340001" / "oracle.json").read_text(encoding="utf-8"))["claim"]
    assert "SIMULATION VERIFIED" in old and all(c["text"] != old for c in cases)         # o texto historico nao e herdado


# ------------------------------------------------------------------------------------------------------- host
def test_run_lock_is_exclusive_and_an_orphan_lock_is_never_taken_over(tmp_path):
    first = host.RunLock(tmp_path, pid=lambda: 11, alive=lambda pid: True)
    first.acquire()
    other = host.RunLock(tmp_path, pid=lambda: 22, alive=lambda pid: True)
    with pytest.raises(LockError, match="outro runner vivo"):
        other.acquire()
    first.release()
    first.acquire()
    orphan = host.RunLock(tmp_path, pid=lambda: 33, alive=lambda pid: False)
    assert orphan.orphan() and not other.orphan()
    with pytest.raises(LockError, match="orfao"):
        orphan.acquire()
    first.release()
    assert not (tmp_path / host.LOCK_NAME).exists()


def test_keep_awake_is_fail_closed_and_stop_flag_only_requests():
    assert host.keep_awake(set_state=lambda flags: 0x80000000) is True
    assert host.keep_awake(set_state=lambda flags: 0) is False

    def boom(flags):
        raise OSError("sem api")

    assert host.keep_awake(set_state=boom) is False
    flag = host.StopFlag()
    assert flag.requested is False
    flag.request("x")
    assert (flag.requested, flag.reason) == (True, "x")


# ------------------------------------------------------------------------------------------------------- harness side / declare
def test_preregistration_and_expected_statuses_come_from_the_real_v22_oracle():
    oracle = json.loads((SIM_ROOT / "oracle" / "artifacts" / "nh-small-v2-s340001-oracle-v2.2" / "oracle.json").read_text(encoding="utf-8"))
    prereg = harness_side.preregistration(oracle)
    assert prereg["expectations"] == 13074 and prereg["oracle_version"] == "v2.2" and len(prereg["retimed_expected"]) == 5
    assert prereg["known_limit_expectations"] >= 28 and prereg["finding_expectations"] > 0
    assert sum(prereg["by_semantics"].values()) == 13074 and prereg["not_collected"] == 28
    assert prereg["expected_findings"].get("F-CALENDAR", 0) > 0
    statuses = harness_side.expected_statuses(oracle)
    assert len([s for s in statuses if s["statuses"] == [403, 409]]) == 3 and len(statuses) == 7
    assert harness_side.preregistration(oracle) == prereg                      # deterministico
    assert re.fullmatch(r"[0-9a-f]{64}", harness_side.authority_invariants_sha256())


def test_verify_authority_offline_writes_the_report_and_turns_findings_into_classes(tmp_path):
    (tmp_path / "authority").mkdir()
    write = lambda name, rows: (tmp_path / "authority" / name).write_text("".join(json.dumps(r) + "\n" for r in rows))   # noqa: E731
    write("status_ledger.jsonl", [{"seq": 3, "status": 404, "day": 3, "at": "12:00"}])
    write("authority_chain.jsonl", [{"seq": 3, "http_status": 404}])
    write("tripwire.jsonl", [{"violation": "SEGREGATION_BREACH"}])
    verdict = harness_side.verify_authority_offline(tmp_path, {"expectations": []})
    assert {v["class"] for v in verdict["violations"]} == {"UNEXPECTED_STATUS_UNEXPLAINED", "AUTHORITY_OUTSIDE_C_AUTH_2"}
    assert (tmp_path / "authority" / "verify_authority.json").exists()


def test_declared_rim_fields_are_read_from_the_frozen_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(declare, "git_out", lambda repo, *a: "c" * 40 if a[0] == "rev-parse" and "^" not in a[-1] and ":" not in a[-1] else "t" + a[-1])
    base = tmp_path / "base.yml"
    base.write_text("a", encoding="utf-8")
    over = tmp_path / "over.yml"
    over.write_text("b", encoding="utf-8")
    d = declare.build_declared(
        repo=tmp_path, scenario_dir=SIM_ROOT / "scenarios" / "nh-small-v2-s340001",
        oracle_dir=SIM_ROOT / "oracle" / "artifacts" / "nh-small-v2-s340001-oracle-v2.2", scenario_id="nh-small-v2-s340001",
        variant="nh-small", seed=340001, oracle_version="v2.2", generator_version="sim-1.5.1",
        base_image_digest="python@sha256:" + "b" * 64, lab_image_ids={"base": "i", "backend": "i", "postgres": "i"},
        compose_files={"base": base, "override": over}, quiescence_sha256="a" * 64, driver_params_sha256="b" * 64,
        plan_sha256="c" * 64, preregistration_sha256="d" * 64, authority_invariants_sha256="e" * 64, growth_model_sha256=None)
    assert d["derived_from_oracle_sha256"] == "88ff8313aed8c4ddb8f1bfdf633b34c5955bbf3db78b170d7cd59933694358bb"
    assert d["agenda_sha256"].startswith("b1c855c1") and d["world_sha256"].startswith("acfadd61")
    assert d["v2_manifest_sha256"].startswith("a394fe5e") and d["v2_oracle_sha256"].startswith("34720168")
    assert d["timeline"] == declare.NH_TIMELINE and d["recovery_policy"] == "R0"
    assert gzip.compress(b"x", mtime=0) == gzip.compress(b"x", mtime=0)


# ------------------------------------------------------------------------------------------------------- fronteira do 1.5E
BASE_COMMIT = "248b2fe62781103df449e7347f81dd44871365b9"
UNTOUCHED = ("sim/driver", "sim/collector", "sim/stack", "sim/live", "sim/scenarios", "sim/company", "sim/generator",
             "sim/oracle/v2_1.py", "sim/oracle/expectations.py", "sim/oracle/expectations_v2.py", "sim/oracle/rules.py",
             "sim/oracle/rules_v2.py", "sim/oracle/schema.py", "sim/oracle/collection_plan.py",
             "sim/oracle/artifacts/nh-small-v2-s340001-oracle-v2.1")


def test_the_1_5e_candidate_leaves_driver_collector_stack_live_generator_v21_oracle_backend_and_frontend_untouched():
    import shutil

    from sim.tests import freeze_support as fs

    if shutil.which("git") is None:
        pytest.skip("git indisponivel")
    repo = SIM_ROOT.parent
    if not fs.require_commit(repo, BASE_COMMIT):
        pytest.skip("commit base do 1.5E ausente (clone raso)")
    # componentes do SIM: igualdade exata com a base (arvore de trabalho e indice)
    assert fs.frozen_component_diff(repo, BASE_COMMIT, UNTOUCHED) == []
    # SIM-FREEZE-IMPL-01: backend/frontend nao sao mais comparados por igualdade absoluta; prova-se que a base tem as
    # arvores congeladas e que nenhum commit do SIM, desde a base, altera o produto (commits mistos, merges, renames).
    assert fs.provenance_violations(repo, {"base_1_5e": BASE_COMMIT}) == []
    assert [str(v) for v in fs.mixed_commit_violations(repo, BASE_COMMIT)] == []


def test_floor_and_restart_are_the_versioned_mechanisms_and_the_runner_adds_no_second_implementation():
    text = {p.name: p.read_text(encoding="utf-8") for p in RUNNER.glob("*.py")}
    assert "flow.build_hooks(lab, floor=True)" in text["nh_phases.py"]
    assert "from sim.stack.lab.run_orchestrator import RunOrchestrator" in text["runner.py"]
    for name, source in text.items():
        for forbidden in ("activate_floor", "restart_backend_checked", "restart_barrier_checked", "docker restart",
                          "evidence_floor_preflight", "--force-recreate"):
            assert forbidden not in source, (name, forbidden)
