"""Shared operational facts for legacy agents and the decision engine.

Account.status stays unchanged. Overdue observation comes from the canonical
receivable lifecycle and the business clock, including for open unpaid titles.
Missing or inconsistent dates/statuses do not authorize an analysis.
"""
from dataclasses import dataclass
from datetime import date
from datetime import datetime

from app.core.receivable_lifecycle import business_today
from app.core.receivable_lifecycle import evaluate_receivable_lifecycle


@dataclass(frozen=True)
class ReceivableAnalysis:
    financial_status: str
    operational_status: str
    days_overdue: int


def evaluate_receivable_analysis(
    *,
    financial_status: str,
    vencimento: str | date,
    today: date | None = None,
) -> ReceivableAnalysis | None:
    status = financial_status.strip().lower()
    if status not in {"aberto", "atrasado", "pago"}:
        return None
    if isinstance(vencimento, datetime):
        return None
    try:
        due_date = (
            vencimento
            if isinstance(vencimento, date)
            else date.fromisoformat(vencimento.strip())
        )
    except (AttributeError, TypeError, ValueError):
        return None
    lifecycle = evaluate_receivable_lifecycle(
        financial_status=status,
        vencimento=due_date,
        today=today if today is not None else business_today(),
    )
    if lifecycle.state == "paid":
        operational_status = "pago"
        days_overdue = 0
    elif lifecycle.days_overdue > 0:
        operational_status = "atrasado"
        days_overdue = lifecycle.days_overdue
    else:
        operational_status = "aberto"
        days_overdue = 0
    return ReceivableAnalysis(
        financial_status=status,
        operational_status=operational_status,
        days_overdue=days_overdue,
    )
