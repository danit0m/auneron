"""
SIM-1.5F -- fixture `[SIM-HARNESS]` do SR-1: determinismo (bytes e hashes), perfil fixado, cobertura dos picos do NH,
marcadores, disjuncao e isolamento (com controles positivos), TA-1 independente, e o laco COMPLETO (Driver, Collector,
orquestrador, runner, Evaluator) contra o produto falso.
"""

from __future__ import annotations

import ast
import builtins
import collections
import json
import pathlib
import re

import pytest

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.rehearsal import contamination
from sim.rehearsal import generate as G
from sim.rehearsal import oracle as O
from sim.rehearsal import profile as P
from sim.rehearsal import schedule as S
from sim.tests.conftest import SCENARIOS
from sim.tests.conftest import SIM_ROOT

REHEARSAL = SIM_ROOT / "rehearsal"
PINS = json.loads((REHEARSAL / "PINS.json").read_text(encoding="utf-8"))
TEST_SCALE = 300


@pytest.fixture(scope="module")
def profile():
    return P.load_profile()


@pytest.fixture(scope="module")
def full_plan():
    return S.build_plan()


@pytest.fixture(scope="module")
def full_bundle():
    return G.build_bundle()


@pytest.fixture(scope="module")
def full_ops(full_bundle):
    return json.loads(full_bundle["public_agenda.json"])["ops"]


@pytest.fixture(scope="module")
def full_oracle(full_bundle):
    return json.loads(full_bundle["oracle.json"])


# ------------------------------------------------------------------------------------------------- perfil
def test_profile_is_pinned_integer_only_and_tamper_evident(tmp_path, profile):
    assert sha256_bytes(P.PROFILE_PATH.read_bytes()) == P.PROFILE_SHA256 == PINS["profile_sha256"]
    assert profile["accounts_total"] == 880 and profile["ops_total"] == 4987 and profile["d0_burst_slot_ops"] == 105
    copy = tmp_path / "p.json"
    copy.write_bytes(P.PROFILE_PATH.read_bytes().replace(b'"accounts_total":880', b'"accounts_total":881'))
    with pytest.raises(P.ProfileError, match="adulterado"):
        P.load_profile(copy)


@pytest.mark.parametrize("bad", [{"name": "Padaria Estrela"}, {"x": 1.5}, {"x": True}, {"x": [1, "a"]}, {"cliente": {"a": "b"}},
                                 {"bad-key": 1}])
def test_profile_schema_refuses_anything_that_is_not_an_integer(profile, bad):
    with pytest.raises(P.ProfileError):
        P.validate_profile({**profile, **bad})


def test_profile_holds_only_counts_no_text_values(profile):
    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
        else:
            yield value

    assert all(isinstance(v, int) and not isinstance(v, bool) for v in walk(profile))


# ------------------------------------------------------------------------------------------------- determinismo
def test_the_bundle_is_byte_identical_when_generated_twice_and_matches_the_pins(full_bundle):
    again = G.build_bundle()
    assert again == full_bundle
    manifest = G.fixture_manifest(full_bundle, seed=S.SEED, scale_permille=1000)
    assert manifest["bundle_sha256"] == PINS["1000"]["bundle_sha256"] and manifest["files"] == PINS["1000"]["files"]
    small = G.fixture_manifest(G.build_bundle(scale_permille=TEST_SCALE), seed=S.SEED, scale_permille=TEST_SCALE)
    assert small["bundle_sha256"] == PINS[str(TEST_SCALE)]["bundle_sha256"]


def test_written_bundle_verifies_and_any_byte_change_is_detected(tmp_path):
    G.write_bundle(tmp_path / "b", scale_permille=TEST_SCALE)
    assert G.verify_bundle(tmp_path / "b") == []
    target = tmp_path / "b" / "oracle.json"
    target.write_bytes(target.read_bytes() + b" ")
    assert any("oracle.json" in p for p in G.verify_bundle(tmp_path / "b"))


