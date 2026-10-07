"""
SIM-1.5A: gates ESTATICOS do harness live versionado (`sim/live/**`), sem Docker.

Cada defeito achado no PRE-LIVE vira teste de regressao: topologia vacuamente aprovada, instrumento cego a
conexoes em pool (sem controle positivo), criterio de nao rastreados por igualdade exata.
"""

from __future__ import annotations

import ast
import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sim.collector.collect import missing_items
from sim.collector.collect import verify_records
from sim.driver.agenda import load_agenda
from sim.driver.agenda import load_manifest
from sim.evaluator.evaluate import evaluate
from sim.live import env as E
from sim.live import flow
from sim.live import gates as G
from sim.live import judges as J
from sim.live import scenarios as S
from sim.live.fingerprint import FINGERPRINT_SQL
from sim.live.fingerprint import delta
from sim.live.fingerprint import fingerprint
from sim.oracle.collection_plan import compile_plan
from sim.stack.lab import driver_lab
from sim.stack.lab import sqlro
from sim.stack.lab.config import REPO_ROOT
from sim.stack.lab.config import load_config
from sim.stack.lab.guard import GuardViolation
from sim.stack.lab.quiescence import HarnessError
from sim.stack.lab.runner import Result
from sim.tests.collector_support import make_collector
from sim.tests.collector_support import orchestrate
from sim.tests.driver_support import make_driver
from sim.tests.fake_product import FakeProduct
from sim.tests.test_lab_driver import merged_resolved

CONFIG = load_config()
LIVE = REPO_ROOT / "sim" / "live"
OBSERVER_EMAIL = CONFIG.email(E.OBSERVER)


# ----------------------------------------------------------------------------------------- higiene do pacote
def live_sources() -> list:
    return sorted(LIVE.rglob("*.py"))


def test_live_package_is_lf_ascii_safe_and_never_touches_dev_or_product_code():
    assert len(live_sources()) >= 12
    for path in live_sources():
        raw = path.read_bytes()
        assert b"\r\n" not in raw, path
        text = raw.decode("utf-8")
        for dev in ("auneron-backend", "auneron-postgres", "auneron-frontend", "auneron_test", "localhost:8000",
                    "shell=True"):
            assert dev not in text, (path, dev)
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                assert not any(n == "app" or n.startswith(("app.", "backend")) for n in names), path


def test_live_files_have_no_trailing_whitespace_and_end_with_one_newline():
    for path in [*live_sources(), *LIVE.rglob("*.md")]:
        text = path.read_bytes().decode("utf-8")
        assert text.endswith("\n") and not text.endswith("\n\n"), path
        assert [i for i, line in enumerate(text.split("\n"), 1) if line != line.rstrip(" \t")] == [], path


def test_every_docker_target_goes_through_the_lab_prefix_guard():
    """Containers sao sempre as constantes `auneron-sim-*`; `dexec`/`cp_in` passam por `require_container`."""
    for constant in (E.DRIVER, E.COLLECTOR, E.BACKEND, E.POSTGRES):
        assert constant.startswith(CONFIG["docker"]["required_prefix_container"])
    runner = FakeRunner()
    lab = SimpleNamespace(runner=runner, config=CONFIG)
    with pytest.raises(GuardViolation):
        E.dexec(lab, "auneron-backend", "ls")
    with pytest.raises(GuardViolation):
        E.cp_in(lab, "auneron-postgres", Path("x"), "/x")
    assert runner.calls == []


def test_container_scripts_compile_and_import_only_stdlib_and_the_two_isolated_packages():
    allowed_roots = {"json", "os", "sys", "re", "time", "hashlib", "datetime", "urllib", "sim"}
    scripts = sorted((LIVE / "container").glob("*.py"))
    assert {p.name for p in scripts} == {"refusal.py", "reader.py", "dr5_cases.py", "dr8_crash.py"}
    for path in scripts:
        source = path.read_text(encoding="utf-8").replace("__DAY__", "9")
        compile(source, str(path), "exec")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert {a.name.split(".")[0] for a in node.names} <= allowed_roots, path
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root in allowed_roots, path
                if root == "sim":
                    assert (node.module or "").startswith(("sim.driver", "sim.collector")), (path, node.module)


def test_container_scripts_never_reference_oracle_or_world_artifacts():
    for path in (LIVE / "container").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in ("oracle.json", "world.json", "sim.oracle", "sim.generator", "sim.evaluator"):
            assert token not in text, (path, token)


def test_refusal_script_runs_offline_and_every_attempt_is_refused():
    out = subprocess.run([sys.executable, "-"], input=(LIVE / "container" / "refusal.py").read_text(encoding="utf-8"),
                         capture_output=True, text=True, cwd=REPO_ROOT, env={"PYTHONPATH": str(REPO_ROOT),
                                                                            "PATH": "", "SYSTEMROOT": "C:\\Windows"}
                         if sys.platform == "win32" else {"PYTHONPATH": str(REPO_ROOT)})
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert len(result["cases"]) == 15 and result["network_calls"] == 0
    assert J.judge_refusal(result) == []
    # negativos: cada tipo de falha e detectado
    for mutate in (lambda r: r["cases"].__setitem__("POST /accounts/", "NOT_REFUSED"),
                   lambda r: r.__setitem__("wrong_host", "NOT_REFUSED"),
                   lambda r: r.__setitem__("network_calls", 1),
                   lambda r: r.__setitem__("mutating_attrs", ["post"]),
                   lambda r: r.__setitem__("cases", {})):
        broken = copy.deepcopy(result)
        mutate(broken)
        assert J.judge_refusal(broken)


def test_dr5_script_substitutes_exactly_one_day_placeholder_and_dr8_patterns_match_only_the_target_calls():
    text = (LIVE / "container" / "dr5_cases.py").read_text(encoding="utf-8")
    assert text.count("__DAY__") == 1
    create, decision = re.compile(G.CRASH_AFTER_CREATE), re.compile(G.CRASH_AFTER_DECISION)
    assert create.search("POST http://sim-backend:8000/accounts/")
    for other in ("POST http://sim-backend:8000/accounts/12", "GET http://sim-backend:8000/accounts/",
                  "POST http://sim-backend:8000/accounts/12/execute-mark-paid", "POST http://sim-backend:8000/auth/login"):
        assert not create.search(other), other
    assert decision.search("POST http://sim-backend:8000/approvals/15/decision")
    for other in ("POST http://sim-backend:8000/approvals/skill-executions/2", "GET http://sim-backend:8000/approvals/15",
                  "POST http://sim-backend:8000/approvals/15/decision/x"):
        assert not decision.search(other), other


