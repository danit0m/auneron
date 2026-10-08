"""
SIM-1.5E -- gates estaticos do Oracle v2.2 (TA-1, D-1.5E-1): v2.1 byte-imutavel, agenda/mundo byte-identicos, SO as 5
expectativas afetadas, conservacao das 13.074, nenhum `exp_id` literal no algoritmo, falha fechada quando a violacao nao
e classificavel, e o laco semantico que prova que o defeito era da EXPECTATIVA (nao do produto).
"""

from __future__ import annotations

import ast
import copy
import json
import re
import shutil
from types import SimpleNamespace

import pytest

from sim.evaluator.evaluate import evaluate
from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.generator.cli import DEFAULT_SEED
from sim.generator.world import Title
from sim.oracle import v2_1
from sim.oracle import v2_2
from sim.oracle.collection_plan import compile_plan
from sim.oracle.v2_2 import Context
from sim.oracle.v2_2 import TemporalAdmissibilityError
from sim.tests.collector_support import load_agenda_ops
from sim.tests.conftest import SCENARIOS
from sim.tests.conftest import SIM_ROOT

EXEC_ID = "nh-small-v2-s340001"
V21_DIR = SIM_ROOT / "oracle" / "artifacts" / f"{EXEC_ID}-oracle-v2.1"
V22_DIR = SIM_ROOT / "oracle" / "artifacts" / f"{EXEC_ID}-oracle-v2.2"
# conjunto de REGRESSAO conhecido (so aqui: o gerador nao conhece estes IDs)
REGRESSION = {"E-002260": (39, 40), "E-003093": (49, 50), "E-003112": (49, 50), "E-005583": (85, 86), "E-008778": (133, 134)}
SNAP = 18 * 60
FROZEN = {
    "v21_oracle": "88ff8313aed8c4ddb8f1bfdf633b34c5955bbf3db78b170d7cd59933694358bb",
    "v2_agenda": "b1c855c1", "v2_world": "acfadd61", "v2_manifest": "a394fe5e", "v2_oracle": "34720168",
}


def read(path):
    return json.loads(path.read_bytes().decode("utf-8"))


@pytest.fixture(scope="module")
def v21():
    return read(V21_DIR / "oracle.json")


@pytest.fixture(scope="module")
def v22():
    return read(V22_DIR / "oracle.json")


@pytest.fixture(scope="module")
def ctx():
    inputs, world, _ = v2_2._build_world("nh-small", DEFAULT_SEED)
    return Context(world, SNAP, inputs.calendar.last_day)


# --------------------------------------------------------------------------------------------- imutabilidade e reuso
def test_v21_artifacts_are_byte_identical_to_the_frozen_hash_and_regenerate_identically():
    assert sha256_bytes((V21_DIR / "oracle.json").read_bytes()) == FROZEN["v21_oracle"]
    _, files = v2_1.generate_oracle_v2_1("nh-small", DEFAULT_SEED, SCENARIOS)
    for name, data in files.items():
        assert (V21_DIR / name).read_bytes() == data, name


def test_v2_agenda_world_manifest_and_oracle_are_untouched():
    for name, prefix in (("public_agenda.json", FROZEN["v2_agenda"]), ("world.json", FROZEN["v2_world"]),
                         ("manifest.json", FROZEN["v2_manifest"]), ("oracle.json", FROZEN["v2_oracle"])):
        assert sha256_bytes((SCENARIOS / EXEC_ID / name).read_bytes()).startswith(prefix), name


def test_v22_committed_artifacts_are_deterministic_and_reuse_v2_by_reference():
    name, files = v2_2.generate_oracle_v2_2("nh-small", DEFAULT_SEED, SCENARIOS, V21_DIR)
    assert name == f"{EXEC_ID}-oracle-v2.2"
    for file, data in files.items():
        assert (V22_DIR / file).read_bytes() == data, file
    manifest = read(V22_DIR / "manifest_v2_2.json")
    assert manifest["reuses_by_reference"] == read(V21_DIR / "manifest_v2_1.json")["reuses_by_reference"]
    assert manifest["derived_from"]["oracle.json"] == FROZEN["v21_oracle"]
    assert manifest["artifacts"]["oracle.json"] == sha256_bytes((V22_DIR / "oracle.json").read_bytes())


