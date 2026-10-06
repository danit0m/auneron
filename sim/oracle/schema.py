"""
Validacao dos artefatos e regra de invalidacao (Design Freeze v2, 8.1/8.5).

* Falha de separacao do Oracle => cenario INVALID (nao so uma metrica).
* Hash divergente do manifesto => cenario INVALID.
"""

from __future__ import annotations

import json
from pathlib import Path

from sim.generator.agenda import MANIFEST_FILE
from sim.generator.agenda import PUBLIC_FILE
from sim.generator.agenda import PUBLIC_OP_KEYS
from sim.generator.agenda import PUBLIC_OPS
from sim.generator.canonical import read_json
from sim.generator.canonical import sha256_bytes

ARTIFACTS = ("public_agenda.json", "world.json", "oracle.json")
SEMANTICS = frozenset({
    "fact", "correlation_only", "abstain", "not_representable", "known_limit",
    "calibration_pending",
})
EXPECTATION_KINDS = frozenset({
    "account_status", "lifecycle_state", "knowledge_transition",
    "classification",
    "mark_overdue_eligible", "escalation_eligible", "nba_decision",
    "approval_request_exists", "approval_decided", "approval_expired",
    "escalation_work_item_exists", "vencimento_change_count", "account_event_count",
    "observed_fact_count", "observed_fact_links_event", "provenance_present",
    "human_assessment_count",
    "must_not_exist", "http_status", "no_duplicate_effect",
    "not_representable",
})
# Tokens que so existem no mundo/Oracle; nunca podem aparecer na agenda.
WORLD_ONLY_KEYS = frozenset({
    "profile", "profile_at_issue", "segment", "plan", "fact", "fact_at", "evidence",
    "evidence_at", "reported", "partial", "drift", "role", "case_ids", "cases",
    "policy", "ideal", "semantics", "expectations", "factors", "finding", "hold_until",
})


def separation_violations(agenda: dict, oracle: dict, world: dict) -> list:
    problems = []
    case_ids = set(oracle.get("case_instances", {}))
    profiles = {f"P{n}" for n in range(1, 11)}
    world_event_types = {event["type"] for event in world.get("events", [])}
    forbidden_values = case_ids | profiles | world_event_types
    previous = (-1, "")
    for index, op in enumerate(agenda.get("ops", [])):
        keys = set(op)
        if keys - PUBLIC_OP_KEYS:
            problems.append(f"op {index}: chaves nao publicas {sorted(keys - PUBLIC_OP_KEYS)}")
        if keys & WORLD_ONLY_KEYS:
            problems.append(f"op {index}: chaves do mundo/Oracle {sorted(keys & WORLD_ONLY_KEYS)}")
        if op.get("op") not in PUBLIC_OPS:
            problems.append(f"op {index}: operacao nao publica {op.get('op')}")
        for key, value in op.items():
            if isinstance(value, str) and value in forbidden_values:
                problems.append(f"op {index}: valor do mundo/Oracle em {key}={value}")
        current = (op["day"], op["at"])
        if current < previous:
            problems.append(f"op {index}: agenda fora de ordem")
        previous = current
        if op.get("seq") != index + 1:
            problems.append(f"op {index}: seq nao consecutivo")
    return problems


def validate_scenario(scenario_dir: Path) -> dict:
    scenario_dir = Path(scenario_dir)
    reasons = []
    manifest = read_json(scenario_dir / MANIFEST_FILE)
    data = {}
    for name in ARTIFACTS:
        raw = (scenario_dir / name).read_bytes()
        if sha256_bytes(raw) != manifest["artifacts"].get(name):
            reasons.append(f"hash divergente: {name}")
        data[name] = json.loads(raw.decode("utf-8"))
    problems = separation_violations(data[PUBLIC_FILE], data["oracle.json"], data["world.json"])
    reasons.extend(f"separacao: {p}" for p in problems)
    for item in data["oracle.json"]["expectations"]:
        if item["semantics"] not in SEMANTICS or item["kind"] not in EXPECTATION_KINDS:
            reasons.append(f"expectativa invalida: {item['exp_id']}")
            break
    return {"status": "VALID" if not reasons else "INVALID", "reasons": reasons}
