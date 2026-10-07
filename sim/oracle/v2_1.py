"""
Oracle v2.1 (SIM-1.5 Design Freeze V1 + V1.1, D-1.5.2-1: caminho A).

REVISAO SOMENTE DO ORACLE. A agenda publica e o mundo v2 sao reutilizados
POR REFERENCIA (mesmos bytes, provados por sha256 contra o manifesto v2); o
identificador de execucao continua `nh-small-v2-s340001` e
`oracle_version = "v2.1"` identifica a revisao epistemologica. Nenhum byte
dos artefatos v2 e alterado.

O que muda em relacao ao Oracle v2:

* `knowledge_transition` projetado sobre o contrato publico observavel de
  `GET /brain/` (somente ALERT_STATES, state -> severity, reemissao quando o
  vencimento muda e o estado se mantem, modelo de observacao fim-de-dia com
  tolerancia explicita para amostragem intradia). Nunca afirma `open`/`paid`
  como Knowledge; o encerramento vem de `GET /accounts/{id}`.
* `vencimento_change_count` deixa de ser claim de produto e vira
  `driver_due_date_change_accepted_count` (DRIVER EXECUTION EVIDENCE).
* cada expectativa recebe `evidence_class` e `safe_at` (momento seguro de
  coleta, executavel pelo Evidence Collector).
* `KIND_SOURCES` com os caminhos reais descobertos no SIM-1.5.
* `provenance_present` por sujeito vira EVALUATOR DERIVATION por exaustao;
  `provenance_aggregate` e o PRODUCT FACT (CLI read-only do produto).
"""

from __future__ import annotations

from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.generator.canonical import read_json
from sim.oracle import rules

ORACLE_VERSION = "v2.1"
ORACLE_SCHEMA = "sim.oracle.v2.1"
GENERATOR_VERSION = "sim-1.5.0"
CLAIM_V2_1 = (
    "SIMULATION RESULT over a known synthetic population, valid only after an "
    "evaluated run. Never OPERATIONALLY OBSERVED."
)

PRODUCT_FACT = "PRODUCT_FACT"
DRIVER_EVIDENCE = "DRIVER_EXECUTION_EVIDENCE"
EVALUATOR_DERIVATION = "EVALUATOR_DERIVATION"
HARNESS_FACT = "HARNESS_FACT"
EVIDENCE_CLASSES = (PRODUCT_FACT, DRIVER_EVIDENCE, EVALUATOR_DERIVATION, HARNESS_FACT)

SAFE_AT = (
    "pre_slot", "post_slot", "in_slot", "after_daily_barrier", "after_floor_barrier",
    "after_restart_barrier", "end_of_run", "not_collected",
)

ALERT_STATES = ("due_soon", "due_today", "overdue", "overdue_alert")
SEVERITY_BY_STATE = {
    "due_soon": "info",
    "due_today": "medium",
    "overdue": "high",
    "overdue_alert": "critical",
}