# ----------------------------------------------------------------------------------------- juizes (regressao do PRE-LIVE)
def good_inspect(volumes=("auneron_sim_driver_state", "auneron_sim_driver_inputs")) -> dict:
    return {"HostConfig": {"Binds": [f"{v}:/x:rw" for v in volumes], "NetworkMode": "auneron_sim_driver",
                           "Privileged": False, "CapAdd": None, "PidMode": ""},
            "NetworkSettings": {"Networks": {"auneron_sim_driver": {}}, "Ports": {}},
            "Mounts": [{"Type": "volume", "Name": v} for v in volumes], "Config": {"User": "simdrv"}}


VOLUMES = {"auneron_sim_driver_state", "auneron_sim_driver_inputs"}


def test_topology_judge_accepts_the_real_shape_and_refuses_each_violation():
    assert J.judge_topology(good_inspect(), VOLUMES) == []
    cases = {
        "extra network": lambda i: i["NetworkSettings"]["Networks"].__setitem__("auneron_sim_internal", {}),
        "wrong network": lambda i: i["NetworkSettings"].__setitem__("Networks", {"bridge": {}}),
        "bind mount": lambda i: (i["HostConfig"]["Binds"].append("C:\\data:/x"), i["Mounts"].append({"Type": "bind", "Name": None})),
        "posix bind": lambda i: i["HostConfig"]["Binds"].append("/var/run/docker.sock:/var/run/docker.sock"),
        "host network": lambda i: i["HostConfig"].__setitem__("NetworkMode", "host"),
        "privileged": lambda i: i["HostConfig"].__setitem__("Privileged", True),
        "published port": lambda i: i["NetworkSettings"].__setitem__("Ports", {"80/tcp": [{"HostPort": "80"}]}),
        "cap_add": lambda i: i["HostConfig"].__setitem__("CapAdd", ["NET_ADMIN"]),
        "pid host": lambda i: i["HostConfig"].__setitem__("PidMode", "host"),
        "root user": lambda i: i["Config"].__setitem__("User", ""),
        "no mounts": lambda i: i.__setitem__("Mounts", []),
        "foreign volume": lambda i: i["Mounts"].__setitem__(0, {"Type": "volume", "Name": "dev_pgdata"}),
    }
    for label, mutate in cases.items():
        broken = copy.deepcopy(good_inspect())
        mutate(broken)
        assert J.judge_topology(broken, VOLUMES), label


def test_topology_judge_is_not_vacuous_when_there_are_no_published_ports():
    """Regressao do PRE-LIVE: a expressao antiga passava sempre que `Ports == {}`."""
    broken = good_inspect()
    broken["HostConfig"]["Binds"] = ["/host/path:/state"]
    broken["Mounts"] = [{"Type": "bind", "Name": None}]
    assert broken["NetworkSettings"]["Ports"] == {}
    assert J.judge_topology(broken, VOLUMES)


def test_named_volume_bind_detection():
    assert J.is_named_volume_bind("auneron_sim_driver_state:/state:rw")
    for bind in ("/var/run/docker.sock:/var/run/docker.sock", "C:\\Users\\x:/state", "C:/Users/x:/state", "./x:/state",
                 "~/x:/state", "nocolon", ":/state"):
        assert not J.is_named_volume_bind(bind), bind


def test_probe_controls_must_all_fire_or_a_clean_pass_proves_nothing():
    assert J.judge_probe_controls({"name_oracle_detected": True, "env_detected": True, "hash_copy_detected": True}) == []
    for missing in ("name_oracle_detected", "env_detected", "hash_copy_detected"):
        controls = {"name_oracle_detected": True, "env_detected": True, "hash_copy_detected": True, missing: False}
        assert J.judge_probe_controls(controls)
    assert J.judge_inputs(["a", "b"], {"a", "b"}) == [] and J.judge_inputs(["a", "plan.json"], {"a", "b"})


def test_purity_judge_separates_worker_noise_from_reads_that_write():
    kw = dict(identical=True, all_200=True, requests=160, tables=33)
    assert J.judge_purity(baseline={}, read_delta={}, **kw) == []
    assert J.judge_purity(baseline={"knowledge": 1}, read_delta={"knowledge": 1}, **kw) == []          # ruido do worker
    assert J.judge_purity(baseline={}, read_delta={"approval_requests": {}}, **kw)                      # leitura que escreve
    assert J.judge_purity(baseline={}, read_delta={}, **{**kw, "identical": False})
    assert J.judge_purity(baseline={}, read_delta={}, **{**kw, "all_200": False})
    assert J.judge_purity(baseline={}, read_delta={}, **{**kw, "requests": 10})
    assert J.judge_purity(baseline={}, read_delta={}, **{**kw, "tables": 3})            # instrumento quase cego


def test_positive_control_fails_when_the_instrument_is_blind():
    """Regressao do PRE-LIVE: o pg_stat NAO via a escrita do NBA; um delta vazio no controle positivo e FALHA."""
    seen = {"nba_recommendation_snapshots": {"count": [4, 5], "content_changed": True}}
    assert J.judge_positive_control(200, seen) == []
    assert J.judge_positive_control(200, {})                                                           # cego
    assert J.judge_positive_control(200, {"nba_recommendation_snapshots": {"count": [4, 4]}})
    assert J.judge_positive_control(500, seen)


class FpRunner:
    """Runner falso que ecoa as linhas do `psql -F '|'` e guarda o script enviado."""

    def __init__(self, rows):
        self.rows, self.scripts = rows, []

    def run(self, args, **kwargs):
        self.scripts.append(args[-1])
        return Result(0, "BEGIN\n" + "\n".join("|".join(map(str, r)) for r in self.rows) + "\nROLLBACK\n", "")


