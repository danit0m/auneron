"""
UTILITARIO DE AUTORIA (executado manualmente, uma vez; o resultado e commitado e fixado por hash). E o UNICO modulo do
pacote que le a agenda publica e o plano de coleta do NH, e so para CONTAR: a saida e um JSON de inteiros. O gerador da
fixture nao pode importar este modulo (teste AST).

    python -m sim.rehearsal.extract_profile --out sim/rehearsal/profiles/nh_peak_profile_v1.json
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

from sim.rehearsal import profile as profile_mod

ROOT = Path(__file__).resolve().parents[1]
AGENDA = ROOT / "scenarios" / "nh-small-v2-s340001" / "public_agenda.json"
ORACLE = ROOT / "oracle" / "artifacts" / "nh-small-v2-s340001-oracle-v2.2" / "oracle.json"


def minutes(hhmm: str) -> int:
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


def extract() -> dict:
    ops = json.loads(AGENDA.read_bytes().decode("utf-8"))["ops"]
    oracle = json.loads(ORACLE.read_bytes().decode("utf-8"))
    from sim.oracle.collection_plan import compile_plan

    plan = compile_plan(oracle, ops)
    by_type = collections.Counter(op["op"] for op in ops)
    by_slot = collections.Counter((op["day"], op["at"]) for op in ops)
    by_day = collections.Counter(op["day"] for op in ops)
    after_d0 = [n for (day, _), n in by_slot.items() if day > 0]
    per_day_items = collections.Counter(i["day"] for i in plan["items"] if i["safe_at"] == "after_daily_barrier")
    groups = collections.Counter((op.get("group"), op.get("mode")) for op in ops if op.get("group"))
    expectations = oracle["expectations"]
    restart_day = next(op["day"] for op in ops if op["op"] == "restart_backend")
    restart_accounts = sum(1 for op in ops if op["op"] == "create_receivable" and op["day"] <= restart_day)
    profile = {
        "schema": profile_mod.SCHEMA, "accounts_total": by_type["create_receivable"], "ops_total": len(ops),
        "ops_by_type": dict(sorted(by_type.items())), "d0_ops": by_day[0],
        "d0_burst_slot_ops": max(n for (day, _), n in by_slot.items() if day == 0),
        "max_ops_day": max(by_day.values()), "max_ops_slot_after_d0": max(after_d0),
        "approvals_total": by_type["request_mark_paid"] + by_type["materialize_mark_overdue"],
        "approvals_mo": by_type["materialize_mark_overdue"], "approvals_mp": by_type["request_mark_paid"],
        "escalation_ops": by_type["materialize_escalation"],
        "escalation_distinct_refs": len({op["ref"] for op in ops if op["op"] == "materialize_escalation"}),
        "escalation_episodes": sum(1 for e in expectations if e["kind"] == "escalation_work_item_exists"),
        "assessments": by_type["record_assessment"], "consults": by_type["consult_nba"], "due_changes": by_type["change_due_date"],
        "observed_fact_expectations": sum(1 for e in expectations if e["kind"] == "observed_fact_count"),
        "observed_fact_links": sum(1 for e in expectations if e["kind"] == "observed_fact_links_event"),
        "provenance_derivations": sum(1 for e in expectations if e["kind"] == "provenance_present"),
        "restart_accounts": restart_accounts, "sweep_items": max(per_day_items.values()),
        "late_slot_minutes": sorted(minutes(at) for (day, at) in by_slot if minutes(at) > 18 * 60),
        "midnight_slots": sum(1 for (day, at) in by_slot if at == "00:00"),
        "same_instant_group_sizes": sorted(n for (g, mode), n in groups.items() if mode == "same_instant"),
        "concurrent_group_sizes": sorted(n for (g, mode), n in groups.items() if mode == "concurrent"),
        "replay_ops": sum(1 for op in ops if op.get("mode") == "replay"),
        "authority_rejections": sum(1 for e in expectations if e["kind"] == "authority_violation_rejected"),
        "wrong_escalation_attempts": sum(1 for e in expectations if e["kind"] == "http_status"),
        "slots_total": len(by_slot), "days_with_ops": len(by_day), "days_total": 180,
    }
    return profile_mod.validate_profile(profile)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sim.rehearsal.extract_profile")
    parser.add_argument("--out", type=Path, default=profile_mod.PROFILE_PATH)
    args = parser.parse_args(argv)
    data = profile_mod.canonical(extract())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)
    print(hashlib.sha256(data).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