def test_v22_refuses_to_derive_from_a_v21_that_differs_from_the_committed(tmp_path):
    copy_dir = tmp_path / "v21"
    shutil.copytree(V21_DIR, copy_dir)
    (copy_dir / "oracle.json").write_bytes((copy_dir / "oracle.json").read_bytes() + b" ")
    with pytest.raises(RuntimeError, match="regenerado do v2.1 difere"):
        v2_2.generate_oracle_v2_2("nh-small", DEFAULT_SEED, SCENARIOS, copy_dir)


def test_v22_reuse_is_refused_when_the_frozen_v2_artifacts_do_not_match(tmp_path):
    target = tmp_path / EXEC_ID
    shutil.copytree(SCENARIOS / EXEC_ID, target)
    manifest = read(target / "manifest.json")
    manifest["artifacts"]["world.json"] = "0" * 64
    (target / "manifest.json").write_bytes(canonical_bytes(manifest))
    with pytest.raises(RuntimeError, match="regenerado difere do v2 congelado"):
        v2_2.generate_oracle_v2_2("nh-small", DEFAULT_SEED, tmp_path, V21_DIR)


# --------------------------------------------------------------------------------------------- TA-1 sobre os dados reais
def test_ta1_positive_control_finds_exactly_the_known_regression_set_in_v21(v21, ctx):
    found = v2_2.violations(v21["expectations"], ctx)
    assert {v["exp_id"] for v in found} == set(REGRESSION)
    assert all(v["kind"] == "account_status" and v["effective"] > v["observation"] for v in found)


def test_v22_satisfies_ta1_everywhere(v22, ctx):
    assert v2_2.violations(v22["expectations"], ctx) == []


def test_only_the_five_expectations_change_and_only_in_day_and_annotation(v21, v22):
    old = {e["exp_id"]: e for e in v21["expectations"]}
    new = {e["exp_id"]: e for e in v22["expectations"]}
    assert list(old) == list(new)                                               # ids e ordem preservados
    changed = {k for k in old if old[k] != new[k]}
    assert changed == set(REGRESSION)
    for exp_id, (before, after) in REGRESSION.items():
        a, b = old[exp_id], dict(new[exp_id])
        assert (a["day"], b["day"]) == (before, after) and b["policy"] == "pago" == a["policy"]
        assert b.pop("retimed")["rule"] == "TA-1"
        b["day"] = before
        assert b == a                                                            # nada alem de `day` e `retimed`


def test_conservation_every_v21_expectation_has_an_explicit_disposition(v21, v22):
    report = v22["temporal_admissibility"]["conservation"]
    assert report["total_v2_1"] == report["total_v2_2"] == len(v21["expectations"]) == 13074
    assert report["dispositions"] == {"PRE_EXISTING_NOT_COLLECTED": 28, "RETIMED": 5, "UNCHANGED": 13041}
    assert report["sum_check"] is True and sum(report["dispositions"].values()) == 13074
    assert report["pre_existing_not_collected_by_reason"] == {"NOT_REPRESENTABLE": 2, "SUPERSEDED_SAME_DAY": 26}
    assert set(report["explicit"]) == set(REGRESSION) and set(report["explicit"].values()) == {"RETIMED"}
    assert report["ids_sha256"] == sha256_bytes(canonical_bytes([e["exp_id"] for e in v22["expectations"]]))
    manifest = read(V22_DIR / "manifest_v2_2.json")
    assert manifest["transformation"]["retimed"] == sorted(REGRESSION) and manifest["transformation"]["ta1_not_collected"] == []


def test_claim_version_and_rules_are_the_v22_own(v22):
    manifest = read(V22_DIR / "manifest_v2_2.json")
    assert v22["oracle_version"] == manifest["oracle_version"] == "v2.2" and v22["schema"] == "sim.oracle.v2.2"
    assert v22["claim"] == manifest["claim"] == v2_2.CLAIM_V2_2
    assert "VERIFIED" not in v22["claim"] and "VERIFIED" not in manifest["claim"]
    assert v22["rule_versions"]["temporal_admissibility"] == "temporal_admissibility_v1"
    assert v22["temporal_admissibility"]["registry_sha256"] == v2_2.registry_sha256()


def test_the_generator_never_names_an_expectation_id():
    source = (SIM_ROOT / "oracle" / "v2_2.py").read_text(encoding="utf-8")
    assert not re.search(r"E-\d{6}", source)
    tree = ast.parse(source)
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [s for s in literals if re.fullmatch(r"[EKDP]-\d{6}", s)]