def test_fingerprint_is_read_only_passes_the_lab_sql_guard_and_parses_rows():
    assert not sqlro._WRITE.search(FINGERPRINT_SQL)
    runner = FpRunner([("accounts", 3, "abc"), ("work_items", 0, "def")])
    assert fingerprint(runner, CONFIG) == {"accounts": [3, "abc"], "work_items": [0, "def"]}
    script = runner.scripts[0]
    assert script.startswith("BEGIN TRANSACTION READ ONLY;") and script.endswith("ROLLBACK;")
    assert "pg_stat" not in FINGERPRINT_SQL


def test_fingerprint_delta_sees_content_changes_that_keep_the_row_count():
    before = {"a": [1, "x"], "b": [2, "y"], "c": [0, "z"]}
    after = {"a": [1, "x"], "b": [2, "CHANGED"], "c": [1, "w"], "d": [1, "n"]}
    assert delta(before, after) == {"b": {"count": [2, 2], "content_changed": True},
                                    "c": {"count": [0, 1], "content_changed": True},
                                    "d": {"count": [None, 1], "content_changed": True}}
    assert delta(before, before) == {}


def test_untracked_classifier_handles_expanded_historical_directories():
    """Regressao do PRE-LIVE: `--untracked-files=all` expande as pastas historicas em arquivos."""
    historical = list(G.HISTORICAL)
    paths = [".claude/launch.json", "Claude outputs/a.md", "Claude outputs/sub/b.txt", "backend/DW3_patch_review_v2.patch",
             "backend/scripts/reset_test_password.py"]
    assert J.classify_untracked(paths, historical) == []
    assert J.classify_untracked([*paths, "sim/live/new.py", "backend/scripts/other.py", "Claude outputs"], historical) == [
        "Claude outputs", "backend/scripts/other.py", "sim/live/new.py"]
    assert J.classify_untracked(["backend/DW3_patch_review_v2.patch.bak"], historical)      # nao e prefixo de arquivo


def good_teardown() -> dict:
    images = ["a:1", "b:1"]
    return {"containers": [], "volumes": [], "networks": [], "containers_from_lab_images": {"a:1": []}, "guardprobe_leftover": False,
            "images": images, "expected_images": images, "head": "h", "expected_head": "h", "staged_count": 5,
            "expected_staged_count": 5, "tracked_diff": [], "bytes_mismatch": [], "digest": "d", "expected_digest": "d",
            "extra_untracked": [], "sim14_hash_mismatches": [], "dev_snapshot": "PASS", "secrets_outside_repo": True}


def test_teardown_judge_accepts_a_clean_state_and_refuses_each_residue():
    assert J.judge_teardown(good_teardown()) == []
    breaks = {
        "container": lambda d: d.__setitem__("containers", ["auneron-sim-x"]),
        "volume": lambda d: d.__setitem__("volumes", ["auneron_sim_x"]),
        "network": lambda d: d.__setitem__("networks", ["auneron_sim_x"]),
        "ancestor": lambda d: d["containers_from_lab_images"].__setitem__("a:1", ["leftover"]),
        "guardprobe": lambda d: d.__setitem__("guardprobe_leftover", True),
        "image missing": lambda d: d.__setitem__("images", ["a:1"]),
        "head": lambda d: d.__setitem__("head", "other"),
        "staged": lambda d: d.__setitem__("staged_count", 6),
        "tracked": lambda d: d.__setitem__("tracked_diff", ["backend/x.py"]),
        "bytes": lambda d: d.__setitem__("bytes_mismatch", ["sim/x.py"]),
        "digest": lambda d: d.__setitem__("digest", "other"),
        "untracked": lambda d: d.__setitem__("extra_untracked", ["sim/new.py"]),
        "sim14": lambda d: d.__setitem__("sim14_hash_mismatches", ["sim/stack/lab/gates.py"]),
        "dev": lambda d: d.__setitem__("dev_snapshot", "FAIL"),
        "secrets": lambda d: d.__setitem__("secrets_outside_repo", False),
    }
    for label, mutate in breaks.items():
        data = copy.deepcopy(good_teardown())
        mutate(data)
        assert J.judge_teardown(data), label


def test_dr_judges_refuse_the_failure_each_one_exists_to_catch():
    # DR-3
    out = {"rerun_without_known_id": "REFUSED: x", "rerun_with_manifest_id": {"version_id": 2},
           "positive": {"http_status": 201, "skill_version_id": 2, "skill_key": "account.mark_paid"},
           "negative": {"fail_closed": True}}
    assert J.judge_dr3(out, 2) == []
    for mutate in (lambda o: o.__setitem__("rerun_without_known_id", "NOT_REFUSED"),
                   lambda o: o["rerun_with_manifest_id"].__setitem__("version_id", 9),
                   lambda o: o["positive"].__setitem__("skill_version_id", 1),
                   lambda o: o["positive"].__setitem__("skill_key", "account.mark_overdue"),
                   lambda o: o["negative"].__setitem__("fail_closed", False)):
        broken = copy.deepcopy(out)
        mutate(broken)
        assert J.judge_dr3(broken, 2)
    # DR-4
    steps = {"1_initial": {"code": 0}, "2_proactive": {"code": 0, "relogged": True, "http_statuses_seq2": [200]},
             "3_401_relogin": {"code": 0, "relogged": True, "http_statuses_seq3": [401, 200]},
             "4_expired": {"code": 0, "relogged": True, "http_statuses_seq4": [200]}}
    assert J.judge_dr4(steps) == []
    for key, field, value in (("2_proactive", "http_statuses_seq2", [401, 200]), ("2_proactive", "relogged", False),
                              ("3_401_relogin", "http_statuses_seq3", [200]), ("4_expired", "relogged", False),
                              ("1_initial", "code", 3)):
        broken = copy.deepcopy(steps)
        broken[key][field] = value
        assert J.judge_dr4(broken), (key, field)


