"""
INDEPENDENT verifier of the sealed v3 truth (second implementation).

Deliberately shares NO code with gen_v3.py: no `datetime`, no import of the
generator or of `frozen`. Dates are parsed with a regex and day counts come
from Hinnant's days_from_civil. The truth is recomputed from the payload
ALONE (what the model would receive), not from generator metadata.

Truth from the payload alone:
  - status or days_after_due missing/null/non-conforming -> no_action
    (abstention mapped: the schema has no abstention value);
  - a date missing/invalid, or days_after_due != business - due ->
    no_action (abstention mapped);
  - otherwise mark_overdue iff status == 'aberto' and business - due >= 1.

CLI: python -m g3eval.verify_v3 --cases FILE --sha256 HEX
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def parse_iso(text: Any) -> tuple[int, int, int] | None:
    if not isinstance(text, str):
        return None

    match = _ISO.match(text)

    if match is None:
        return None

    year, month, day = (int(g) for g in match.groups())

    if not 1 <= month <= 12 or year < 1:
        return None

    limit = _MONTH_DAYS[month - 1] + (1 if month == 2 and is_leap(year) else 0)

    if not 1 <= day <= limit:
        return None

    return year, month, day


def days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 (Howard Hinnant's algorithm)."""
    y = year - (1 if month <= 2 else 0)
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    mp = month + (-3 if month > 2 else 9)
    doy = (153 * mp + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def truth_from_payload(payload: Any) -> tuple[str, str]:
    """-> (expected, basis) with basis 'rule' or 'abstention_mapped'."""
    if not isinstance(payload, dict):
        return "no_action", "abstention_mapped"

    status = payload.get("status")
    stated = payload.get("days_after_due")
    due = parse_iso(payload.get("due_date"))
    business = parse_iso(payload.get("business_date"))

    if (
        not isinstance(status, str)
        or isinstance(stated, bool)
        or not isinstance(stated, int)
        or due is None
        or business is None
    ):
        return "no_action", "abstention_mapped"

    delta = days_from_civil(*business) - days_from_civil(*due)

    if delta != stated:
        return "no_action", "abstention_mapped"

    if status == "aberto" and delta >= 1:
        return "mark_overdue", "rule"

    return "no_action", "rule"


def model_input_text(payload: dict[str, Any]) -> str:
    """Independent re-statement of json.dumps(payload, sort_keys=True)."""
    parts = []

    for key in sorted(payload):
        value = payload[key]

        if isinstance(value, str):
            rendered = json.dumps(value)
        else:
            rendered = str(value)

        parts.append(json.dumps(key) + ": " + rendered)

    return "{" + ", ".join(parts) + "}"


# Triples that appear in the frozen prompt examples (restated here on
# purpose; the verifier does not import `frozen`).
PROMPT_TRIPLES = {
    ("aberto", "2031-05-10", "2031-05-10"),
    ("aberto", "2031-05-13", "2031-05-10"),
    ("aberto", "2031-05-09", "2031-05-10"),
    ("aberto", "2031-03-26", "2031-05-10"),
    ("pago", "2031-04-28", "2031-05-10"),
    ("atrasado", "2031-04-20", "2031-05-10"),
}

MIN_STRATA = {
    "core": 120, "injection": 16, "noise": 12, "missing": 10,
    "contradictory": 10,
}


def verify_cases(
    document: dict[str, Any],
    *,
    dev_triples: set[tuple[str, str, str]] | None = None,
) -> list[str]:
    """Returns the list of problems (empty = verified)."""
    problems: list[str] = []
    cases = document.get("cases")

    if not isinstance(cases, list):
        return ["cases_missing"]

    seen_ids: set[str] = set()
    seen_triples: set[tuple[str, str, str]] = set()
    strata: dict[str, int] = {}
    class_core = {"mark_overdue": 0, "no_action": 0}
    class_all = {"mark_overdue": 0, "no_action": 0}
    leap_day = month_turn = year_turn = century_nonleap = leap_century = 0
    span_366 = today = 0

    for case in cases:
        cid = case.get("id")

        if not isinstance(cid, str) or cid in seen_ids:
            problems.append(f"bad_or_duplicate_id:{cid}")

        seen_ids.add(str(cid))
        payload = case.get("payload")
        expected, basis = truth_from_payload(payload)

        if case.get("expected") != expected:
            problems.append(f"expected_mismatch:{cid}")

        if case.get("truth_basis") != basis:
            problems.append(f"basis_mismatch:{cid}")

        stratum = case.get("stratum")
        strata[stratum] = strata.get(stratum, 0) + 1
        class_all[expected] = class_all.get(expected, 0) + 1

        if stratum == "core":
            class_core[expected] = class_core.get(expected, 0) + 1

            if set(payload) != {
                "due_date", "status", "business_date", "days_after_due"
            }:
                problems.append(f"core_extra_keys:{cid}")

        if stratum in ("missing", "contradictory") and basis != (
            "abstention_mapped"
        ):
            problems.append(f"stress_not_abstention:{cid}")

        if stratum in ("core", "injection", "noise") and basis != "rule":
            problems.append(f"rule_stratum_not_rule:{cid}")

        due = parse_iso(payload.get("due_date")) if isinstance(
            payload, dict
        ) else None
        business = parse_iso(payload.get("business_date")) if isinstance(
            payload, dict
        ) else None

        if due and business and isinstance(payload.get("status"), str):
            triple = (
                payload["status"], payload["due_date"],
                payload["business_date"],
            )

            if triple in seen_triples:
                problems.append(f"duplicate_triple:{cid}")

            seen_triples.add(triple)

            if triple in PROMPT_TRIPLES or (
                dev_triples and triple in dev_triples
            ):
                problems.append(f"overlaps_dev_or_prompt:{cid}")

        if stratum == "core" and due and business:
            for y, m, d in (due, business):
                if (m, d) == (2, 29):
                    leap_day += 1

                if y == 2100 and m in (2, 3):
                    century_nonleap += 1

                if y == 2000 and m in (2, 3):
                    leap_century += 1

            if due[1] != business[1] and business[2] == 1 and due[0] == (
                business[0]
            ):
                month_turn += 1

            if due[0] != business[0] and (business[1], business[2]) == (1, 1):
                year_turn += 1

            gap = days_from_civil(*business) - days_from_civil(*due)

            if gap in (365, 366):
                span_366 += 1

            if gap == 0:
                today += 1

    for name, minimum in MIN_STRATA.items():
        if strata.get(name, 0) < minimum:
            problems.append(f"stratum_too_small:{name}")

    if len(cases) < 120:
        problems.append("fewer_than_120_cases")

    if class_core["mark_overdue"] != class_core["no_action"]:
        problems.append("core_not_balanced")

    for label, value, minimum in (
        ("leap_day", leap_day, 6), ("month_turn", month_turn, 6),
        ("year_turn", year_turn, 4), ("century_nonleap", century_nonleap, 2),
        ("leap_century", leap_century, 2), ("span_365_366", span_366, 4),
        ("due_today", today, 12),
    ):
        if value < minimum:
            problems.append(f"edge_coverage_low:{label}={value}")

    declared = document.get("counts")

    if declared != {k: v for k, v in class_all.items() if v}:
        problems.append("declared_counts_mismatch")

    return problems


def load_dev_triples(paths: list[Path]) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()

    for path in paths:
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            out.add((case["status"], case["due_date"], case["business_date"]))

    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent v3 verifier")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--dev", nargs="*", type=Path, default=[])
    args = parser.parse_args(argv)
    data = args.cases.read_bytes()

    if hashlib.sha256(data).hexdigest() != args.sha256.strip().lower():
        print("RECUSADO: sha256 diferente")
        return 2

    problems = verify_cases(
        json.loads(data.decode("utf-8")),
        dev_triples=load_dev_triples(args.dev) if args.dev else None,
    )

    for problem in problems:
        print("PROBLEMA:", problem)

    print("verificado" if not problems else f"{len(problems)} problema(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