def test_registry_covers_every_kind_and_fails_closed_when_incomplete(monkeypatch):
    assert set(v2_2.RULES) == set(v2_1._BY_KIND)
    v2_2.check_registry_complete()
    broken = dict(v2_2.RULES)
    broken.pop("account_status")
    monkeypatch.setattr(v2_2, "RULES", broken)
    with pytest.raises(TemporalAdmissibilityError, match="registro TA-1 incompleto"):
        v2_2.check_registry_complete()


# --------------------------------------------------------------------------------------------- o plano de coleta
def test_plan_has_the_same_items_and_only_ten_items_change_their_satisfies_set(v21, v22):
    ops = load_agenda_ops()
    p21 = compile_plan(v21, ops)
    p22 = compile_plan(v22, ops)

    def key(item):
        return (item["route"], item["path"], json.dumps(item["bind"], sort_keys=True), json.dumps(item["query"], sort_keys=True),
                item["safe_at"], item["day"], item["at"], tuple(item["requires"]), item["channel"])

    k21 = {key(i): i for i in p21["items"]}
    k22 = {key(i): i for i in p22["items"]}
    assert set(k21) == set(k22) and len(p21["items"]) == len(p22["items"])
    differing = [k for k in k21 if k21[k]["satisfies"] != k22[k]["satisfies"]]
    assert len(differing) == 10
    moved = {e for k in differing for e in set(k21[k]["satisfies"]) ^ set(k22[k]["satisfies"])}
    assert moved == set(REGRESSION)


# --------------------------------------------------------------------------------------------- propriedades (mundo sintetico)
def make_title(status_changes, issue=(0, 540), due=5):
    title = Title(ref="R-T", customer=None, order_no=1, issue=issue, valor=100, method="boleto", opening=False, plan={})
    title.venc_log = [(issue, due)]
    title.status_log = [(issue, "aberto"), *status_changes]
    return title


def synth(title, last=30):
    return Context(SimpleNamespace(titles=[title], governed=[]), SNAP, last)


def status_item(exp_id, day, policy, **extra):
    return {"exp_id": exp_id, "kind": "account_status", "subject": {"rec_ref": "R-T"}, "day": day, "policy": policy,
            "semantics": "fact", "safe_at": "after_daily_barrier", "evidence_class": "PRODUCT_FACT", **extra}


@pytest.mark.parametrize("minute,retimed", [(17 * 60 + 59, False), (18 * 60, False), (18 * 60 + 1, True),
                                            (23 * 60 + 59, True), (0, False)])
def test_boundary_of_the_snapshot_is_inclusive_and_late_transitions_move_to_the_next_day(minute, retimed):
    title = make_title([((8, minute), "pago")])
    items, report = v2_2.apply_ta1([status_item("X-1", 8, "pago")], synth(title))
    assert (items[0]["day"], "retimed" in items[0]) == ((9 if retimed else 8), retimed)
    assert report["dispositions"] == {("RETIMED" if retimed else "UNCHANGED"): 1}


def test_a_late_transition_on_the_last_day_has_no_snapshot_left_and_generation_fails_closed():
    title = make_title([((30, 20 * 60), "pago")])
    with pytest.raises(TemporalAdmissibilityError, match="nao ha snapshot posterior"):
        v2_2.apply_ta1([status_item("X-1", 30, "pago")], synth(title, last=30))


def test_a_transition_superseded_before_the_next_snapshot_becomes_not_collected_with_a_reason_code():
    title = make_title([((8, 20 * 60), "atrasado"), ((9, 10 * 60), "pago")])
    items, report = v2_2.apply_ta1([status_item("X-1", 8, "atrasado")], synth(title))
    assert items[0]["safe_at"] == "not_collected" and items[0]["day"] == 8
    assert items[0]["ta1"]["reason_code"] == "SUPERSEDED_BEFORE_NEXT_SAFE_AT" and items[0]["ta1"]["superseded_by"]["status"] == "pago"
    assert report["dispositions"] == {"NOT_COLLECTED_TA1": 1}


def test_an_unprovable_violation_fails_closed_instead_of_dropping_the_expectation(monkeypatch):
    title = make_title([((8, 20 * 60), "atrasado")])
    rule = v2_2.RULES["account_status"]
    bad = v2_2.Rule(rule.name, rule.effective, lambda c, i, d: "valor-que-nao-existe", rule.superseded_by)
    monkeypatch.setitem(v2_2.RULES, "account_status", bad)
    with pytest.raises(TemporalAdmissibilityError, match="nao demonstravel"):
        v2_2.apply_ta1([status_item("X-1", 8, "atrasado")], synth(title))