def dr5_results() -> dict:
    def read(n, cands):
        return {"kind": "reconcile_read", "read": n, "candidates": cands}
    return {
        0: {"result": "completed", "accounts_after": 1, "seconds": 10.1,
            "evidence": [read(1, []), read(2, []), read(3, []), {"kind": "op_result", "resent_after_stable_absence": True}]},
        1: {"result": "completed", "accounts_after": 1, "evidence": [read(1, [7]), read(2, [7]), read(3, [7]),
                                                                       {"kind": "op_result", "outcome": "reconciled"}]},
        2: {"result": "HARNESS_ERROR", "error": "reconciliacao ambigua: 2 candidatos", "accounts_after": 2, "op_status": "failed",
            "evidence": []},
        3: {"result": "HARNESS_ERROR", "error": "reconciliacao instavel: leituras divergentes entre si", "accounts_after": 1,
            "op_status": "failed", "evidence": []}}


def test_dr5_judge_requires_the_exact_reconciliation_behaviour_per_case():
    assert all(J.judge_dr5(dr5_results()).values())
    for case, mutate in ((0, lambda r: r.__setitem__("accounts_after", 2)), (0, lambda r: r.__setitem__("seconds", 1)),
                         (1, lambda r: r.__setitem__("accounts_after", 2)), (2, lambda r: r.__setitem__("accounts_after", 3)),
                         (3, lambda r: r.__setitem__("result", "completed"))):
        broken = copy.deepcopy(dr5_results())
        mutate(broken[case])
        assert not all(J.judge_dr5(broken).values()), case


def test_dr6_and_dr8_judges_require_one_effect_and_real_overlap():
    per_ref = {"MP-k1": [{"seq": 1, "http": 200, "duplicate": False}, {"seq": 2, "http": 200, "duplicate": True}]}
    db = {"MP-k1": {"account_events_pago": 1, "approval_consumptions": 1, "account_status": "pago"}}
    conc = [{"seqs": [1, 2], "t_start": [1.0, 1.001], "t_end": [1.5, 1.6]}]
    assert J.judge_dr6(per_ref=per_ref, db=db, copies={"MP-k1": 2}, concurrency=conc) == []
    broken_db = {"MP-k1": {"account_events_pago": 2, "approval_consumptions": 1, "account_status": "pago"}}
    assert J.judge_dr6(per_ref=per_ref, db=broken_db, copies={"MP-k1": 2}, concurrency=conc)
    two_effects = {"MP-k1": [{"seq": 1, "http": 200, "duplicate": False}, {"seq": 2, "http": 200, "duplicate": False}]}
    assert J.judge_dr6(per_ref=two_effects, db=db, copies={"MP-k1": 2}, concurrency=conc)
    assert J.judge_dr6(per_ref=per_ref, db=db, copies={"MP-k1": 2},
                       concurrency=[{"seqs": [1, 2], "t_start": [1.0, 2.0], "t_end": [1.5, 2.5]}])      # sem sobreposicao
    steps = {"crash1": {"exit_code": 137, "accounts_g1_in_product": 1, "in_flight": [[1, "create_receivable", "sent"]]},
             "restart1": {"ops_after_restart": [[1, "create_receivable", "sent"]]},
             "resume1": {"exit_code": 0, "accounts_g1": 1, "accounts_g2": 1},
             "crash2": {"exit_code": 137, "approval_status_in_product": "approved", "decisions_before_resume": 1},
             "resume2": {"exit_code": 0, "decisions_after_resume": 1},
             "final": {"account_g1_status": "pago", "events_pago": 1, "consumptions": 1, "decisions": 1, "accounts_g1": 1,
                       "accounts_g2": 1, "status": {"in_flight": [], "counts": {"done": 3, "reconciled": 2}}},
             "reconciled_seqs": [1, 4]}
    assert J.judge_dr8(steps) == []
    for mutate in (lambda s: s["resume1"].__setitem__("accounts_g1", 2), lambda s: s["resume2"].__setitem__("decisions_after_resume", 2),
                   lambda s: s["crash1"].__setitem__("exit_code", 0), lambda s: s["final"].__setitem__("events_pago", 2),
                   lambda s: s.__setitem__("reconciled_seqs", [1]), lambda s: s["final"]["status"].__setitem__("in_flight", [4]),
                   lambda s: s["restart1"].__setitem__("ops_after_restart", [])):
        broken = copy.deepcopy(steps)
        mutate(broken)
        assert J.judge_dr8(broken)


def test_distinction_keeps_product_fact_derivation_and_instrument_failure_apart():
    results = [{"evidence_class": "PRODUCT_FACT", "class": "CORRECT"}, {"evidence_class": "PRODUCT_FACT", "class": "HARNESS_ERROR"},
               {"evidence_class": "EVALUATOR_DERIVATION", "class": "KNOWN_LIMIT"},
               {"evidence_class": "DRIVER_EXECUTION_EVIDENCE", "class": "CORRECT"}]
    out = J.distinguish(results)
    assert out["by_evidence_class"]["EVALUATOR_DERIVATION"] == {"KNOWN_LIMIT": 1}
    assert out["by_evidence_class"]["PRODUCT_FACT"] == {"CORRECT": 1, "HARNESS_ERROR": 1}
    assert out["instrument_failures"] == 1


# ----------------------------------------------------------------------------------------- compose real (formas)
def test_every_compose_mutation_applies_and_is_refused_on_the_merged_shape():
    resolved = merged_resolved()
    assert driver_lab.check_resolved_driver_compose(resolved, CONFIG) == []
    for label, mutate in G.COMPOSE_MUTATIONS.items():
        broken = copy.deepcopy(resolved)
        mutate(broken)                                   # nao pode levantar: mutacao que nao aplica nao prova nada
        assert driver_lab.check_resolved_driver_compose(broken, CONFIG), label


# ----------------------------------------------------------------------------------------- cenarios
@pytest.mark.parametrize("name,factory", [
    ("c0", lambda: S.scen_c0("inst")), ("dr3", lambda: S.scen_dr3("inst")), ("dr4", lambda: S.scen_dr4("inst")),
    ("dr5", lambda: S.scen_dr5("inst")), ("dr6", lambda: S.scen_dr6("inst")), ("dr8", lambda: S.scen_dr8("inst")),
    ("c0b", lambda: S.scen_c0b("inst"))])