def test_different_seeds_change_the_bundle_and_the_seed_is_not_the_nh_seed():
    other = G.build_bundle(seed=S.SEED + 1, scale_permille=TEST_SCALE)
    assert other != G.build_bundle(scale_permille=TEST_SCALE) and S.SEED != 340001


def test_scale_below_the_minimum_fails_closed():
    with pytest.raises(S.ScheduleError):
        S.build_plan(scale_permille=100)
    with pytest.raises(S.ScheduleError):
        S.build_plan(scale_permille=50)


def test_splitmix_rng_matches_the_reference_vectors_and_is_pure_integer():
    rng = S.Rng(0)
    assert [rng.next() for _ in range(3)] == [0xE220A8397B1DCDAF, 0x6E789E6AA1B965F4, 0x06C45D188009454F]
    items = list(range(20))
    S.Rng(5).shuffle(items)
    again = list(range(20))
    S.Rng(5).shuffle(again)
    assert items == again and sorted(items) == list(range(20))
    assert S.Rng(9).sample(list(range(10)), 3) == S.Rng(9).sample(list(range(10)), 3)
    with pytest.raises(S.ScheduleError):
        S.Rng(1).sample([1], 2)


# ------------------------------------------------------------------------------------------------- picos do NH (limites inferiores)
def test_the_fixture_meets_or_exceeds_every_nh_peak_in_the_profile(full_ops, full_oracle, profile):
    by_type = collections.Counter(o["op"] for o in full_ops)
    by_slot = collections.Counter((o["day"], o["at"]) for o in full_ops)
    by_day = collections.Counter(o["day"] for o in full_ops)
    kinds = collections.Counter(e["kind"] for e in full_oracle["expectations"])
    assert by_type["create_receivable"] >= profile["accounts_total"] and len(full_ops) >= profile["ops_total"]
    assert max(by_day.values()) >= profile["max_ops_day"] * 2                               # pico de dia >= 2x o do NH
    assert by_day[0] >= profile["d0_ops"] and max(n for (d, _), n in by_slot.items() if d == 0) == profile["d0_burst_slot_ops"]
    assert max(n for (d, _), n in by_slot.items() if d > 0) <= profile["max_ops_slot_after_d0"]
    assert by_type["request_mark_paid"] + by_type["materialize_mark_overdue"] >= profile["approvals_total"]
    assert by_type["request_mark_paid"] >= profile["approvals_mp"] and by_type["materialize_mark_overdue"] >= profile["approvals_mo"]
    assert kinds["escalation_work_item_exists"] >= profile["escalation_episodes"]
    assert by_type["record_assessment"] >= profile["assessments"] * 99 // 100
    assert by_type["consult_nba"] >= profile["consults"] and by_type["change_due_date"] >= profile["due_changes"]
    assert kinds["observed_fact_count"] >= profile["observed_fact_expectations"]
    assert kinds["observed_fact_links_event"] >= profile["observed_fact_links"]
    assert kinds["provenance_present"] >= profile["provenance_derivations"]


def test_restart_straddles_a_larger_population_and_sits_alone_in_its_slot(full_ops, profile):
    restart = [o for o in full_ops if o["op"] == "restart_backend"]
    assert len(restart) == 1 and (restart[0]["day"], restart[0]["at"]) == (S.RESTART_DAY, "11:00")
    assert [o["op"] for o in full_ops if (o["day"], o["at"]) == (S.RESTART_DAY, "11:00")] == ["restart_backend"]
    created = sum(1 for o in full_ops if o["op"] == "create_receivable" and o["day"] <= S.RESTART_DAY)
    assert created >= profile["restart_accounts"] * 17 // 10
    straddle = [o for o in full_ops if o["day"] == S.RESTART_DAY and o["op"] == "execute_mark_paid"]
    requests = [o for o in full_ops if o["day"] == S.RESTART_DAY and o["op"] == "request_mark_paid"]
    assert len(straddle) == len(requests) >= 30 and all(o["at"] > "11:00" for o in straddle) and all(o["at"] < "11:00" for o in requests)