def test_a_kind_without_a_retiming_rule_that_violates_ta1_fails_closed():
    title = make_title([((8, 20 * 60), "pago")])
    item = {"exp_id": "X-1", "kind": "account_event_count", "subject": {"rec_ref": "R-T"}, "day": 8,
            "policy": {"status_changed_to_pago": 1}, "semantics": "fact", "safe_at": "after_daily_barrier",
            "evidence_class": "PRODUCT_FACT"}
    with pytest.raises(TemporalAdmissibilityError, match="nao tem regra de retemporizacao"):
        v2_2.apply_ta1([item], synth(title))


def test_retiming_the_superseding_item_restores_the_earlier_same_day_item_explicitly():
    title = make_title([((8, 5), "atrasado"), ((8, 23 * 60 + 59), "pago")])
    early = status_item("X-1", 8, "atrasado", safe_at="not_collected", superseded_same_day=True)
    late = status_item("X-2", 8, "pago")
    items, report = v2_2.apply_ta1([early, late], synth(title))
    assert (items[0]["safe_at"], "superseded_same_day" in items[0]) == ("after_daily_barrier", False)
    assert items[1]["day"] == 9 and report["dispositions"] == {"RESTORED_COLLECTIBLE": 1, "RETIMED": 1}
    assert v2_2.violations(items, synth(title)) == []


def test_an_item_that_changes_without_a_disposition_breaks_conservation():
    before = [status_item("X-1", 8, "pago")]
    after = [dict(before[0], day=9)]
    with pytest.raises(TemporalAdmissibilityError, match="mudou sem disposicao explicita"):
        v2_2.conservation_report(before, after, {})


# --------------------------------------------------------------------------------------------- laco semantico (defeito era da expectativa)
def evaluate_account_status(exp_day, observe_day, title):
    """Produto-duplo: o GET /accounts/{id} do snapshot de `observe_day` mostra o estado em (dia, 18:00)."""
    exp = status_item("X-1", exp_day, "pago", evidence_class="PRODUCT_FACT")
    plan = {"clock": {"d0": "2026-01-05", "utc_offset_minutes": -180},
            "items": [{"item_id": "I-1", "satisfies": ["X-1"], "safe_at": "after_daily_barrier", "day": exp_day}]}
    body = {"status": title.status_at((observe_day, SNAP))}
    collected = [{"kind": "collected", "item_id": "I-1", "status": 200, "body": body}]
    oracle = {"oracle_version": "t", "execution_scenario_id": "t", "expectations": [exp]}
    return evaluate(oracle, plan, collected, [], {"refs": {}}, {})["results"][0]["class"]


def test_the_late_payment_is_a_false_divergence_under_v21_timing_and_correct_under_v22_timing():
    title = make_title([((8, 23 * 60 + 59), "pago")])
    assert evaluate_account_status(8, 8, title) == "DIVERGENCE"          # expectativa v2.1: 'pago' em D, snapshot D 18:00
    items, _ = v2_2.apply_ta1([status_item("X-1", 8, "pago")], synth(title))
    assert evaluate_account_status(items[0]["day"], items[0]["day"], title) == "CORRECT"


def test_evaluator_marks_a_ta1_not_collected_item_as_a_known_limit_never_correct_never_harness_error():
    exp = status_item("X-1", 8, "atrasado", safe_at="not_collected",
                      ta1={"disposition": "NOT_COLLECTED", "reason_code": "SUPERSEDED_BEFORE_NEXT_SAFE_AT"})
    oracle = {"oracle_version": "v2.2", "execution_scenario_id": "t", "expectations": [exp]}
    plan = {"clock": {"d0": "2026-01-05", "utc_offset_minutes": -180}, "items": []}
    result = evaluate(oracle, plan, [], [], {"refs": {}}, {})
    assert result["results"][0]["class"] == "KNOWN_LIMIT" and result["run_valid"] is True


def test_copy_of_the_v22_oracle_in_memory_is_what_the_evaluator_reads_without_changes(v22):
    assert copy.deepcopy(v22) == v22 and all("exp_id" in e for e in v22["expectations"])


def test_next_observable_day_boundary_is_inclusive_at_the_snapshot():
    context = SimpleNamespace(snapshot=SNAP)
    assert v2_2._next_observable_day(context, 9 * 1440 + SNAP) == 9              # 18:00 ainda e observavel no mesmo dia
    assert v2_2._next_observable_day(context, 9 * 1440 + SNAP + 1) == 10
    assert v2_2._next_observable_day(context, 9 * 1440 + 23 * 60 + 59) == 10