def test_every_scenario_is_a_valid_public_agenda_for_the_driver(tmp_path, name, factory):
    builder = factory()
    info = builder.write(tmp_path, 2)
    manifest = load_manifest(tmp_path / "driver_manifest.json")
    ops = load_agenda(tmp_path / "public_agenda.json", manifest)
    assert [o["seq"] for o in ops] == list(range(1, len(ops) + 1))
    assert set(info["personas"]) <= set(E.DRIVER_PERSONAS)
    text = (tmp_path / "public_agenda.json").read_text(encoding="utf-8").lower()
    assert "oracle" not in text and "expectation" not in text
    names = [o["cliente"] for o in ops if o["op"] == "create_receivable"]
    assert len(names) == len(set(names)) and all(n.startswith("[SIM-HARNESS]") for n in names)
    keys = [o["idempotency_key"] for o in ops if "idempotency_key" in o]
    assert len(keys) == len(set(keys))


def test_scenario_days_only_move_forward_and_floor_scenario_straddles_the_floor():
    order = ["c0", "dr3", "dr4", "dr5", "dr6", "dr7", "dr8", "c0b", "d90"]
    days = [S.DAYS[k] for k in order]
    assert days == sorted(days) and len(set(days)) == len(days)
    assert S.DAYS["c0b"] == S.FLOOR_DAY - 1 == CONFIG["clock"]["floor_day"] - 1
    days_c0b = {o["day"] for o in S.scen_c0b("i").ops()}
    assert days_c0b == {S.FLOOR_DAY - 1, S.FLOOR_DAY}
    assert max(o["day"] for o in S.scen_dr8("i").ops()) < S.DAYS["c0b"]
    assert S.DAYS["dr4"] + 1 < S.DAYS["dr5"] and S.DAYS["c0"] + 1 < S.DAYS["dr3"]


def test_lifecycle_rule_matches_the_product_freeze_table():
    cases = [(30, False, "open"), (15, False, "open"), (14, False, "due_soon"), (1, False, "due_soon"), (0, False, "due_today"),
             (-1, False, "overdue"), (-5, False, "overdue"), (-6, False, "overdue_alert"), (-8, False, "overdue_alert"),
             (-8, True, "paid"), (5, True, "paid")]
    for days_to_due, paid, state in cases:
        assert S.expected_lifecycle(days_to_due, paid) == state, (days_to_due, paid)


def test_fixture_expectations_that_failed_in_the_prelive_are_now_computed_from_the_rule():
    """No PRE-LIVE a conta de -3 dias tinha 6 dias de atraso no D3 e minha expectativa fixa errou."""
    oracle = S.oracle_c0("c", 3)
    state = {e["subject"]["rec_ref"]: e["policy"] for e in oracle["expectations"]
             if e["kind"] == "lifecycle_state" and e["day"] == 3}
    assert state["C-04"] == "overdue_alert"
    state1 = {e["subject"]["rec_ref"]: e["policy"] for e in S.oracle_c0("c", 1)["expectations"]
              if e["kind"] == "lifecycle_state" and e["day"] == 1}
    assert state1["C-04"] == "overdue" and state1["C-01"] == "overdue_alert" and state1["C-02"] == "paid"
    assert state1["C-03"] == "open"
    # pagamento so e correlacionado depois da deteccao: o Oracle de C0 so conta eventos de contas VENCIDAS no dia D
    events = [e for e in oracle["expectations"] if e["kind"] == "account_event_count"]
    assert {e["subject"]["rec_ref"] for e in events} == {"C-04", "C-05", "C-06"} and all(e["day"] == 4 for e in events)


@pytest.mark.parametrize("name,oracle,builder", [
    ("c0", S.oracle_c0("c"), S.scen_c0("inst")), ("dr6", S.oracle_dr6(), S.scen_dr6("inst")),
    ("c0b", S.oracle_c0b(), S.scen_c0b("inst"))])
def test_technical_oracles_compile_to_values_free_plans(name, oracle, builder):
    plan = compile_plan(oracle, builder.ops(), S.D0, -180, OBSERVER_EMAIL)
    text = json.dumps(plan)
    for forbidden in ('"policy"', '"ideal"', '"semantics"', '"finding"', "case_ids"):
        assert forbidden not in text, (name, forbidden)
    assert plan["items"] and all(i["principal"] == "sim-harness-observer" for i in plan["items"])
    assert all(e["evidence_class"] in ("PRODUCT_FACT", "DRIVER_EXECUTION_EVIDENCE", "EVALUATOR_DERIVATION")
               for e in oracle["expectations"])


def test_c0b_plan_covers_the_floor_kinds_with_the_floor_barrier_and_the_cli_aggregate():
    oracle, builder = S.oracle_c0b(), S.scen_c0b("inst")
    plan = compile_plan(oracle, builder.ops(), S.D0, -180, OBSERVER_EMAIL)
    by_route = {i["route"] for i in plan["items"]}
    assert {"observations", "work_item", "account", "cli"} <= by_route
    cli = [i for i in plan["items"] if i["route"] == "cli"]
    assert len(cli) == 1 and cli[0]["channel"] == "cli" and cli[0]["safe_at"] == "end_of_run"
    obs = [i for i in plan["items"] if i["route"] == "observations"]
    assert obs and all("after_floor_barrier" in i["requires"] for i in obs)
    kinds = {e["kind"]: e["evidence_class"] for e in oracle["expectations"]}
    assert kinds["provenance_present"] == "EVALUATOR_DERIVATION" and kinds["provenance_aggregate"] == "PRODUCT_FACT"
    # as 3 contas: paga ANTES do floor, paga DEPOIS, nunca paga
    count = {e["subject"]["rec_ref"]: e["policy"] for e in oracle["expectations"] if e["kind"] == "observed_fact_count"}
    assert count == {"M-01": 0, "M-02": 1, "M-03": 0}
    pre = [o for o in builder.ops() if o["day"] == S.FLOOR_DAY - 1 and o["op"] == "execute_mark_paid"]
    post = [o for o in builder.ops() if o["day"] == S.FLOOR_DAY and o["op"] == "execute_mark_paid"]
    assert [o["rec_ref"] for o in pre] == ["M-01"] and [o["rec_ref"] for o in post] == ["M-02"]
    esc = {o["rec_ref"]: o for o in builder.ops() if o["op"] == "materialize_escalation"}
    for rec, pay in (("M-01", pre[0]), ("M-02", post[0])):
        assert (esc[rec]["day"], esc[rec]["at"]) < (pay["day"], pay["at"])     # escalonamento ANTES do pagamento


