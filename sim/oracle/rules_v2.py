"""
Replica das regras de corredor da v2 (SIM-1.3 Discovery V1, aprovada).

Complementa `sim.oracle.rules` (v1, intacto). Fonte: leitura do codigo em
`27b6438`; nunca importa o produto.

* expiracao efetiva = `expires_at <= instante` (o produto pode continuar
  expondo status `pending` depois disso);
* separacao de funcoes nas mutacoes financeiras humanas:
  decisor != solicitante; executor != decisor;
* nomes reais de `applied_rules` do NBA (R0-R4).
"""

from __future__ import annotations

from sim.oracle import rules

RULE_VERSIONS_V2 = {
    "receivable_lifecycle": "f3_lifecycle_v1",
    "mark_overdue_eligibility": "account_mark_overdue_eligibility_v1",
    "human_escalation_eligibility": "human_escalation_eligibility_v1",
    "behavior_pattern": "client_behavior_memory_v1(utc_payment_date)",
    "classification": "recurrence_v1",
    "nba": "nba_policy_v1(applied_rules R0-R4)",
    "observed_fact": "escalation_payment_observation:v1",
    "mark_paid_corridor": "account_mark_paid_execution(request/decision/execution)",
    "mark_overdue_corridor": "human_account_mark_overdue_v1(no_retry_on_terminal_approval)",
    "approval_expiry": "effective_expiry_from_expires_at",
    "business_effect_verification": "bev_v1(human corridors only)",
    "overdue_detection_f1": "OUT (G-SIM-17)",
}

APPROVAL_TTL_MINUTES = 1440

# Papeis que podem cada passo governado (RBAC real, Discovery 12).
MUTATION_ROLES = frozenset({"manager", "executive", "administrator", "developer"})


def effectively_expired(expires_at: tuple[int, int], at: tuple[int, int]) -> bool:
    return at >= expires_at


def expiry_instant(created: tuple[int, int], ttl_minutes: int = APPROVAL_TTL_MINUTES) -> tuple[int, int]:
    day, minute = created
    total = minute + ttl_minutes
    return (day + total // 1440, total % 1440)


def authority_violation(requester: str, decider: str | None, executor: str | None) -> str | None:
    """None = combinacao valida. Codigos estaveis para expectativas."""
    if decider is not None and decider == requester:
        return "decider_is_requester"
    if executor is not None and decider is not None and executor == decider:
        return "executor_is_decider"
    return None


def can_mutate(role: str) -> bool:
    return role in MUTATION_ROLES


def mark_paid_execution_result(current_status: str, expected_status: str) -> str | None:
    """None = executa. Senao, motivo da recusa do produto."""
    if current_status == "pago":
        return "already_paid"
    if current_status != expected_status:
        return "expected_status_mismatch"
    return None


def mark_overdue_execution_result(current_status: str, current_due: int, episode_due: int, today: int) -> str | None:
    if current_status != "aberto":
        return "only_aberto_may_transition"
    if current_due != episode_due:
        return "due_date_changed_after_approval"
    if not current_due < today:
        return "not_overdue"
    return None


def nba_applied_rules(
    mark_overdue_available: bool,
    escalate_available: bool,
    overdue_days: int,
    amount_cents: int,
    prior_effect_contradicted: bool = False,
) -> tuple[str, ...]:
    """Nomes reais de `_decide` (R0-R3) + R4 de revisao."""
    review = ("prior_effect_contradiction_review",) if prior_effect_contradicted else ()
    if not mark_overdue_available and not escalate_available:
        return () + review
    if mark_overdue_available and not escalate_available:
        return ("mark_overdue_only_candidate",) + review
    if escalate_available and not mark_overdue_available:
        return ("escalate_to_human_only_candidate",) + review
    decision = rules.nba_decision(True, True, overdue_days, amount_cents)
    if decision.trigger_rules:
        return decision.trigger_rules + review
    return ("mark_overdue_precedence",) + review
