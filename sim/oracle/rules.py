"""
Replica independente das regras do Auneron usadas como camada `policy`.

Fonte: Design Freeze v2, secao 2.1 (lida em `9572338`). Cada regra tem
versao propria em RULE_VERSIONS; quando o produto mudar uma regra, a replica
e reimplementada explicitamente (nova versao) -- nunca ajustada em silencio.

Datas sao indices de dia (int). Valores monetarios em centavos (int).
"""

from __future__ import annotations

from dataclasses import dataclass

RULE_VERSIONS = {
    "receivable_lifecycle": "f3_lifecycle_v1",
    "mark_overdue_eligibility": "account_mark_overdue_eligibility_v1",
    "human_escalation_eligibility": "human_escalation_eligibility_v1",
    "behavior_pattern": "client_behavior_memory_v1(utc_payment_date)",
    "classification": "recurrence_v1",
    "nba": "nba_policy_v1",
    "observed_fact": "escalation_payment_observation:v1",
    "overdue_detection": "f1_replica_v0 (attempts/expiry: calibration_pending)",
}

# --- ciclo de vida (receivable_lifecycle) ---------------------------------
DUE_SOON_THRESHOLD_DAYS = 14
OVERDUE_ALERT_THRESHOLD_DAYS = 5


def lifecycle_state(financial_status: str, due_day: int, today: int) -> str:
    if financial_status == "pago":
        return "paid"
    days_to_due = due_day - today
    days_overdue = max(-days_to_due, 0)
    if days_to_due > DUE_SOON_THRESHOLD_DAYS:
        return "open"
    if days_to_due > 0:
        return "due_soon"
    if days_to_due == 0:
        return "due_today"
    if days_overdue <= OVERDUE_ALERT_THRESHOLD_DAYS:
        return "overdue"
    return "overdue_alert"


def days_overdue(due_day: int, today: int) -> int:
    return max(today - due_day, 0)


# --- elegibilidades -------------------------------------------------------
def mark_overdue_reason(financial_status: str, due_day: int, today: int) -> str | None:
    """None = elegivel (account_mark_overdue_eligibility_v1)."""
    if financial_status == "pago":
        return "already_paid"
    if financial_status == "atrasado":
        return "status_not_open"
    if not due_day < today:
        return "not_overdue"
    return None


def escalation_reason(
    financial_status: str,
    due_day: int,
    today: int,
    active_escalation: bool,
) -> str | None:
    """None = elegivel (human_escalation_eligibility_v1, R1 -> R2 -> R3)."""
    if financial_status == "pago":
        return "account_paid"
    if lifecycle_state(financial_status, due_day, today) not in (
        "overdue",
        "overdue_alert",
    ):
        return "lifecycle_not_overdue"
    if active_escalation:
        return "active_escalation_exists"
    return None


# --- memoria de comportamento + classificacao -----------------------------
MIN_RESOLVED_OCCURRENCES = 3
ATRASO_THRESHOLD_NUM = 1  # 0,50 = 1/2, comparado em inteiros
ATRASO_THRESHOLD_DEN = 2


def classify(atraso_dias: list[int]) -> str:
    """recurrence_v1: <3 -> INSUFFICIENT_DATA; proporcao (atraso>0) >= 0,50
    -> ATRASO_RECORRENTE; senao PAGAMENTO_REGULAR. Sem janela, sem peso."""
    resolved = len(atraso_dias)
    if resolved < MIN_RESOLVED_OCCURRENCES:
        return "INSUFFICIENT_DATA"
    late = sum(1 for value in atraso_dias if value > 0)
    if late * ATRASO_THRESHOLD_DEN >= resolved * ATRASO_THRESHOLD_NUM:
        return "ATRASO_RECORRENTE"
    return "PAGAMENTO_REGULAR"


# --- NBA ------------------------------------------------------------------
CRITICAL_OVERDUE_REFERENCE_DAYS = 45
EARLY_HIGH_EXPOSURE_REFERENCE_DAY = 5
ABSOLUTE_HIGH_VALUE_REFERENCE_CENTS = 1_500_000
OPERATIONAL_COST_FLOOR_REFERENCE_CENTS = 50_000

MARK_OVERDUE = "account.mark_overdue"
ESCALATE = "escalate_to_human"


@dataclass(frozen=True)
class NbaDecision:
    decision_type: str
    selected_actions: tuple[str, ...]
    trigger_rules: tuple[str, ...]


def nba_decision(
    mark_overdue_available: bool,
    escalate_available: bool,
    overdue_days: int,
    amount_cents: int,
) -> NbaDecision:
    """nba_policy_v1, R0-R3. `trigger_rules` lista SOMENTE os gatilhos de
    escalonamento de R3 (nomes congelados); demais nomes de regra nao sao
    replicados (calibracao no SIM-1.3)."""
    if not mark_overdue_available and not escalate_available:
        return NbaDecision("no_action", (), ())
    if mark_overdue_available and not escalate_available:
        return NbaDecision("single_action", (MARK_OVERDUE,), ())
    if escalate_available and not mark_overdue_available:
        return NbaDecision("single_action", (ESCALATE,), ())
    triggers: list[str] = []
    if (
        overdue_days > CRITICAL_OVERDUE_REFERENCE_DAYS
        and amount_cents > OPERATIONAL_COST_FLOOR_REFERENCE_CENTS
    ):
        triggers.append("prolonged_overdue_escalation")
    if (
        overdue_days >= EARLY_HIGH_EXPOSURE_REFERENCE_DAY
        and amount_cents >= ABSOLUTE_HIGH_VALUE_REFERENCE_CENTS
    ):
        triggers.append("high_exposure_early_escalation")
    if triggers:
        return NbaDecision("action_bundle", (MARK_OVERDUE, ESCALATE), tuple(triggers))
    return NbaDecision("single_action", (MARK_OVERDUE,), ())