# ----------------------------------------------------------------------------------------- pipeline contra o produto falso
def run_scenario_against_fake(tmp_path, builder, oracle, first, last, facts, floor_day=99):
    info = builder.write(tmp_path / "in", 7)
    plan = compile_plan(oracle, info["ops"], S.D0, -180, OBSERVER_EMAIL)
    fake = FakeProduct()
    driver, fake, store, evidence, _ = make_driver(tmp_path, fake, agenda_path=tmp_path / "in" / "public_agenda.json")
    collector, _, chain, _ = make_collector(tmp_path, fake, plan)
    orchestrate(driver, fake, collector, first=first, last=last, floor_day=floor_day, finish=True)
    from sim.collector.chainlog import ChainLog

    records = ChainLog.read(chain.path)
    lines = [json.loads(l) for l in (tmp_path / "state" / "driver_evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    refs = {"refs": store.all_refs(), "due_days": store.all_due()}
    return plan, records, evaluate(oracle, plan, records, lines, refs, facts), lines


def test_c0_runs_through_driver_collector_and_evaluator_without_an_instrument_failure(tmp_path):
    day = S.DAYS["c0"]
    plan, records, ev, lines = run_scenario_against_fake(tmp_path, S.scen_c0("inst", "c", day), S.oracle_c0("c", day),
                                                         day, day + 1, {})
    assert verify_records(plan, records) == [] and missing_items(plan, records) == []
    assert ev["summary"].get("HARNESS_ERROR", 0) == 0 and ev["run_valid"] is True
    assert J.distinguish(ev["results"])["instrument_failures"] == 0
    groups = [r for r in lines if r.get("kind") == "concurrency"]
    assert sorted(len(g["seqs"]) for g in groups) == [2, 3]


def test_c0b_runs_end_to_end_and_provenance_needs_its_conditions(tmp_path):
    floor = S.FLOOR_DAY
    good_facts = {"provenance_exit": 0, "provenance_report": {"observations": {"observed_fact_with_provenance": 0,
                                                                                 "observed_fact_legacy_without_provenance": 0},
                                                              "integrity_failures": 0}}
    plan, records, ev, _ = run_scenario_against_fake(tmp_path, S.scen_c0b("inst", "m", floor), S.oracle_c0b("m", floor),
                                                     floor - 1, floor, good_facts, floor_day=floor)
    assert verify_records(plan, records) == [] and missing_items(plan, records) == []     # o item `cli` e do harness
    assert ev["summary"].get("HARNESS_ERROR", 0) == 0
    # sem as condicoes de exaustao, `provenance_present` NUNCA vira CORRECT (regra congelada)
    present = [r for r in ev["results"] if r["kind"] == "provenance_present"]
    assert present and all(r["class"] in ("CORRECT_ABSTENTION", "KNOWN_LIMIT", "DIVERGENCE") for r in present)
    bad = copy.deepcopy(good_facts)
    bad["provenance_report"]["observations"]["observed_fact_legacy_without_provenance"] = 3
    _, _, ev2, _ = run_scenario_against_fake(tmp_path / "again", S.scen_c0b("inst", "m", floor), S.oracle_c0b("m", floor),
                                             floor - 1, floor, bad, floor_day=floor)
    assert all(r["class"] == "KNOWN_LIMIT" for r in ev2["results"] if r["kind"] == "provenance_present")
    _, _, ev3, _ = run_scenario_against_fake(tmp_path / "third", S.scen_c0b("inst", "m", floor), S.oracle_c0b("m", floor),
                                             floor - 1, floor, {}, floor_day=floor)
    assert any(r["kind"] == "provenance_aggregate" and r["class"] == "HARNESS_ERROR" for r in ev3["results"])   # CLI ausente = falha do instrumento


# ----------------------------------------------------------------------------------------- hooks docker exec / floor
class FakeRunner:
    def __init__(self, replies=None):
        self.calls, self.replies = [], list(replies or [])

    def run(self, args, input_text=None, env=None, timeout=None, cwd=None):
        self.calls.append({"args": list(args), "stdin": input_text, "env": env})
        if self.replies:
            reply = self.replies.pop(0)
            return reply(args) if callable(reply) else reply
        return Result(0, "{}\n", "")


class FakeClock:
    def __init__(self):
        self.sets = []
        self.config_clock = CONFIG["clock"]

    def virtual(self, day, at):
        return (day, at)

    def set(self, target):
        self.sets.append(target)
        return {"target": target}


class FakeLab(SimpleNamespace):
    def __init__(self, runner=None, **kw):
        super().__init__(runner=runner or FakeRunner(), config=CONFIG, clock=FakeClock(), evidence={}, records=[], barriers=[],
                         **kw)

    def run_barrier(self, since, events, params=None):
        self.barriers.append((since, list(events)))
        return {"ok": True}

    def events(self):
        return ["e1", "e2"]

    def record(self, gate, ok, data):
        entry = {"status": "PASS" if ok else "FAIL", **data}
        self.records.append((gate, entry))
        return entry

    def save(self):
        pass

    def wait_ready(self):
        return {"ready": True}


def exec_args(call) -> list:
    return call["args"]


def test_docker_exec_hooks_target_the_isolated_containers_with_per_scenario_env():
    lab = FakeLab(FakeRunner([Result(0, '[[1, "09:00"], [1, "10:00"]]\n', ""),
                              Result(0, '{"executed": 2, "harness_ops": [], "counts": {}}\n', ""),
                              Result(0, '{"refs": {}, "due_days": {}}\n', ""), Result(0, '{"refs": 0}\n', ""),
                              Result(0, '{"trigger": "pre_slot"}\n', ""), Result(0, '{"completed_barriers": []}\n', ""),
                              Result(0, '{"seq": 5, "marked": true}\n', "")]))
    hooks, state = flow.build_hooks(lab, denv=flow.driver_env("c"), cenv=flow.collector_env("c"))
    assert hooks.driver_slots() == [(1, "09:00"), (1, "10:00")]
    hooks.driver_run_slot(1, "09:00")
    hooks.collector_load_refs(hooks.driver_export_refs())
    hooks.collector_trigger("pre_slot", 1, "09:00")
    hooks.collector_mark_barrier("after_floor_barrier")
    hooks.driver_mark_harness_done(5, "restart")
    calls = [c["args"] for c in lab.runner.calls]
    driver_calls = [c for c in calls if "auneron-sim-driver" in c]
    collector_calls = [c for c in calls if "auneron-sim-collector" in c]
    assert len(driver_calls) == 4 and len(collector_calls) == 3
    assert all("SIM_DRIVER_HOME=/state/c" in c for c in driver_calls)
    assert all("SIM_COLLECTOR_PLAN=/inputs/c/collection_plan.json" in c for c in collector_calls)
    assert json.loads(lab.runner.calls[3]["stdin"]) == {"refs": {}, "due_days": {}}
    assert not any(a in ("sh", "bash") for c in calls for a in c)


def test_hooks_raise_exec_error_on_a_nonzero_cli_exit_and_barrier_uses_the_clock_jump_instant():
    lab = FakeLab(FakeRunner([Result(3, '{"harness_error": "agenda fora de ordem"}\n', "")]))
    hooks, state = flow.build_hooks(lab)
    with pytest.raises(flow.ExecError):
        hooks.driver_run_slot(1, "09:00")
    hooks.clock_set(1, "17:00")
    assert state["since"] is None
    hooks.clock_set(1, "18:00")
    since = state["since"]
    assert since is not None and lab.clock.sets[-1] == (1, "18:00")
    hooks.daily_barrier(1)
    assert lab.barriers == [(since, ["e1", "e2"])]


def floor_activation_evidence(t0="2026-01-19T11:00:00.123456Z"):
    return {"capture_code": 0, "t0": t0, "check_code": 0, "check_stdout": f"OK T0={t0}", "negative_future_code": 2,
            "negative_future_stdout": "ACTIVATION PREFLIGHT FAIL: future"}


def run_activate_floor(monkeypatch, networks):
    compose_calls = []
    monkeypatch.setattr(flow.activation, "capture_and_check", lambda runner, cfg: floor_activation_evidence())
    monkeypatch.setattr(flow.E, "compose_ext", lambda lab, *a, **k: compose_calls.append(a) or Result(0, "", ""))
    inspect = json.dumps([{"NetworkSettings": {"Networks": {n: {} for n in networks}}}])
    lab = FakeLab(FakeRunner([Result(0, inspect, "")]))
    lab.startup_record = lambda since: {"evidence_floor": floor_activation_evidence()["t0"], "evidence_floor_state": "armed",
                                        "evidence_worker_enabled": True}
    monkeypatch.setattr("time.sleep", lambda s: None)
    return lab, compose_calls


def test_floor_activation_recreates_the_backend_through_the_override_and_keeps_both_networks(monkeypatch):
    lab, compose_calls = run_activate_floor(monkeypatch, ["auneron_sim_internal", "auneron_sim_driver"])
    data = flow.activate_floor(lab)
    assert compose_calls == [("up", "-d", "--no-deps", "--force-recreate", "sim-backend")]       # via compose_ext (override)
    assert lab.clock.sets == [(14, "07:59"), (14, "08:00")]
    assert lab.evidence["floor_t0"] == floor_activation_evidence()["t0"]
    assert lab.barriers and lab.records[-1][0] == "FLOOR" and lab.records[-1][1]["status"] == "PASS"
    assert data["networks_after_recreate"] == ["auneron_sim_driver", "auneron_sim_internal"]


def test_floor_activation_fails_if_the_recreated_backend_lost_the_driver_network(monkeypatch):
    lab, _ = run_activate_floor(monkeypatch, ["auneron_sim_internal"])
    with pytest.raises(HarnessError):
        flow.activate_floor(lab)
    assert lab.records[-1][1]["status"] == "FAIL"


def test_floor_activation_stops_when_the_real_preflight_does_not_pass(monkeypatch):
    lab, compose_calls = run_activate_floor(monkeypatch, ["auneron_sim_internal", "auneron_sim_driver"])
    bad = floor_activation_evidence()
    bad["negative_future_code"] = 0                                    # o preflight deveria recusar um T0 no futuro
    monkeypatch.setattr(flow.activation, "capture_and_check", lambda runner, cfg: bad)
    with pytest.raises(HarnessError):
        flow.activate_floor(lab)
    assert compose_calls == [] and "floor_t0" not in lab.evidence       # nada foi recriado


def test_compose_ext_always_checks_the_extended_guard_before_any_command(monkeypatch):
    runner = FakeRunner()
    lab = SimpleNamespace(runner=runner, config=CONFIG, env=lambda: {})
    bad = merged_resolved()
    bad["services"]["sim-driver"]["network_mode"] = "host"
    monkeypatch.setattr(E, "resolved_compose", lambda l: bad)
    with pytest.raises(GuardViolation):
        E.compose_ext(lab, "up", "-d")
    assert runner.calls == []
    monkeypatch.setattr(E, "resolved_compose", lambda l: merged_resolved())
    E.compose_ext(lab, "up", "-d")
    args = runner.calls[0]["args"]
    assert args[:3] == ["docker", "compose", "-p"] and args[3] == "auneron-sim"
    assert args.count("-f") == 2 and str(E.OVERRIDE) in args and args[-2:] == ["up", "-d"]


def test_provenance_facts_parse_the_cli_json_and_keep_the_exit_code():
    report = {"observations": {"observed_fact_with_provenance": 1}, "integrity_failures": 0}
    lab = FakeLab(FakeRunner([Result(0, "warning line\n" + json.dumps(report) + "\n", "")]))
    facts = flow.provenance_facts(lab)
    assert facts == {"provenance_exit": 0, "provenance_report": report}
    assert "evidence_provenance_report.py" in lab.runner.calls[0]["args"][-2]
    lab = FakeLab(FakeRunner([Result(3, "EVIDENCE PROVENANCE REPORT UNAVAILABLE: x\n", "")]))
    facts = flow.provenance_facts(lab)
    assert facts["provenance_exit"] == 3 and facts["provenance_report"] is None


# ----------------------------------------------------------------------------------------- fases e CLI
def test_compose_config_phase_records_a_pass_only_when_every_negative_is_refused(monkeypatch, tmp_path):
    lab = FakeLab(live_dir=tmp_path)
    monkeypatch.setattr(E, "resolved_compose", lambda l: merged_resolved())
    result = G.phase_compose_config(lab)
    assert result["status"] == "PASS" and set(result["negatives"].values()) == {"REFUSED"}
    assert len(result["negatives"]) == len(G.COMPOSE_MUTATIONS) == 10
    weak = merged_resolved()
    del weak["services"]["sim-driver"]["volumes"]
    monkeypatch.setattr(E, "resolved_compose", lambda l: weak)
    result = G.phase_compose_config(lab)
    assert result["status"] == "FAIL" and any(v.startswith("MUTATION_NOT_APPLIED") for v in result["negatives"].values())


def test_candidate_manifest_is_deterministic_and_changes_with_any_byte():
    rows = [("a" * 64, "sim/a.py"), ("b" * 64, "sim/b.py")]
    text = G.manifest_text(rows)
    assert text == f"{'a' * 64} *sim/a.py\n{'b' * 64} *sim/b.py\n"
    assert G.manifest_digest(rows) == G.manifest_digest(list(rows))
    assert G.manifest_digest(rows) != G.manifest_digest([("a" * 64, "sim/a.py"), ("c" * 64, "sim/b.py")])
    assert len(G.manifest_digest(rows)) == 16


def test_prelive_candidate_digest_method_is_the_one_the_po_approved():
    """O digest aprovado `02769e26585a1035` e o sha256 (16 hex) do manifesto `<sha256> *<caminho>`; reproduzido aqui."""
    import hashlib

    rows = [("0" * 64, "sim/x.py")]
    assert G.manifest_digest(rows) == hashlib.sha256(G.manifest_text(rows).encode("utf-8")).hexdigest()[:16]


def test_cli_exposes_every_phase_and_requires_an_instance_after_prep():
    from sim.live import cli

    wanted = {"prep", "up-base", "provision", "compose-config", "build", "up-ext", "stage-base", "stage-secrets", "c0", "c0b",
              "dr1", "dr2", "dr3", "dr4", "dr5", "dr6", "dr7", "dr8", "reset", "teardown"}
    assert wanted <= set(cli.PHASES) and all(callable(f) for f in cli.PHASES.values())
    with pytest.raises(SystemExit):
        cli.main(["dr3"])
    with pytest.raises(SystemExit):
        cli.main(["not-a-phase", "--instance", "x"])


def test_sim14_lab_modules_are_not_modified_by_the_live_harness():
    frozen = json.loads((REPO_ROOT / "sim/tests/sim14_frozen_hashes.json").read_text(encoding="utf-8"))
    frozen = frozen.get("files", frozen)
    import hashlib

    assert len(frozen) == 23
    for rel, digest in frozen.items():
        assert hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest() == digest, rel


def pipeline_run(collected: int, summary=None):
    summary = summary if summary is not None else {"CORRECT": 3}
    evaluation = {"run_valid": True, "summary": summary, "evaluation_sha256": "x", "results": [], "provenance": {}}
    return {"evaluation": evaluation, "problems": [], "missing": [], "collected": [{}] * collected, "seconds": 1,
            "state": {"barriers": []}, "distinction": {"by_evidence_class": {}, "instrument_failures": 0}}


def test_pipeline_verdict_counts_only_http_plan_items_and_not_the_harness_cli_item(tmp_path):
    """Regressao do C0b (tentativa 1): o plano tinha 10 itens (1 `cli` do harness) e 9 foram coletados corretamente."""
    (tmp_path / "collection_plan.json").write_text("{}", encoding="utf-8")
    (tmp_path / "public_agenda.json").write_text("{}", encoding="utf-8")
    items = [{"item_id": "I-1"}, {"item_id": "I-2", "channel": "http"}, {"item_id": "I-3", "channel": "cli"}]
    info = {"plan": {"items": items}, "work": tmp_path}
    assert [i["item_id"] for i in G.http_items(info["plan"])] == ["I-1", "I-2"]
    lab = FakeLab(live_dir=tmp_path)
    assert G._record_pipeline(lab, "G", pipeline_run(2), info)["status"] == "PASS"            # 2 HTTP coletados
    assert G._record_pipeline(lab, "G", pipeline_run(1), info)["status"] == "FAIL"            # item HTTP faltando
    assert G._record_pipeline(lab, "G", pipeline_run(3), info)["status"] == "FAIL"            # coleta a mais
    assert G._record_pipeline(lab, "G", pipeline_run(2, {"HARNESS_ERROR": 1}), info)["status"] == "FAIL"


def test_dr5_run_judge_tells_an_execution_failure_from_the_expected_harness_error_messages():
    """Regressao: os casos 2 e 3 trazem `error` (HarnessError esperado); isso NAO e falha de execucao do container."""
    good = dr5_results()
    assert "error" in good[2] and "error" in good[3]
    assert J.judge_dr5_run(good) == J.judge_dr5(good) and all(J.judge_dr5_run(good).values())
    crashed = copy.deepcopy(good)
    crashed[1] = {"execution_error": "docker exec: exit 1"}
    assert J.judge_dr5_run(crashed) == {"execution": False}
    assert J.judge_dr5_run({k: v for k, v in good.items() if k != 3}) == {"execution": False}       # caso faltando
    wrong = copy.deepcopy(good)
    wrong[2]["accounts_after"] = 3
    assert not all(J.judge_dr5_run(wrong).values())
