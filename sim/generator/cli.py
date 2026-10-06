"""
CLI do gerador SIM-1.2.

    python -m sim.generator.cli generate --variant nh-standard --seed 340001
    python -m sim.generator.cli verify sim/scenarios/nh-standard-s340001
    python -m sim.generator.cli hashes --variant nh-small --seed 340001

`generate` grava `<out>/<variant>-s<seed>/` com manifest + 3 artefatos.
`hashes` gera em memoria e imprime os hashes (prova de determinismo entre
SOs sem gravar nada).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sim import CLAIM
from sim.generator import GENERATOR_VERSION
from sim.generator import SCHEMA_VERSION
from sim.generator.agenda import build_public_agenda
from sim.generator.canonical import canonical_bytes
from sim.generator.canonical import sha256_bytes
from sim.generator.cases import Injector
from sim.generator.cases import inject_agenda
from sim.generator.cases import restart_in_flight
from sim.generator.cases import tag_cases
from sim.generator.config import load_inputs
from sim.generator.timeline import to_hhmm
from sim.generator.world import Simulator
from sim.oracle.expectations import build_expectations
from sim.oracle.rules import RULE_VERSIONS
from sim.oracle.schema import separation_violations
from sim.oracle.schema import validate_scenario

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "scenarios"
DEFAULT_SEED = 340001


def _instant(value):
    if value is None:
        return None
    return {"day": value[0], "at": to_hhmm(value[1])}


def world_artifact(world, scenario_id: str) -> dict:
    customers = [
        {
            "cust_ref": c.ref,
            "name": c.name,
            "email": c.email,
            "whatsapp": c.whatsapp,
            "segment": c.segment,
            "profile": c.profile,
            "route": c.route,
            "drift": None if c.drift_to is None else {"to": c.drift_to, "from_day": c.drift_from_day},
            "role": c.role,
        }
        for c in world.customers
    ]
    titles = []
    for t in world.titles:
        titles.append({
            "rec_ref": t.ref,
            "cust_ref": t.customer.ref,
            "order_no": t.order_no,
            "opening": t.opening,
            "issue": _instant(t.issue),
            "valor": f"{t.valor // 100}.{t.valor % 100:02d}",
            "method": t.method,
            "behavior": {"profile_at_issue": t.profile_at_issue, "plan_kind": t.plan.get("kind")},
            "due_history": [{"day": at[0], "at": to_hhmm(at[1]), "due_day": due} for at, due in t.venc_log],
            "fact_at": _instant(t.fact),
            "partial": None if t.partial is None else {**_instant(t.partial[0]),
                                                       "amount": f"{t.partial[1] // 100}.{t.partial[1] % 100:02d}"},
            "evidence_at": _instant(t.evidence),
            "reported_at": _instant(t.reported),
            "contacts": [{**_instant(c["at"]), "actor": c["actor"], "code": c["code"]} for c in t.contacts],
        })
    events = []
    ordered = sorted(world.events, key=lambda item: (item[0], item[1], item[2]))
    for number, (day, minute, _, record) in enumerate(ordered, start=1):
        events.append({"world_event_id": f"WE-{number:06d}", "day": day, "at": to_hhmm(minute), **record})
    return {
        "artifact": "world",
        "scenario_id": scenario_id,
        "customers": customers,
        "titles": titles,
        "events": events,
    }


def oracle_artifact(scenario_id: str, expectations: list, case_instances: dict, inputs) -> dict:
    cal = inputs.calendar
    counts: dict = {}
    for item in expectations:
        counts[item["semantics"]] = counts.get(item["semantics"], 0) + 1
    return {
        "artifact": "oracle",
        "scenario_id": scenario_id,
        "claim": CLAIM,
        "rule_versions": RULE_VERSIONS,
        "evidence_floor": {"day": cal.evidence_floor[0], "at": to_hhmm(cal.evidence_floor[1])},
        "invalidation_rules": [
            "oracle_separation_violation => scenario INVALID",
            "any HARNESS_ERROR => run INVALID",
        ],
        "evaluator_classes": [
            "CORRECT", "CORRECT_ABSTENTION", "DIVERGENCE", "PRODUCT_FINDING", "KNOWN_LIMIT",
            "NOT_REPRESENTABLE", "CALIBRATION_PENDING", "HARNESS_ERROR",
        ],
        "semantics_counts": counts,
        "case_instances": case_instances,
        "expectations": expectations,
    }


def generate(variant: str, seed: int) -> tuple[str, dict, dict]:
    """Gera em memoria. Retorna (scenario_id, {nome: bytes}, resumo)."""
    inputs = load_inputs()
    spec = inputs.variant(variant)
    scenario_id = f"{variant}-s{seed}"
    injector = Injector(inputs, variant, seed)
    world = Simulator(inputs, variant, seed, injector).run()
    agenda_cases = inject_agenda(world, inputs, int(spec["min_case_instances"]))
    world.replayed = set(agenda_cases.get("C-ADV-2", []))
    world.duplicate_settled = set(agenda_cases.get("C-ADV-1", []) + agenda_cases.get("C-ADV-3", []))
    world.restart_in_flight = restart_in_flight(world, inputs)
    expectations, labels = build_expectations(world, inputs)
    case_instances = tag_cases(world, inputs, agenda_cases, labels)
    for case in inputs.cases["cases"]:
        case_instances.setdefault(case, [])
    oracle = oracle_artifact(scenario_id, expectations, case_instances, inputs)
    world_doc = world_artifact(world, scenario_id)
    agenda = build_public_agenda(world, scenario_id)
    problems = separation_violations(agenda, oracle, world_doc)
    if problems:
        raise RuntimeError("cenario INVALID (separacao do Oracle): " + "; ".join(problems[:5]))
    files = {
        "public_agenda.json": canonical_bytes(agenda),
        "world.json": canonical_bytes(world_doc),
        "oracle.json": canonical_bytes(oracle),
    }
    manifest = {
        "artifact": "manifest",
        "scenario_id": scenario_id,
        "variant": variant,
        "seed": seed,
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "claim": CLAIM,
        "d0": inputs.calendar.iso(0),
        "days": inputs.calendar.days,
        "inputs": dict(sorted(inputs.hashes.items())),
        "artifacts": {name: sha256_bytes(data) for name, data in sorted(files.items())},
    }
    files["manifest.json"] = canonical_bytes(manifest)
    summary = {
        "customers": len(world.customers),
        "titles": len(world.titles),
        "agenda_ops": len(agenda["ops"]),
        "world_events": len(world_doc["events"]),
        "expectations": len(expectations),
        "semantics": oracle["semantics_counts"],
        "case_instances": {k: len(v) for k, v in case_instances.items()},
    }
    return scenario_id, files, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim.generator.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--variant", required=True)
    gen.add_argument("--seed", type=int, default=DEFAULT_SEED)
    gen.add_argument("--out", type=Path, default=DEFAULT_OUT)
    hsh = sub.add_parser("hashes")
    hsh.add_argument("--variant", required=True)
    hsh.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ver = sub.add_parser("verify")
    ver.add_argument("scenario_dir", type=Path)
    args = parser.parse_args(argv)

    if args.command == "verify":
        result = validate_scenario(args.scenario_dir)
        print(result["status"])
        for reason in result["reasons"]:
            print(" -", reason)
        return 0 if result["status"] == "VALID" else 2

    scenario_id, files, summary = generate(args.variant, args.seed)
    if args.command == "hashes":
        print(scenario_id)
        for name in sorted(files):
            print(f"{sha256_bytes(files[name])}  {name}")
        return 0
    target = args.out / scenario_id
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (target / name).write_bytes(data)
    print(scenario_id, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
