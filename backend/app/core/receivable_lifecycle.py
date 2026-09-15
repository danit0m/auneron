"""
Fatia F3 -- avaliador canonico de situacao operacional de recebiveis.

Funcao pura, sem acesso a banco, sem I/O: dado o status financeiro
persistido de uma Account, a data de vencimento e a data de referencia
("today"), calcula a situacao operacional observada pelo Auneron.

Design freeze (congelado com Tomaz em 12/09/2026):

    financial_status == "pago"          -> paid
    days_to_due > 14                    -> open
    1 <= days_to_due <= 14              -> due_soon
    days_to_due == 0                    -> due_today
    1 <= days_overdue <= 5              -> overdue
    days_overdue >= 6                   -> overdue_alert

Isso e puramente advisory: nao concede autoridade, nao altera
Account.status, nao substitui o corredor governado de aprovacao
(OverdueDetectionService, fatia F1) por onde a unica transicao real de
status ainda precisa passar. financial_status e operational_status sao
duas verdades paralelas e podem divergir legitimamente enquanto uma
proposta aguarda decisao humana.

Os limiares de severidade de risco de credito do RiskAgent (1/7/15/30
dias) permanecem uma politica separada sobre o mesmo fato-base
(days_overdue) e nao sao alterados por este modulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from app.core.config import settings


ReceivableLifecycleState = Literal[
    "open",
    "due_soon",
    "due_today",
    "overdue",
    "overdue_alert",
    "paid",
]


DUE_SOON_THRESHOLD_DAYS = 14
OVERDUE_ALERT_THRESHOLD_DAYS = 5


@dataclass(frozen=True)
class ReceivableLifecycle:
    state: ReceivableLifecycleState
    days_to_due: int
    days_overdue: int
    as_of: date


def evaluate_receivable_lifecycle(
    *,
    financial_status: str,
    vencimento: date,
    today: date,
) -> ReceivableLifecycle:
    days_to_due = (vencimento - today).days
    days_overdue = max(-days_to_due, 0)

    if financial_status.strip().lower() == "pago":
        return ReceivableLifecycle(
            state="paid",
            days_to_due=days_to_due,
            days_overdue=days_overdue,
            as_of=today,
        )

    if days_to_due > DUE_SOON_THRESHOLD_DAYS:
        state: ReceivableLifecycleState = "open"
    elif days_to_due > 0:
        state = "due_soon"
    elif days_to_due == 0:
        state = "due_today"
    elif days_overdue <= OVERDUE_ALERT_THRESHOLD_DAYS:
        state = "overdue"
    else:
        state = "overdue_alert"

    return ReceivableLifecycle(
        state=state,
        days_to_due=days_to_due,
        days_overdue=days_overdue,
        as_of=today,
    )


def business_today() -> date:
    """
    Data civil de referencia para regras de ciclo de vida de
    recebiveis, no fuso horario de negocio configurado (nao o relogio
    UTC do servidor) -- evita uma nota mudar de estado horas antes ou
    depois do esperado por causa do fuso do host.
    """

    return datetime.now(
        ZoneInfo(settings.business_timezone)
    ).date()
