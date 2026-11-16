"""
Gerador deterministico do conjunto selado de casos da avaliacao
independente do modelo no agente de vencidos (shadow).

Uso (a partir de backend/):
    python -m evals.overdue_shadow.generate_cases            # confere o arquivo
    python -m evals.overdue_shadow.generate_cases --write    # grava o arquivo

REGRA DE ROTULO (unica fonte da verdade do rotulo esperado)
-----------------------------------------------------------
Fonte: a rotina deterministica OverdueDetectionService.run_scan
(app/services/overdue_detection_service.py) seleciona exatamente as contas
com `Account.status == "aberto"` E `Account.vencimento < today`
(comparacao ESTRITA). O rotulo do caso e a transcricao dessa regra, com
`business_date` no papel de `today`:

    esperado = "mark_overdue"  se status == "aberto" e due_date < business_date
    esperado = "no_action"     em qualquer outro caso

Consequencias, todas cobertas por casos:
- vence hoje (due_date == business_date): no_action (nao e estritamente menor);
- status "pago": no_action, mesmo com vencimento passado;
- status "atrasado": no_action (a rotina so le "aberto"; ja esta atrasado);
- vencimento no futuro: no_action.
Os unicos status existentes sao os do CHECK ck_accounts_status_valid do
modelo Account: aberto, atrasado, pago. Nao existe status "cancelado"; por
isso nenhum caso o usa (um status inexistente nao e entrada real).

TAMANHO: 80 casos, 40 `no_action` e 40 `mark_overdue`. Com 40 casos por
classe, zero erros observados ainda deixam o limite superior de 95% (regra
dos tres) em ~7,5% por classe; menos que isso nao distingue um modelo
confiavel de um mediano, e mais que isso custa ~30 s por caso sem ganho
proporcional. Tempo estimado da rodada real: 80 x ~30 s = ~40 min (teto: 80 x 120 s = 160 min).

Dados: apenas datas e um status; nenhum dado pessoal, valor ou
identificador de conta. O arquivo e produzido de forma deterministica
(seed fixa, JSON canonico com chaves ordenadas, newline final, LF).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import date
from datetime import timedelta
from pathlib import Path
from typing import Any
from typing import Final

DATASET_VERSION: Final[str] = "overdue-shadow-cases-v1"
SEED: Final[int] = 20261116
CASES_PATH: Final[Path] = Path(__file__).with_name("cases_v1.json")
DECISIONS: Final[tuple[str, str]] = ("mark_overdue", "no_action")
STATUSES: Final[tuple[str, ...]] = ("aberto", "atrasado", "pago")

LABEL_RULE: Final[str] = (
    "mark_overdue iff status == 'aberto' and due_date < business_date "
    "(strict); otherwise no_action. Source: "
    "OverdueDetectionService.run_scan in "
    "app/services/overdue_detection_service.py "
    "(Account.status == 'aberto' and Account.vencimento < today)."
)

_RANDOM_FROM: Final[date] = date(2023, 1, 1)
_RANDOM_SPAN_DAYS: Final[int] = 365 * 5


def expected_decision(
    status: str, due_date: date, business_date: date
) -> str:
    """The documented rule. The only place that assigns a label."""
    if status == "aberto" and due_date < business_date:
        return "mark_overdue"

    return "no_action"


def _d(text: str) -> date:
    return date.fromisoformat(text)


# (category, status, due_date, business_date) fixed edge cases.
_FIXED: Final[tuple[tuple[str, str, str, str], ...]] = (
    # not_due_future: aberto, due after business date
    ("not_due_future", "aberto", "2024-01-01", "2023-12-31"),
    ("not_due_future", "aberto", "2024-03-01", "2024-02-29"),
    ("not_due_future", "aberto", "2024-02-29", "2024-02-28"),
    ("not_due_future", "aberto", "2025-03-01", "2024-03-01"),
    # due_today: aberto, due == business date (not strictly before)
    ("due_today", "aberto", "2024-02-29", "2024-02-29"),
    ("due_today", "aberto", "2023-12-31", "2023-12-31"),
    ("due_today", "aberto", "2024-01-01", "2024-01-01"),
    ("due_today", "aberto", "2023-02-28", "2023-02-28"),
    ("due_today", "aberto", "2024-02-28", "2024-02-28"),
    ("due_today", "aberto", "2025-03-01", "2025-03-01"),
    # one_day: aberto, due is the day before (month/year/leap turns)
    ("one_day", "aberto", "2023-12-31", "2024-01-01"),
    ("one_day", "aberto", "2024-02-28", "2024-02-29"),
    ("one_day", "aberto", "2024-02-29", "2024-03-01"),
    ("one_day", "aberto", "2023-02-28", "2023-03-01"),
    ("one_day", "aberto", "2024-01-31", "2024-02-01"),
    ("one_day", "aberto", "2023-04-30", "2023-05-01"),
    ("one_day", "aberto", "2024-12-31", "2025-01-01"),
    ("one_day", "aberto", "2025-06-30", "2025-07-01"),
    # many_days: exact boundaries
    ("many_days", "aberto", "2024-01-01", "2024-01-31"),   # 30
    ("many_days", "aberto", "2024-01-01", "2024-02-01"),   # 31
    ("many_days", "aberto", "2024-01-01", "2024-03-31"),   # 90
    ("many_days", "aberto", "2023-01-01", "2024-01-01"),   # 365
    ("many_days", "aberto", "2024-01-01", "2025-01-01"),   # 366 (leap year)
    ("many_days", "aberto", "2023-06-15", "2023-12-12"),   # 180
    # extreme: very late (999 is the shadow budget worst-case size)
    ("extreme", "aberto", "2023-01-01", "2025-09-26"),     # 999
    ("extreme", "aberto", "2022-01-01", "2025-12-31"),
)

# category -> (count, status, days_after_due range [min, max]); the
# fixed cases above count toward the total of their category.
_RANDOM_FILL: Final[dict[str, tuple[int, str, int, int]]] = {
    "not_due_future": (12, "aberto", -400, -2),
    "due_today": (8, "aberto", 0, 0),
    "paid_past_due": (5, "pago", 1, 400),
    "paid_today": (2, "pago", 0, 0),
    "paid_future": (3, "pago", -200, -1),
    "already_overdue_past": (7, "atrasado", 1, 400),
    "already_overdue_today": (1, "atrasado", 0, 0),
    "already_overdue_future": (2, "atrasado", -200, -1),
    "one_day": (12, "aberto", 1, 1),
    "few_days": (8, "aberto", 2, 10),
    "many_days": (12, "aberto", 11, 400),
    "extreme": (8, "aberto", 401, 2000),
}


def _build_raw(seed: int) -> list[tuple[str, str, date, date]]:
    rng = random.Random(seed)
    rows: list[tuple[str, str, date, date]] = []
    seen: set[tuple[str, date, date]] = set()

    def add(category: str, status: str, due: date, business: date) -> bool:
        key = (status, due, business)

        if key in seen:
            return False

        seen.add(key)
        rows.append((category, status, due, business))
        return True

    per_category: dict[str, int] = {name: 0 for name in _RANDOM_FILL}

    for category, status, due_text, business_text in _FIXED:
        if add(category, status, _d(due_text), _d(business_text)):
            per_category[category] += 1

    # Deterministic iteration order: dict insertion order of _RANDOM_FILL.
    for category, (count, status, low, high) in _RANDOM_FILL.items():
        guard = 0

        while per_category[category] < count:
            guard += 1

            if guard > 10_000:
                raise RuntimeError("could not fill category " + category)

            business = _RANDOM_FROM + timedelta(
                days=rng.randrange(_RANDOM_SPAN_DAYS)
            )
            days_after_due = rng.randint(low, high)
            due = business - timedelta(days=days_after_due)

            if add(category, status, due, business):
                per_category[category] += 1

    rng.shuffle(rows)
    return rows


def build_dataset(seed: int = SEED) -> dict[str, Any]:
    cases = []

    for index, (category, status, due, business) in enumerate(
        _build_raw(seed), start=1
    ):
        cases.append(
            {
                "id": f"OS-{index:03d}",
                "category": category,
                "status": status,
                "due_date": due.isoformat(),
                "business_date": business.isoformat(),
                "days_after_due": (business - due).days,
                "expected": expected_decision(status, due, business),
            }
        )

    counts = {name: 0 for name in DECISIONS}

    for case in cases:
        counts[case["expected"]] += 1

    return {
        "dataset_version": DATASET_VERSION,
        "seed": seed,
        "label_rule": LABEL_RULE,
        "counts": counts,
        "cases": cases,
    }


def render(dataset: dict[str, Any]) -> bytes:
    """Canonical bytes: sorted keys, 2-space indent, LF, final newline."""
    text = json.dumps(
        dataset, sort_keys=True, indent=2, ensure_ascii=True
    )
    return (text + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
