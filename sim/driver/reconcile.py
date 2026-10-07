"""
Reconciliacao fail-closed (Design Freeze V1.1 E-2, D-1.5.2-5).

`create_receivable` (PF-2: o produto nao e idempotente):

    resultado ambiguo -> 3 varreduras COMPLETAS e paginadas, 5 s entre elas
      3x vazio estavel            -> ABSENT (um unico resend)
      3x o MESMO unico candidato  -> ADOPT
      qualquer outra coisa        -> HarnessError (>1, leituras divergentes,
                                     pagina com erro/incompleta, truncada)

Um candidato e uma conta com (cliente, email, valor, vencimento) identicos e
id NAO mapeado. Nunca ha escolha heuristica.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from decimal import InvalidOperation

from sim.driver.dispatch import iso_day
from sim.driver.errors import HarnessError

ABSENT = "absent"
ADOPT = "adopt"


def _money(value) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def matches(account: dict, op: dict, d0: date) -> bool:
    return (
        account.get("cliente") == op["cliente"]
        and account.get("email") == op["email"]
        and _money(account.get("valor")) == _money(op["valor"])
        and account.get("vencimento") == iso_day(d0, int(op["vencimento_day"]))
    )


def scan_candidates(get_page, op: dict, d0: date, mapped_ids: set, page_limit: int, max_pages: int) -> tuple:
    """Varredura completa. `get_page(skip, limit) -> (status, items)`.
    Devolve (ids_candidatos_ordenados, paginas_lidas, total_visto)."""
    found: list = []
    skip, pages, total = 0, 0, 0
    while True:
        status, items = get_page(skip, page_limit)
        if status != 200 or not isinstance(items, list):
            raise HarnessError(f"varredura de reconciliacao inconclusiva (status={status})")
        pages += 1
        total += len(items)
        found.extend(int(item["id"]) for item in items if matches(item, op, d0) and int(item["id"]) not in mapped_ids)
        if len(items) < page_limit:
            return sorted(found), pages, total
        skip += page_limit
        if pages >= max_pages:
            raise HarnessError("varredura de reconciliacao truncada (max_pages)")


def reconcile_create_receivable(get_page, op: dict, d0: date, mapped_ids: set, params: dict, sleep, log=None):
    reads = []
    for index in range(int(params["reads"])):
        if index:
            sleep(float(params["wait_s"]))
        ids, pages, total = scan_candidates(get_page, op, d0, mapped_ids, int(params["page_limit"]),
                                            int(params["max_pages"]))
        reads.append(ids)
        if log is not None:
            log({"kind": "reconcile_read", "read": index + 1, "pages": pages, "seen": total, "candidates": ids})
    first = reads[0]
    if any(read != first for read in reads):
        raise HarnessError("reconciliacao instavel: leituras divergentes entre si")
    if not first:
        return ABSENT, None
    if len(first) == 1:
        return ADOPT, first[0]
    raise HarnessError(f"reconciliacao ambigua: {len(first)} candidatos")


def reconcile_decision(status_value, expected: str) -> str:
    """`status_value` = status atual da aprovacao. -> 'reconciled' | 'resend'."""
    if status_value == expected:
        return "reconciled"
    if status_value == "pending":
        return "resend"
    raise HarnessError(f"decisao ambigua: aprovacao em '{status_value}', esperado '{expected}' ou 'pending'")


def reconcile_due(current_iso, target_iso: str) -> bool:
    if current_iso == target_iso:
        return True
    raise HarnessError(f"vencimento nao confere apos tentativas: {current_iso} != {target_iso}")
