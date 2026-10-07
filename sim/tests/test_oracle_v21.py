"""
SIM-1.5 -- gates estaticos do Oracle v2.1 (caminho A): revisao SOMENTE do
Oracle, agenda/mundo v2 reutilizados por referencia (byte-identidade PROVADA,
nunca presumida), imutabilidade dos artefatos v1/v2, taxonomia de claims,
projecao de `knowledge_transition` e fronteira exata do candidato.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess

import pytest

from sim.generator.canonical import canonical_bytes
from sim.generator.cli import DEFAULT_SEED
from sim.generator.world import Title
from sim.oracle import rules
from sim.oracle import v2_1
from sim.oracle.v2_1 import ALERT_STATES
from sim.oracle.v2_1 import CLAIM_V2_1
from sim.oracle.v2_1 import EVIDENCE_CLASSES
from sim.oracle.v2_1 import KIND_SOURCES_V2_1
from sim.oracle.v2_1 import REPLACED_KINDS
from sim.oracle.v2_1 import SAFE_AT
from sim.oracle.v2_1 import SEVERITY_BY_STATE
from sim.oracle.v2_1 import generate_oracle_v2_1
from sim.oracle.v2_1 import knowledge_projection
from sim.tests.conftest import SCENARIOS
from sim.tests.conftest import SIM_ROOT

REPO = SIM_ROOT.parent
EXEC_ID = "nh-small-v2-s340001"
V21_DIR = SIM_ROOT / "oracle" / "artifacts" / f"{EXEC_ID}-oracle-v2.1"
ADDED_KEYS = {"evidence_class", "safe_at", "requires", "derivation", "superseded_same_day"}


def read(path):
    return json.loads(path.read_bytes().decode("utf-8"))


@pytest.fixture(scope="module")
def v21():
    return read(V21_DIR / "oracle.json")


@pytest.fixture(scope="module")
def v2():
    return read(SCENARIOS / EXEC_ID / "oracle.json")


# --------------------------------------------------------------------------
# reuso por referencia e imutabilidade (A-4: byte-identidade e PROPRIEDADE A PROVAR)
# --------------------------------------------------------------------------
def test_committed_v21_artifacts_are_deterministic_and_reproducible():
    directory, files = generate_oracle_v2_1("nh-small", DEFAULT_SEED, SCENARIOS)
    assert directory == V21_DIR.name
    for name, data in files.items():
        assert (V21_DIR / name).read_bytes() == data, name


def test_v21_reuses_the_v2_agenda_and_world_by_reference_with_proven_hashes():
    manifest = read(V21_DIR / "manifest_v2_1.json")
    v2_manifest = read(SCENARIOS / EXEC_ID / "manifest.json")
    reuse = manifest["reuses_by_reference"]
    for name in ("public_agenda.json", "world.json"):
        data = (SCENARIOS / EXEC_ID / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == reuse[name] == v2_manifest["artifacts"][name], name
    assert hashlib.sha256((SCENARIOS / EXEC_ID / "manifest.json").read_bytes()).hexdigest() == reuse["manifest.json"]
    assert manifest["execution_scenario_id"] == EXEC_ID and manifest["oracle_version"] == "v2.1"
    assert manifest["artifacts"] == {"oracle.json": hashlib.sha256((V21_DIR / "oracle.json").read_bytes()).hexdigest()}
    assert not (V21_DIR / "public_agenda.json").exists() and not (V21_DIR / "world.json").exists()   # sem copias
    assert reuse["oracle.json (v2, superseded for evaluation)"] == v2_manifest["artifacts"]["oracle.json"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git indisponivel")
def test_v1_and_v2_scenario_artifacts_are_untouched_in_the_working_tree():
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", "sim/scenarios/nh-small-s340001", "sim/scenarios/nh-standard-s340001",
         "sim/scenarios/nh-small-v2-s340001", "sim/scenarios/nh-standard-v2-s340001"],
        cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == ""


def test_v2_oracle_is_still_the_frozen_artifact(v2):
    manifest = read(SCENARIOS / EXEC_ID / "manifest.json")
    assert hashlib.sha256((SCENARIOS / EXEC_ID / "oracle.json").read_bytes()).hexdigest() == \
        manifest["artifacts"]["oracle.json"]
    assert "oracle_version" not in v2 and v2["claim"].startswith("SIMULATION VERIFIED")        # v2 inalterado


# --------------------------------------------------------------------------
# v2.1 = v2 + anotacoes; so dois kinds substituidos
# --------------------------------------------------------------------------
def test_unchanged_expectations_are_the_v2_expectations_plus_annotations(v21, v2):
    by_id = {e["exp_id"]: e for e in v21["expectations"]}
    kept = 0
    for old in v2["expectations"]:
        if old["kind"] in REPLACED_KINDS:
            assert old["exp_id"] not in by_id
            continue
        new = by_id[old["exp_id"]]
        assert {k: v for k, v in new.items() if k not in ADDED_KEYS} == old, old["exp_id"]
        kept += 1
    replaced = sum(1 for e in v2["expectations"] if e["kind"] in REPLACED_KINDS)
    assert len(v21["expectations"]) == len(v2["expectations"]) - replaced + replaced + 1       # +1 provenance_aggregate
    assert kept == len(v2["expectations"]) - replaced


def test_replacements_and_new_kinds(v21, v2):
    kinds = {e["kind"] for e in v21["expectations"]}
    assert "vencimento_change_count" not in kinds and "driver_due_date_change_accepted_count" in kinds
    assert "provenance_aggregate" in kinds
    old = {e["subject"]["rec_ref"]: e["policy"] for e in v2["expectations"] if e["kind"] == "vencimento_change_count"}
    new = {e["subject"]["rec_ref"]: e["policy"] for e in v21["expectations"]
           if e["kind"] == "driver_due_date_change_accepted_count"}
    assert new == old                                                  # mesma contagem, outra semantica/classe
    assert sum(1 for e in v21["expectations"] if e["kind"] == "knowledge_transition") == \
        sum(1 for e in v2["expectations"] if e["kind"] == "knowledge_transition")


# --------------------------------------------------------------------------
# taxonomia de claims e momentos seguros
# --------------------------------------------------------------------------
def test_every_expectation_has_a_valid_class_and_safe_at(v21):
    for exp in v21["expectations"]:
        assert exp["evidence_class"] in EVIDENCE_CLASSES and exp["safe_at"] in SAFE_AT, exp["exp_id"]
        if exp["safe_at"] == "pre_slot":
            assert "at" in exp
    assert v21["evidence_classes"] == list(EVIDENCE_CLASSES) and v21["safe_at_values"] == list(SAFE_AT)


def test_class_to_kind_mapping_never_promotes_driver_evidence_to_product_fact(v21):
    by_kind: dict = {}
    for exp in v21["expectations"]:
        by_kind.setdefault(exp["kind"], set()).add((exp["evidence_class"], exp["safe_at"]))
    driver = {"nba_decision", "nba_applied_rules", "governed_execution", "authority_violation_rejected",
              "http_status", "driver_due_date_change_accepted_count"}
    for kind in driver:
        assert {c for c, _ in by_kind[kind]} == {"DRIVER_EXECUTION_EVIDENCE"}, kind
    assert {c for c, _ in by_kind["provenance_present"]} == {"EVALUATOR_DERIVATION"}
    assert {c for c, _ in by_kind["provenance_aggregate"]} == {"PRODUCT_FACT"}
    assert by_kind["not_representable"] == {("HARNESS_FACT", "not_collected")}
    for kind, pairs in by_kind.items():
        if kind not in driver | {"provenance_present", "not_representable"}:
            assert {c for c, _ in pairs} == {"PRODUCT_FACT"}, kind
    assert {s for _, s in by_kind["mark_overdue_eligible"] | by_kind["escalation_eligible"]} == {"pre_slot"}
    assert {s for _, s in by_kind["nba_decision"]} == {"in_slot"}


def test_floor_gated_expectations_require_the_floor_barrier(v21):
    floor_day = v21["evidence_floor"]["day"]
    gated = [e for e in v21["expectations"] if e["kind"] in ("observed_fact_count", "observed_fact_links_event")]
    assert gated and all(e["requires"] == ["after_floor_barrier"] and e["day"] >= floor_day for e in gated)
    present = [e for e in v21["expectations"] if e["kind"] == "provenance_present"]
    assert all(e["derivation"] == "exhaustion" and "provenance_aggregate" in e["requires"] for e in present)


def test_claim_and_sources_reflect_the_discovered_contract(v21):
    assert v21["claim"] == CLAIM_V2_1 and "SIMULATION VERIFIED" not in v21["claim"]
    assert "OPERATIONALLY OBSERVED" in v21["claim"] and "Never" in v21["claim"]
    manifest = (V21_DIR / "manifest_v2_1.json").read_text(encoding="utf-8")
    assert "SIMULATION VERIFIED" not in manifest
    sources = v21["kind_sources"]
    assert sources == KIND_SOURCES_V2_1
    assert "/nba-policy" not in json.dumps(sources) and "GET /memory " not in json.dumps(sources)
    assert "/memories" in sources["behavior_pattern_present"] and "/brain/" in sources["knowledge_transition"]
    assert "never re-read" in sources["nba_decision"] and "not a product count" in \
        sources["driver_due_date_change_accepted_count"]
    assert v21["oracle_version"] == "v2.1" and v21["schema"] == "sim.oracle.v2.1"


def test_account_status_same_day_supersession_is_only_a_collection_limit(v21):
    marked = [e for e in v21["expectations"] if e.get("superseded_same_day")]
    assert marked and all(e["kind"] == "account_status" and e["safe_at"] == "not_collected" for e in marked)
    last: dict = {}
    for exp in v21["expectations"]:
        if exp["kind"] == "account_status":
            last[(exp["subject"]["rec_ref"], exp["day"])] = exp
    assert all(e["safe_at"] == "after_daily_barrier" for e in last.values() if not e.get("superseded_same_day"))


# --------------------------------------------------------------------------
# knowledge_transition projetado
# --------------------------------------------------------------------------
def make_title(issue, due, status_changes=(), due_changes=(), status="aberto"):
    title = Title(ref="R-T", customer=None, order_no=1, issue=issue, valor=100, method="boleto", opening=False,
                  plan={})
    title.venc_log = [(issue, due), *due_changes]
    title.status_log = [(issue, status), *status_changes]
    return title


SNAP = 18 * 60


def test_projection_follows_alert_states_and_never_asserts_open_or_paid():
    title = make_title((0, 540), due=2)                       # vence no dia 2, nunca pago
    policy = knowledge_projection(title, 8, SNAP)
    assert [(r["day"], r["state"]) for r in policy["rows"]] == [
        (0, "due_soon"), (2, "due_today"), (3, "overdue"), (8, "overdue_alert")]
    assert policy["severities"] == ["info", "medium", "high", "critical"]
    assert {r["state"] for r in policy["rows"]} <= set(ALERT_STATES)
    assert all(state not in ("open", "paid") for r in policy["rows"] for state in [r["state"]])
    assert policy["observation_model"] == "end_of_day" and policy["closure"] == "GET /accounts/{id}"


def test_projection_open_far_future_has_no_rows_and_paid_closes_without_a_row():
    far = make_title((0, 540), due=40)
    assert knowledge_projection(far, 3, SNAP)["rows"] == []
    paid = make_title((0, 540), due=20, status_changes=[((21, 600), "pago")])
    policy = knowledge_projection(paid, 21, SNAP)
    assert [r["state"] for r in policy["rows"]] == ["due_soon", "due_today"]          # sem linha `paid`/`overdue`
    assert policy["intraday_allowance"] == {"21": ["high"]}      # `overdue` transitorio possivel antes do pagamento


def test_projection_reemits_the_same_state_when_the_due_date_changes():
    title = make_title((0, 540), due=3, due_changes=[((1, 630), 10)])
    policy = knowledge_projection(title, 1, SNAP)
    assert [(r["day"], r["severity"]) for r in policy["rows"]] == [(0, "info"), (1, "info")]
    assert policy["intraday_allowance"] == {"1": ["info"]}


def test_projection_ignores_a_title_issued_after_the_snapshot():
    title = make_title((2, 1200), due=1)
    policy = knowledge_projection(title, 4, SNAP)
    assert [r["day"] for r in policy["rows"]][:1] == [3]            # nenhuma linha de fim de dia no dia 2


def test_projection_matches_the_product_rule_replica_for_every_day():
    title = make_title((0, 540), due=5)
    for day in range(0, 12):
        state = rules.lifecycle_state("aberto", 5, day)
        rows = [r for r in knowledge_projection(title, day, SNAP)["rows"] if r["day"] == day]
        assert bool(rows) == (state in ALERT_STATES and not any(
            rules.lifecycle_state("aberto", 5, d) == state for d in range(0, day)))
        if rows:
            assert rows[0]["severity"] == SEVERITY_BY_STATE[state]


# --------------------------------------------------------------------------
# fronteira EXATA do candidato SIM-1.5
# --------------------------------------------------------------------------
ALLOWED = [
    r"^sim/driver/[^/]+$", r"^sim/collector/[^/]+$", r"^sim/evaluator/[^/]+$",
    r"^sim/oracle/(v2_1|collection_plan)\.py$",
    r"^sim/oracle/artifacts/nh-small-v2-s340001-oracle-v2\.1/(oracle|manifest_v2_1)\.json$",
    r"^sim/stack/(docker-compose\.sim\.driver\.yml|driver\.Dockerfile|collector\.Dockerfile|DRIVER_LAB\.md)$",
    r"^sim/stack/lab/(driver_lab|run_orchestrator)\.py$",
    r"^sim/live/(container/)?[^/]+$",                  # SIM-1.5A: harness live versionado
    r"^sim/tests/[^/]+$",
]
HISTORICAL = (".claude/", "Claude outputs/", "backend/DW3_patch_review_v2.patch",
              "backend/scripts/reset_test_password.py", "backend/tests/test_value33a_",
              "backend/tests/test_value33b_")


@pytest.mark.skipif(shutil.which("git") is None, reason="git indisponivel")
def test_candidate_file_boundary_is_exactly_the_authorized_set():
    """O candidato e ADITIVO: nenhum arquivo rastreado e modificado/removido/renomeado. Os arquivos novos podem estar
    nao rastreados OU adicionados ao indice (`A`, EXACT STAGING); em ambos os casos precisam estar na lista autorizada."""
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO,
                            capture_output=True, text=True).stdout.splitlines()
    not_additions = [line for line in status if line[0] != "A"]            # `A?` = adicionado ao indice (pode ter edicao nao staged)
    assert not_additions == [], "alteracao em arquivo rastreado (o SIM-1.5 e 100% aditivo)"
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO,
                               capture_output=True, text=True).stdout.splitlines()
    staged_added = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=A"], cwd=REPO,
                                  capture_output=True, text=True).stdout.splitlines()
    candidate = [p for p in [*untracked, *staged_added] if not p.startswith(HISTORICAL)]
    stray = [p for p in candidate if not any(re.match(rule, p) for rule in ALLOWED)]
    assert stray == [], stray
    assert not any(p.startswith(("backend/", "frontend/")) for p in candidate)
    assert "sim/stack/lab/gates.py" not in candidate and not [p for p in candidate if p.endswith("__pycache__")]


def test_candidate_text_files_are_lf_without_trailing_whitespace():
    """Equivalente local de `git diff --check` para arquivos ainda nao rastreados."""
    roots = [SIM_ROOT / "driver", SIM_ROOT / "collector", SIM_ROOT / "evaluator"]
    files = [p for root in roots for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    files += [SIM_ROOT / "oracle" / "v2_1.py", SIM_ROOT / "oracle" / "collection_plan.py",
              SIM_ROOT / "stack" / "docker-compose.sim.driver.yml", SIM_ROOT / "stack" / "driver.Dockerfile",
              SIM_ROOT / "stack" / "collector.Dockerfile", SIM_ROOT / "stack" / "DRIVER_LAB.md",
              SIM_ROOT / "stack" / "lab" / "driver_lab.py", SIM_ROOT / "stack" / "lab" / "run_orchestrator.py",
              SIM_ROOT / "tests" / "sim14_frozen_hashes.json"]
    files += [p for p in (SIM_ROOT / "tests").glob("*.py")]
    for path in files:
        data = path.read_bytes()
        if not data:                      # __init__.py vazio preexistente
            continue
        assert b"\r" not in data and data.endswith(b"\n") and not data.endswith(b"\n\n"), path.name
        for number, line in enumerate(data.decode("utf-8").split("\n"), start=1):
            assert line == line.rstrip(), f"{path.name}:{number} espaco no fim da linha"


def test_reuse_by_reference_is_refused_when_the_frozen_artifacts_do_not_match(tmp_path):
    """A byte-identidade e uma PROPRIEDADE A PROVAR: manifesto ou arquivo v2 adulterado => v2.1 nao e gerado."""
    import shutil

    source = SCENARIOS / EXEC_ID
    for case in ("manifest", "disk"):
        scenarios = tmp_path / case
        target = scenarios / EXEC_ID
        shutil.copytree(source, target)
        if case == "manifest":
            manifest = read(target / "manifest.json")
            manifest["artifacts"]["world.json"] = "0" * 64
            (target / "manifest.json").write_bytes(canonical_bytes(manifest))
            with pytest.raises(RuntimeError, match="regenerado difere do v2 congelado"):
                generate_oracle_v2_1("nh-small", DEFAULT_SEED, scenarios)
        else:
            agenda = (target / "public_agenda.json").read_bytes()
            (target / "public_agenda.json").write_bytes(agenda + b" ")
            with pytest.raises(RuntimeError, match="em disco difere do manifesto v2"):
                generate_oracle_v2_1("nh-small", DEFAULT_SEED, scenarios)