KIND_SOURCES_V2_1 = {
    "account_status": "GET /accounts/{id}",
    "lifecycle_state": "GET /accounts/{id} (receivable_lifecycle)",
    "knowledge_transition": "GET /brain/?account_id= (Knowledge receivable_lifecycle; severity rows, end-of-day model)",
    "classification": "GET /accounts/{id}/classification",
    "behavior_pattern_present": "GET /memories?scope_type=account&account_id=&memory_key=client_behavior_payment_pattern",
    "mark_overdue_eligible": "GET /recommendations/mark-overdue/accounts/{id}/episodes/{due}",
    "escalation_eligible": "GET /recommendations/human-escalation/accounts/{id}/episodes/{due}",
    "nba_decision": "Driver response of consult_nba (GET /recommendations/next-best-action/...; the GET persists a snapshot, never re-read)",
    "nba_applied_rules": "Driver response of consult_nba (never re-read)",
    "governed_request_exists": "GET /approvals (paginated, after_id)",
    "governed_decision": "GET /approvals/{id}",
    "governed_execution": "HTTP response recorded by the Driver",
    "effective_expiry": "GET /approvals/{id} (expires_at; status may remain pending)",
    "authority_violation_rejected": "HTTP response recorded by the Driver (403/409)",
    "escalation_work_item_exists": "GET /work-items/{id}",
    "driver_due_date_change_accepted_count": "Driver evidence: accepted PUT /accounts/{id} responses (not a product count)",
    "account_event_count": "GET /outcomes/accounts/{id}/episodes/{due} (evidence)",
    "business_effect_verified": "GET /outcomes/accounts/{id}/episodes/{due} (effect_verification.result)",
    "observed_fact_count": "GET /work-items/{id}/escalation-observations",
    "observed_fact_links_event": "GET /work-items/{id}/escalation-observations",
    "provenance_aggregate": "docker exec scripts/evidence_provenance_report.py report (product read-only CLI)",
    "provenance_present": "EVALUATOR DERIVATION by exhaustion over provenance_aggregate + collected observed facts",
    "human_assessment_count": "GET /work-items/{id}/escalation-observations",
    "must_not_exist": "GET /work-items?scope_type=account&account_id= + GET /outcomes",
    "http_status": "HTTP response recorded by the Driver",
    "no_duplicate_effect": "GET /approvals + GET /work-items + GET /outcomes (end of run)",
    "not_representable": "(none: world-only fact)",
}

REPLACED_KINDS = frozenset({"knowledge_transition", "vencimento_change_count"})

# kind -> (evidence_class, safe_at). `account_status` e `lifecycle_state` etc.
# usam o instante de fim de dia (apos a barreira diaria).
_BY_KIND = {
    "account_status": (PRODUCT_FACT, "after_daily_barrier"),
    "lifecycle_state": (PRODUCT_FACT, "after_daily_barrier"),
    "classification": (PRODUCT_FACT, "after_daily_barrier"),
    "behavior_pattern_present": (PRODUCT_FACT, "after_daily_barrier"),
    "mark_overdue_eligible": (PRODUCT_FACT, "pre_slot"),
    "escalation_eligible": (PRODUCT_FACT, "pre_slot"),
    "nba_decision": (DRIVER_EVIDENCE, "in_slot"),
    "nba_applied_rules": (DRIVER_EVIDENCE, "in_slot"),
    "governed_request_exists": (PRODUCT_FACT, "after_daily_barrier"),
    "governed_decision": (PRODUCT_FACT, "after_daily_barrier"),
    "effective_expiry": (PRODUCT_FACT, "after_daily_barrier"),
    "governed_execution": (DRIVER_EVIDENCE, "in_slot"),
    "authority_violation_rejected": (DRIVER_EVIDENCE, "in_slot"),
    "http_status": (DRIVER_EVIDENCE, "in_slot"),
    "escalation_work_item_exists": (PRODUCT_FACT, "after_daily_barrier"),
    "human_assessment_count": (PRODUCT_FACT, "after_daily_barrier"),
    "observed_fact_count": (PRODUCT_FACT, "after_daily_barrier"),
    "observed_fact_links_event": (PRODUCT_FACT, "after_daily_barrier"),
    "account_event_count": (PRODUCT_FACT, "after_daily_barrier"),
    "business_effect_verified": (PRODUCT_FACT, "after_daily_barrier"),
    "provenance_present": (EVALUATOR_DERIVATION, "end_of_run"),
    "must_not_exist": (PRODUCT_FACT, "end_of_run"),
    "no_duplicate_effect": (PRODUCT_FACT, "end_of_run"),
    "knowledge_transition": (PRODUCT_FACT, "after_daily_barrier"),
    "driver_due_date_change_accepted_count": (DRIVER_EVIDENCE, "end_of_run"),
    "provenance_aggregate": (PRODUCT_FACT, "end_of_run"),
    "not_representable": (HARNESS_FACT, "not_collected"),
}
_REQUIRES_FLOOR = frozenset({"observed_fact_count", "observed_fact_links_event"})