def test_floor_day_hosts_payments_of_accounts_escalated_before_it(full_plan):
    post = [a for a in full_plan.accounts if a.role == "E2"]
    assert len(post) >= 180 and all(S.FLOOR_DAY <= a.pay["day"] <= 19 and a.esc_day < S.FLOOR_DAY for a in post)
    pre = [a for a in full_plan.accounts if a.role == "E1"]
    assert pre and all(a.pay["day"] < S.FLOOR_DAY and a.pay["day"] > a.esc_day for a in pre)
    never = [a for a in full_plan.accounts if a.role == "E3"]
    assert never and all(a.pay is None for a in never)


def test_late_and_midnight_slots_match_the_nh_instants_and_never_hit_the_snapshot_window(full_ops, profile):
    minutes = lambda at: int(at[:2]) * 60 + int(at[3:])                                     # noqa: E731
    late = sorted(minutes(at) for at in {o["at"] for o in full_ops} if minutes(at) > 18 * 60)
    assert late == sorted(set(profile["late_slot_minutes"]))
    exec_late = sorted(minutes(o["at"]) for o in full_ops if o["op"] == "execute_mark_paid" and minutes(o["at"]) > 18 * 60)
    assert exec_late == sorted(profile["late_slot_minutes"])
    midnight = [o for o in full_ops if o["at"] == "00:00"]
    assert len(midnight) == profile["midnight_slots"] and all(o["op"] == "execute_mark_paid" for o in midnight)
    assert not [o for o in full_ops if "17:30" <= o["at"] <= "18:30"]
    gaps = collections.defaultdict(list)
    for at in sorted({(o["day"], o["at"]) for o in full_ops}):
        gaps[at[0]].append(minutes(at[1]))
    assert all(b - a >= 15 for times in gaps.values() for a, b in zip(times, times[1:]))


def test_concurrency_replays_rejections_and_wrong_attempts_meet_the_profile(full_ops, profile):
    same = collections.Counter(o["group"] for o in full_ops if o.get("mode") == "same_instant")
    conc = collections.Counter(o["group"] for o in full_ops if o.get("mode") == "concurrent")
    assert sorted(same.values()) == profile["same_instant_group_sizes"] and sorted(conc.values()) == [2, 3]
    for group in same:
        assert len({(o["day"], o["at"]) for o in full_ops if o.get("group") == group}) == 1
    assert sum(1 for o in full_ops if o.get("mode") == "replay") == profile["replay_ops"]
    wrong = [o for o in full_ops if o["op"] == "materialize_escalation" and o["ref"].startswith("SH-ESC-W")]
    assert len(wrong) == profile["wrong_escalation_attempts"]
    attempts = [o for o in full_ops if o["op"] == "decide_mark_paid" and o["actor"] == "sim-gerente-fin"]
    attempts += [o for o in full_ops if o["op"] == "execute_mark_paid" and o["actor"] == "sim-coordenador-fin"]
    assert len(attempts) == 7


def test_the_sweep_day_has_no_operations_and_at_least_1_4x_the_nh_collection_volume(full_ops, full_oracle, profile):
    from sim.oracle.collection_plan import compile_plan

    assert not [o for o in full_ops if o["day"] == S.LAST_DAY]
    plan = compile_plan(full_oracle, full_ops)
    sweep = sum(1 for i in plan["items"] if i["safe_at"] == "after_daily_barrier" and i["day"] == S.LAST_DAY)
    assert sweep >= profile["sweep_items"] * 14 // 10


# ------------------------------------------------------------------------------------------------- marcadores, disjuncao, contaminacao
def test_every_operation_carries_the_fixture_markers(full_ops):
    assert contamination.format_problems(full_ops) == []
    assert S.SCENARIO_ID.startswith("sim-harness-sr1") and not S.SCENARIO_ID.startswith("nh")
    creates = [o for o in full_ops if o["op"] == "create_receivable"]
    assert all(o["email"].endswith("@sim-harness-sr1.example.com") and "[SIM-HARNESS-SR1]" in o["cliente"] for o in creates)


