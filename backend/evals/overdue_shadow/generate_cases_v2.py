"""
Gerador deterministico do conjunto de PROVA v2 (selado) da avaliacao
independente do modelo no agente de vencidos (shadow).

Papel dos conjuntos
-------------------
- cases_v1.json = conjunto de AJUSTE (desenvolvimento): foi usado para
  medir o prompt v1 e pode orientar a correcao do prompt.
- cases_v2.json = conjunto de PROVA: inedito, nao pode orientar ajuste
  de prompt. Ao ser consultado para ajustar, deixa de ser prova.

Uso (a partir de backend/):
    python -m evals.overdue_shadow.generate_cases_v2            # confere
    python -m evals.overdue_shadow.generate_cases_v2 --write    # grava

Regra de rotulo: a MESMA do v1 (ver generate_cases.py; transcricao de
OverdueDetectionService.run_scan): mark_overdue sse status == 'aberto' e
due_date < business_date (estrito); caso contrario no_action. A funcao
`expected_decision` e reutilizada do v1, nao copiada.

Composicao: 80 casos, 40 no_action e 40 mark_overdue, com reforco nos
limites (vence hoje, -1/-2 dias = ainda nao venceu, +1/+2 dias =
vencido). Seed 20261117 (v1: 20261116). Nenhuma tripla
(status, due_date, business_date) coincide com o v1 (garantido na
construcao e por teste). Apenas datas e status; nenhum dado pessoal.
Arquivo deterministico: JSON canonico, chaves ordenadas, LF, newline
final; `.gitattributes` o marca `-text` para que o hash nao mude.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date
from datetime import timedelta
from pathlib import Path
from typing import Any
from typing import Final

from evals.overdue_shadow import generate_cases as v1

DATASET_VERSION: Final[str] = "overdue-shadow-cases-v2"
SEED: Final[int] = 20261117
CASES_PATH: Final[Path] = Path(__file__).with_name("cases_v2.json")
DECISIONS: Final[tuple[str, str]] = v1.DECISIONS
LABEL_RULE: Final[str] = v1.LABEL_RULE
ROLE: Final[str] = (
    "PROVA (hold-out): inedito, nao usar para ajustar prompt; "
    "o ajuste usa cases_v1.json"
)

_RANDOM_FROM: Final[date] = date(2025, 1, 1)
_RANDOM_SPAN_DAYS: Final[int] = 365 * 4

# (category, status, due_date, business_date); fixed boundary cases
_FIXED: Final[tuple[tuple[str, str, str, str], ...]] = (
    # not_due_minus1: due is the day after the business date
    ("not_due_minus1", "aberto", "2028-02-29", "2028-02-28"),
    ("not_due_minus1", "aberto", "2028-01-01", "2027-12-31"),
    ("not_due_minus1", "aberto", "2026-05-01", "2026-04-30"),
    # not_due_minus2
    ("not_due_minus2", "aberto", "2028-02-29", "2028-02-27"),
    ("not_due_minus2", "aberto", "2027-01-02", "2026-12-31"),
    # due_today
    ("due_today", "aberto", "2028-02-29", "2028-02-29"),
    ("due_today", "aberto", "2027-12-31", "2027-12-31"),
    ("due_today", "aberto", "2026-03-01", "2026-03-01"),
    # plus1: due is the day before
    ("plus1", "aberto", "2027-12-31", "2028-01-01"),
    ("plus1", "aberto", "2028-02-29", "2028-03-01"),
    ("plus1", "aberto", "2026-04-30", "2026-05-01"),
    # plus2
    ("plus2", "aberto", "2027-12-30", "2028-01-01"),
    ("plus2", "aberto", "2028-02-28", "2028-03-01"),
    ("plus2", "aberto", "2026-02-27", "2026-03-01"),
)

# category -> (count, status, min days_after_due, max days_after_due)
_FILL: Final[dict[str, tuple[int, str, int, int]]] = {
    # expected no_action: 5+5+6 + 10 + (4+1+2) + (4+1+2) = 40
    "not_due_minus1": (5, "aberto", -1, -1),
    "not_due_minus2": (5, "aberto", -2, -2),
    "not_due_far": (6, "aberto", -400, -3),
    "due_today": (10, "aberto", 0, 0),
    "paid_past_due": (4, "pago", 1, 400),
    "paid_today": (1, "pago", 0, 0),
    "paid_future": (2, "pago", -200, -1),
    "already_overdue_past": (4, "atrasado", 1, 400),
    "already_overdue_today": (1, "atrasado", 0, 0),
    "already_overdue_future": (2, "atrasado", -200, -1),
    # expected mark_overdue: 10+8+6+10+6 = 40
    "plus1": (10, "aberto", 1, 1),
    "plus2": (8, "aberto", 2, 2),
    "few_days": (6, "aberto", 3, 10),
    "many_days": (10, "aberto", 11, 400),
    "extreme": (6, "aberto", 401, 2000),
}


def v1_keys() -> set[tuple[str, date, date]]:
    return {
        (status, due, business)
        for _, status, due, business in v1._build_raw(v1.SEED)
    }


def _build_raw() -> list[tuple[str, str, date, date]]:
    rng = random.Random(SEED)
    rows: list[tuple[str, str, date, date]] = []
    seen = v1_keys()
    per_category = {name: 0 for name in _FILL}

    def add(category: str, status: str, due: date, business: date) -> bool:
        key = (status, due, business)

        if key in seen:
            return False

        seen.add(key)
        rows.append((category, status, due, business))
        per_category[category] += 1
        return True

    for category, status, due_text, business_text in _FIXED:
        add(
            category,
            status,
            date.fromisoformat(due_text),
            date.fromisoformat(business_text),
        )

    for category, (count, status, low, high) in _FILL.items():
        guard = 0

        while per_category[category] < count:
            guard += 1

            if guard > 10_000:
                raise RuntimeError("could not fill category " + category)

            business = _RANDOM_FROM + timedelta(
                days=rng.randrange(_RANDOM_SPAN_DAYS)
            )
            add(
                category,
                status,
                business - timedelta(days=rng.randint(low, high)),
                business,
            )

    rng.shuffle(rows)
    return rows


def build_dataset() -> dict[str, Any]:
    cases = []

    for index, (category, status, due, business) in enumerate(
        _build_raw(), start=1
    ):
        cases.append(
            {
                "id": f"OS2-{index:03d}",
                "category": category,
                "status": status,
                "due_date": due.isoformat(),
                "business_date": business.isoformat(),
                "days_after_due": (business - due).days,
                "expected": v1.expected_decision(status, due, business),
            }
        )

    counts = {name: 0 for name in DECISIONS}

    for case in cases:
        counts[case["expected"]] += 1

    return {
        "dataset_version": DATASET_VERSION,
        "seed": SEED,
        "role": ROLE,
        "label_rule": LABEL_RULE,
        "counts": counts,
        "cases": cases,
    }


render = v1.render
sha256_hex = v1.sha256_hex


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    data = render(build_dataset())
    digest = sha256_hex(data)

    if args.write:
        if CASES_PATH.exists():
            print("RECUSADO: o arquivo selado ja existe; nao sobrescreve.")
            return 2

        CASES_PATH.write_bytes(data)
        print(f"gravado {CASES_PATH.name} sha256={digest}")
        return 0

    if not CASES_PATH.exists():
        print(f"ausente; sha256 gerado={digest}")
        return 2

    on_disk = sha256_hex(CASES_PATH.read_bytes())
    print(f"arquivo sha256={on_disk} gerado sha256={digest}")
    return 0 if on_disk == digest else 1


if __name__ == "__main__":
    sys.exit(main())