def annotate(item: dict, floor_day: int) -> dict:
    """Acrescenta evidence_class/safe_at (e requires) sem tocar nos campos v2."""
    cls, safe = _BY_KIND[item["kind"]]
    out = dict(item)
    out["evidence_class"] = cls
    out["safe_at"] = safe
    if item["kind"] in _REQUIRES_FLOOR:
        out["requires"] = ["after_floor_barrier"]
        if item["day"] < floor_day:
            raise ValueError(f"expectativa {item['exp_id']} antes do floor")
    if item["kind"] == "provenance_present":
        out["derivation"] = "exhaustion"
        out["requires"] = ["provenance_aggregate", "after_floor_barrier"]
    if safe == "pre_slot" and "at" not in item:
        raise ValueError(f"pre_slot sem instante: {item['exp_id']}")
    return out


# ---------------------------------------------------------------------------
# knowledge_transition projetado (fim de dia + tolerancia intradia)
# ---------------------------------------------------------------------------
def _key(title, instant, day):
    status = title.status_at(instant)
    due = title.venc_at(instant)
    return (rules.lifecycle_state(status, due, day), due)


def day_keys(title, day: int) -> list:
    """Chaves (estado, vencimento) por intervalo do dia CIVIL inteiro."""
    if title.issue[0] > day:
        return []
    start = max((day, 0), title.issue)
    cuts = {at for at, _ in title.status_log if at[0] == day and at > start}
    cuts |= {at for at, _ in title.venc_log if at[0] == day and at > start}
    return [_key(title, point, day) for point in [start, *sorted(cuts)]]


def knowledge_projection(title, end_day: int, snapshot: int) -> dict:
    seen: set = set()
    rows: list = []
    allowance: dict = {}
    for day in range(title.issue[0], end_day + 1):
        keys = day_keys(title, day)
        if len({k for k in keys}) > 1:
            severities = sorted({SEVERITY_BY_STATE[s] for s, _ in keys if s in ALERT_STATES})
            if severities:
                allowance[str(day)] = severities
        if title.issue > (day, snapshot):
            continue
        end_key = _key(title, (day, snapshot), day)
        if end_key[0] in ALERT_STATES and end_key not in seen:
            rows.append({"day": day, "state": end_key[0], "severity": SEVERITY_BY_STATE[end_key[0]]})
        if end_key[0] in ALERT_STATES:
            seen.add(end_key)
    return {
        "observation_model": "end_of_day",
        "rows": rows,
        "severities": [row["severity"] for row in rows],
        "intraday_allowance": allowance,
        "closure": "GET /accounts/{id}",
    }


def build_items_v2_1(world, inputs, v2_items: list) -> list:
    """Transforma os itens do Oracle v2 (ja construidos) em itens v2.1."""
    snapshot = _snapshot_minute(inputs)
    floor_day = inputs.calendar.evidence_floor[0]
    by_ref = {title.ref: title for title in world.titles}
    out: list = []
    for item in v2_items:
        if item["kind"] in REPLACED_KINDS:
            continue
        out.append(annotate(item, floor_day))
    last = inputs.calendar.last_day
    knowledge = []
    for item in v2_items:
        if item["kind"] != "knowledge_transition":
            continue
        title = by_ref[item["subject"]["rec_ref"]]
        policy = knowledge_projection(title, item["day"], snapshot)
        knowledge.append(annotate({
            "kind": "knowledge_transition", "subject": item["subject"], "day": item["day"],
            "policy": policy, "semantics": "fact", "exp_id": "K-" + item["exp_id"][2:],
            "v2_exp_id": item["exp_id"],
        }, floor_day))
    driver = []
    for item in v2_items:
        if item["kind"] != "vencimento_change_count":
            continue
        driver.append(annotate({
            "kind": "driver_due_date_change_accepted_count", "subject": item["subject"],
            "day": item["day"], "policy": item["policy"], "semantics": "fact",
            "exp_id": "D-" + item["exp_id"][2:], "v2_exp_id": item["exp_id"],
        }, floor_day))
    aggregate = annotate({
        "kind": "provenance_aggregate", "subject": {"scope": "run"}, "day": last,
        "policy": {"legacy_without_provenance": 0, "integrity_failures": 0,
                   "with_provenance_equals_collected_observed_facts": True},
        "semantics": "fact", "exp_id": "P-000001",
    }, floor_day)
    return out + knowledge + driver + [aggregate]