def test_fixture_and_nh_identities_are_disjoint_with_a_positive_control(full_ops):
    nh = json.loads((SCENARIOS / "nh-small-v2-s340001" / "public_agenda.json").read_bytes())["ops"]
    assert contamination.disjointness(full_ops, nh) == []
    poisoned = [dict(o) for o in full_ops]
    first = next(o for o in poisoned if o["op"] == "create_receivable")
    first["email"] = next(o for o in nh if o["op"] == "create_receivable")["email"]
    assert contamination.disjointness(poisoned, nh) == [("email", first["email"])]


def test_format_check_catches_missing_markers():
    ops = [{"seq": 1, "op": "create_receivable", "cliente": "Padaria", "email": "x@nova-horizonte.example.com",
            "whatsapp": "+55 00 0000-0001", "rec_ref": "R-1"}]
    assert len(contamination.format_problems(ops)) >= 3


def test_scan_tree_finds_markers_in_text_gzip_and_sqlite_and_the_control_is_positive(tmp_path):
    import gzip
    import sqlite3

    (tmp_path / "a.txt").write_text("limpo", encoding="utf-8")
    assert contamination.scan_tree(tmp_path)["hits"] == []
    (tmp_path / "b.log.gz").write_bytes(gzip.compress(b"... [SIM-HARNESS-SR1] 0001 ..."))
    db = sqlite3.connect(tmp_path / "c.db")
    db.execute("create table t (v text)")
    db.execute("insert into t values ('sr1-0001@sim-harness-sr1.example.com')")
    db.commit()
    db.close()
    (tmp_path / "d.txt").write_text("Sim-Harness-SR1.example.com", encoding="utf-8")
    hits = {h["path"] for h in contamination.scan_tree(tmp_path)["hits"]}
    assert hits == {"b.log.gz", "c.db", "d.txt"}
    assert contamination.scan_tree(tmp_path, allow_paths=("b.log.gz", "c.db", "d.txt"))["hits"] == []


def test_p14_judge_is_fail_closed():
    good = {"volumes_created_after_previous_terminal": True, "driver_state_present": False, "collector_state_present": False,
            "business_rows": 0, "marker_rows": 0, "previous_runs_non_terminal": False}
    assert contamination.judge_p14(good) == []
    for key, value in (("volumes_created_after_previous_terminal", False), ("driver_state_present", True),
                       ("collector_state_present", True), ("business_rows", 3), ("marker_rows", 1),
                       ("previous_runs_non_terminal", True)):
        assert contamination.judge_p14({**good, key: value}), key
    assert "ausentes" in contamination.judge_p14({k: v for k, v in good.items() if k != "marker_rows"})[0]


# ------------------------------------------------------------------------------------------------- isolamento do gerador
GENERATOR_MODULES = ("schedule.py", "oracle.py", "generate.py", "profile.py", "contamination.py", "cli.py")