def _snapshot_minute(inputs) -> int:
    from sim.generator.timeline import to_minutes

    return to_minutes(inputs.calendar.intraday["snapshot"])


def superseded_same_day(items: list) -> list:
    """Marca `account_status` anteriores a outro do mesmo (sujeito, dia): so o
    ultimo e verificavel ao fim do dia (nao e mudanca de claim, e coleta)."""
    last_seen: dict = {}
    for index, item in enumerate(items):
        if item["kind"] == "account_status":
            last_seen[(item["subject"]["rec_ref"], item["day"])] = index
    for index, item in enumerate(items):
        if item["kind"] == "account_status" and last_seen[(item["subject"]["rec_ref"], item["day"])] != index:
            item["safe_at"] = "not_collected"
            item["superseded_same_day"] = True
    return items


def generate_oracle_v2_1(variant: str, seed: int, scenarios_dir) -> tuple[str, dict]:
    """Gera em memoria o Oracle v2.1 e PROVA a reutilizacao por referencia.

    Retorna (nome_do_diretorio, {arquivo: bytes}). Levanta RuntimeError se a
    agenda ou o mundo regenerados diferirem dos bytes v2 congelados."""
    from pathlib import Path

    from sim import CLAIM  # noqa: F401  (documenta a origem da claim v2)
    from sim.generator.agenda import PUBLIC_OP_KEYS_V2
    from sim.generator.agenda import PUBLIC_OPS_V2
    from sim.generator.agenda import build_public_agenda
    from sim.generator.cases_v2 import InjectorV2
    from sim.generator.cases_v2 import inject_agenda_v2
    from sim.generator.cases_v2 import restart_in_flight_v2
    from sim.generator.cases_v2 import tag_cases_v2
    from sim.generator.cli import oracle_artifact
    from sim.generator.cli import world_artifact_v2
    from sim.generator.config import load_inputs_v2
    from sim.generator.world_v2 import SimulatorV2
    from sim.oracle.expectations_v2 import build_expectations_v2
    from sim.oracle.rules_v2 import RULE_VERSIONS_V2

    inputs = load_inputs_v2()
    spec = inputs.variant(variant)
    scenario_id = f"{variant}-v2-s{seed}"
    injector = InjectorV2(inputs, variant, seed)
    world = SimulatorV2(inputs, variant, seed, injector, scenario_id).run()
    agenda_cases = inject_agenda_v2(world, inputs, int(spec["min_case_instances"]))
    world.replayed = set(agenda_cases.get("C-ADV-2", []))
    world.duplicate_settled = set(agenda_cases.get("C-ADV-1", []) + agenda_cases.get("C-ADV-3", []))
    world.restart_in_flight = restart_in_flight_v2(world, inputs)
    v2_items, labels = build_expectations_v2(world, inputs)
    case_instances = tag_cases_v2(world, inputs, agenda_cases, labels)
    for case in inputs.cases["cases"]:
        case_instances.setdefault(case, [])

    # prova de reuso por referencia: agenda e mundo regenerados == bytes v2 congelados
    source = Path(scenarios_dir) / scenario_id
    frozen = read_json(source / "manifest.json")
    agenda = build_public_agenda(world, scenario_id, keys=PUBLIC_OP_KEYS_V2, allowed_ops=PUBLIC_OPS_V2)
    world_doc = world_artifact_v2(world, scenario_id)
    regenerated = {
        "public_agenda.json": sha256_bytes(canonical_bytes(agenda)),
        "world.json": sha256_bytes(canonical_bytes(world_doc)),
    }
    for name, digest in regenerated.items():
        if frozen["artifacts"][name] != digest:
            raise RuntimeError(f"{name} regenerado difere do v2 congelado: v2.1 nao pode reusar por referencia")
        if sha256_bytes((source / name).read_bytes()) != digest:
            raise RuntimeError(f"{name} em disco difere do manifesto v2")

    items = superseded_same_day(build_items_v2_1(world, inputs, v2_items))
    oracle = oracle_artifact(scenario_id, items, case_instances, inputs)
    oracle["rule_versions"] = {**RULE_VERSIONS_V2,
                               "knowledge_projection": "receivable_lifecycle_knowledge_v1(end_of_day+intraday_allowance)"}
    oracle["claim"] = CLAIM_V2_1
    oracle["oracle_version"] = ORACLE_VERSION
    oracle["schema"] = ORACLE_SCHEMA
    oracle["execution_scenario_id"] = scenario_id
    oracle["kind_sources"] = KIND_SOURCES_V2_1
    oracle["authority_chains"] = inputs.company["authority_chains"]
    oracle["reserved_kinds"] = {"work_outcome_evaluated": "RESERVED/GAP (no public exposure)"}
    oracle["out_of_scope"] = {"F1": "OUT/GAP (G-SIM-17)", "L3": "OUT (G-SIM-4)"}
    oracle["invalidation_rules"] = oracle["invalidation_rules"] + [
        "authority choreography violation outside C-AUTH-2 => scenario INVALID",
    ]
    oracle["evidence_classes"] = list(EVIDENCE_CLASSES)
    oracle["safe_at_values"] = list(SAFE_AT)
    oracle["claim_rules"] = [
        "an EVALUATOR_DERIVATION never promotes DRIVER_EXECUTION_EVIDENCE to PRODUCT_FACT",
        "open/paid are never asserted as Knowledge rows; closure comes from GET /accounts/{id}",
        "driver_due_date_change_accepted_count is not a product-observed count (PF-6)",
    ]
    oracle_bytes = canonical_bytes(oracle)
    manifest = {
        "artifact": "manifest_v2_1",
        "oracle_version": ORACLE_VERSION,
        "schema": ORACLE_SCHEMA,
        "execution_scenario_id": scenario_id,
        "generator_version": GENERATOR_VERSION,
        "claim": CLAIM_V2_1,
        "reuses_by_reference": {
            "directory": scenario_id,
            "public_agenda.json": frozen["artifacts"]["public_agenda.json"],
            "world.json": frozen["artifacts"]["world.json"],
            "manifest.json": sha256_bytes((source / "manifest.json").read_bytes()),
            "oracle.json (v2, superseded for evaluation)": frozen["artifacts"]["oracle.json"],
        },
        "artifacts": {"oracle.json": sha256_bytes(oracle_bytes)},
        "inputs": dict(sorted(inputs.hashes.items())),
        "expectations": len(items),
    }
    directory = f"{scenario_id}-oracle-{ORACLE_VERSION}"
    return directory, {"oracle.json": oracle_bytes, "manifest_v2_1.json": canonical_bytes(manifest)}


def default_artifacts_dir():
    """Os artefatos v2.1 ficam FORA de `sim/scenarios/` (essa pasta so contem cenarios
    congelados v1/v2 e seus gates globbam por ela)."""
    from pathlib import Path

    return Path(__file__).resolve().parent / "artifacts"


def write_oracle_v2_1(variant: str, seed: int, scenarios_dir, out_dir=None) -> "Path":
    """Grava `<out>/<scenario>-oracle-v2.1/{oracle.json, manifest_v2_1.json}` (nunca
    toca nos diretorios v1/v2)."""
    from pathlib import Path

    directory, files = generate_oracle_v2_1(variant, seed, scenarios_dir)
    target = Path(out_dir or default_artifacts_dir()) / directory
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (target / name).write_bytes(data)
    return target


def main(argv=None) -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="sim.oracle.v2_1")
    parser.add_argument("--variant", default="nh-small")
    parser.add_argument("--seed", type=int, default=340001)
    parser.add_argument("--scenarios", type=Path, default=Path(__file__).resolve().parents[1] / "scenarios")
    args = parser.parse_args(argv)
    print(write_oracle_v2_1(args.variant, args.seed, args.scenarios))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