def imports(path: pathlib.Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def test_generator_modules_never_import_the_authoring_utility_or_the_nh_artifacts():
    for name in GENERATOR_MODULES:
        found = imports(REHEARSAL / name)
        assert "sim.rehearsal.extract_profile" not in found and not any("extract_profile" in n for n in found), name
        assert not [n for n in found if n.startswith(("sim.scenarios", "sim.company"))], name
        text = (REHEARSAL / name).read_text(encoding="utf-8")
        assert "nova_horizonte" not in text and "sim/scenarios" not in text and "nh-small" not in text, name


def test_only_the_authoring_utility_touches_the_nh_agenda():
    readers = [p.name for p in REHEARSAL.glob("*.py") if "scenarios" in p.read_text(encoding="utf-8")
               or "nh-small-v2" in p.read_text(encoding="utf-8")]
    assert readers == ["extract_profile.py"]


def test_building_the_bundle_reads_no_file_other_than_the_profile(monkeypatch):
    G.build_bundle(scale_permille=TEST_SCALE)                            # aquece importacoes
    touched: list = []
    real_open, real_read_bytes, real_read_text = builtins.open, pathlib.Path.read_bytes, pathlib.Path.read_text

    def spy_open(file, *args, **kwargs):
        touched.append(str(file))
        return real_open(file, *args, **kwargs)

    def spy_bytes(self):
        touched.append(str(self))
        return real_read_bytes(self)

    def spy_text(self, *args, **kwargs):
        touched.append(str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(pathlib.Path, "read_bytes", spy_bytes)
    monkeypatch.setattr(pathlib.Path, "read_text", spy_text)
    G.build_bundle(scale_permille=TEST_SCALE)
    monkeypatch.undo()
    normalized = {t.replace("\\", "/") for t in touched}
    assert normalized == {str(P.PROFILE_PATH).replace("\\", "/")}, normalized
    assert not [t for t in normalized if "scenarios" in t or "nova_horizonte" in t]


# ------------------------------------------------------------------------------------------------- Oracle tecnico
def test_the_independent_ta1_checker_is_clean_and_has_a_positive_control(full_ops, full_oracle):
    assert O.check_ta1_independent(full_ops, full_oracle["expectations"]) == []
    broken = [dict(e) for e in full_oracle["expectations"]]
    late = next(e for e in broken if e["kind"] == "account_status" and e["policy"] == "pago"
                and e["subject"]["rec_ref"] in {o["rec_ref"] for o in full_ops if o["at"] == "23:59"})
    late["day"] -= 1                                                   # volta ao dia do pagamento tardio (o defeito do F-1.5D-1)
    assert O.check_ta1_independent(full_ops, broken)


def test_late_payments_are_expected_on_the_next_snapshot_and_midnight_ones_on_the_same_day(full_ops, full_oracle):
    pay_at = {o["rec_ref"]: (o["day"], o["at"]) for o in full_ops if o["op"] == "execute_mark_paid" and o["actor"] == "sim-gerente-fin"}
    status = {(e["subject"]["rec_ref"], e["day"]) for e in full_oracle["expectations"]
              if e["kind"] == "account_status" and e["policy"] == "pago"}
    late = [rec for rec, (day, at) in pay_at.items() if at > "18:00"]
    assert len(late) == 5 and all((rec, pay_at[rec][0] + 1) in status and (rec, pay_at[rec][0]) not in status for rec in late)
    midnight = [rec for rec, (day, at) in pay_at.items() if at == "00:00"]
    assert len(midnight) == 2 and all((rec, pay_at[rec][0]) in status for rec in midnight)


def test_oracle_expectations_are_valid_v22_items_with_a_technical_claim(full_oracle):
    assert full_oracle["oracle_version"] == "v2.2" and "VERIFIED" not in full_oracle["claim"] and "TECHNICAL" in full_oracle["claim"]
    ids = [e["exp_id"] for e in full_oracle["expectations"]]
    assert len(ids) == len(set(ids)) >= 10000
    for e in full_oracle["expectations"]:
        assert e["evidence_class"] and e["safe_at"]
        if e["kind"] in ("observed_fact_count", "observed_fact_links_event"):
            assert e["requires"] == ["after_floor_barrier"] and e["day"] >= S.FLOOR_DAY or e["semantics"] == "abstain"


def test_expectations_come_from_public_rules_not_from_product_values():
    text = (REHEARSAL / "oracle.py").read_text(encoding="utf-8")
    assert "rules.lifecycle_state" in text and "rules.nba_decision" in text and "knowledge_projection" in text
    found = imports(REHEARSAL / "oracle.py")
    assert not [n for n in found if "sim.tests" in n or "sim.driver" in n or "sim.collector" in n or "sim.evaluator" in n]


# ------------------------------------------------------------------------------------------------- Driver real contra o produto falso
def test_the_real_driver_executes_every_operation_and_only_the_designed_rejections_fail(tmp_path):
    from sim.tests.driver_support import make_driver
    from sim.tests.driver_support import run_slots

    G.write_bundle(tmp_path / "b", scale_permille=TEST_SCALE)
    driver, fake, store, evidence, _ = make_driver(tmp_path, agenda_path=tmp_path / "b" / "public_agenda.json")
    run_slots(driver, fake)
    plan = S.build_plan(scale_permille=TEST_SCALE)
    assert store.counts() == {"done": plan.meta["ops"]}
    assert len(fake.accounts) == plan.meta["accounts"] and len(fake.approvals) == plan.meta["mp"] + plan.meta["mo"]
    lines = [json.loads(l) for l in (tmp_path / "state" / "driver_evidence.jsonl").read_text().splitlines()]
    rejected = collections.Counter((r["op"], r["http_status"]) for r in lines
                                   if r.get("kind") == "op_result" and r["http_status"] not in (200, 201))
    assert rejected == {("decide_mark_paid", 403): 4, ("execute_mark_paid", 403): 3}
    paid = [a for a in fake.accounts.values() if a["status"] == "pago"]
    assert len(paid) == plan.meta["mp"]


def test_full_loop_runner_orchestrator_driver_collector_evaluator_against_the_fake(tmp_path):
    from sim.runner import harness_side
    from sim.runner import package
    from sim.runner import results as results_mod
    from sim.tests import test_runner_e2e as E2E
    from sim.tests.fixture_support import build_fixture_world

    world = build_fixture_world(tmp_path, TEST_SCALE)
    run, live, nh = E2E.start(tmp_path, world, balance_mode="enforce", timeline=(S.LAST_DAY, S.FLOOR_DAY, S.RESTART_DAY))
    result = nh.run()
    assert result["state"] == "COMPLETED", RunLedgerTail(run)
    assert package.verify(run.run_dir)["ok"]
    report = harness_side.evaluate_package(run.run_dir, registry=run.registry)
    assert report["evaluation"]["summary"].get("HARNESS_ERROR", 0) == 0
    assert results_mod.verify_results(run.run_dir)["ok"]


def RunLedgerTail(run):
    from sim.runner.ledger import RunLedger

    return RunLedger(run.run_dir).events()[-1]["payload"]


# ------------------------------------------------------------------------------------------ gate de mutantes (1.5F)
def test_the_seed_drives_the_plan_itself_not_only_the_manifest():
    base = S.build_plan(S.SEED, TEST_SCALE)
    other = S.build_plan(S.SEED + 1, TEST_SCALE)
    assert base.ops != other.ops and [a.cents for a in base.accounts] != [a.cents for a in other.accounts]
    assert S.build_plan(S.SEED, TEST_SCALE).ops == base.ops


def test_the_restart_slot_is_reserved_for_the_restart_day_only(monkeypatch):
    seen = {}

    class Spy(S.DayAllocator):
        def __init__(self, day, reserved=()):
            seen[day] = tuple(reserved)
            super().__init__(day, reserved)

    monkeypatch.setattr(S, "DayAllocator", Spy)
    S.build_plan(S.SEED, TEST_SCALE)
    assert seen[S.RESTART_DAY] == (S.RESTART_MINUTE,)
    assert all(reserved == () for day, reserved in seen.items() if day != S.RESTART_DAY) and seen


def test_the_allocator_never_hands_out_a_reserved_slot():
    alloc = S.DayAllocator(S.RESTART_DAY, reserved=(S.RESTART_MINUTE,))
    alloc.cursor = S.RESTART_MINUTE
    assert alloc._slot() == S.RESTART_MINUTE + S.SLOT_STEP
    free = S.DayAllocator(S.RESTART_DAY)
    free.cursor = S.RESTART_MINUTE
    assert free._slot() == S.RESTART_MINUTE


def test_generation_fails_when_the_independent_ta1_check_finds_a_violation(monkeypatch):
    violation = [{"exp_id": "E-000001", "kind": "account_status", "day": 1, "effective": 2, "observation": 1}]
    monkeypatch.setattr(O, "check_ta1_independent", lambda ops, expectations: violation)
    with pytest.raises(O.OracleError, match="TA-1"):
        G.build_bundle(scale_permille=TEST_SCALE)
