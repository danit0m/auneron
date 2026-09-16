"""
Pilot Action Space V1.B -- Human Escalation Recommendation V1.

Projecao read-only e deterministica de elegibilidade da capability
work_action `escalate_to_human` para um unico financial_episode
(account_id + due_date, ja congelado em Outcome V1). Nao cria, nao
muta, nao persiste nada -- nao materializa WorkItem, ApprovalRequest
ou qualquer registro.

Congelado em quatro documentos de design freeze com Tomaz em
15/09/2026: Capability/Eligibility Survey (read-only), Architecture
Freeze, PRE-APPLY Mechanical/RBAC/Contract Gate, Executable Contract
/ File-Set Freeze (incluindo os dois ajustes finais: a formulacao de
supressao terminal em termos de R3 nao "episodio que deixa de
existir", e `status` como unica fonte de verdade no schema).

Politica versionada `human_escalation_eligibility_v1`, precedencia
fixa de `reason` (o gate estrutural, nunca a ordem incidental de
codigo):

    R1 (reason=account_paid):            account.status == "pago"
    R2 (reason=lifecycle_not_overdue):    lifecycle.state not in
                                           {overdue, overdue_alert}
    R3 (reason=active_escalation_exists): WorkItem nao-terminal com a
                                           mesma work_key do episodio

SUPPORT EVIDENCE (days_overdue, amount, classification,
average_late_days, prior_episodes) nunca e gate: ausencia de qualquer
sinal opcional nunca fabrica dado nem altera eligible/ineligible.

Supressao (#4 do Executable Contract) -- distincao normativa:

    creation idempotency (WorkRepository.find_by_key):
        qualquer WorkItem com a mesma work_key e encontrado,
        independente do status.

    recommendation suppression (esta politica, R3):
        o WorkItem encontrado so suprime se status NOT IN
        (completed, cancelled). Um WorkItem terminal com a mesma
        work_key permanece encontravel pelo mecanismo de idempotencia
        de criacao -- so seu status terminal faz R3 passar. Um
        WorkItem de outro financial_episode/namespace nunca suprime.

work_key/origin_type/origin_reference abaixo sao materialization
metadata reserved by V1.B: contrato para um futuro POST /work-items
(fora do escopo desta fatia) -- esta funcao nunca os produz como
efeito, apenas os usa para *consultar* supressao.

Customer Intelligence 360 V1 e Outcome Intelligence V1 sao consumidos
exclusivamente por suas funcoes publicas (get_customer_context,
get_outcome_episode) -- nenhum dos 14 arquivos dessas duas fatias e
alterado por esta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from app.core.customer_context import get_customer_context
from app.core.outcome_correlation import FinancialEpisode
from app.core.outcome_correlation import OutcomeEpisodeResult
from app.core.receivable_lifecycle import business_today
from app.core.receivable_lifecycle import evaluate_receivable_lifecycle
from app.models.account import Account
from app.models.work import WorkItem
from app.repositories.work_repository import WorkRepository


RULE_VERSION = "human_escalation_eligibility_v1"

EligibilityStatus = Literal["eligible", "ineligible"]

IneligibilityReason = Literal[
    "account_paid",
    "lifecycle_not_overdue",
    "active_escalation_exists",
]

_TERMINAL_STATUSES = ("completed", "cancelled")


def _work_key(account_id: int, due_date: date) -> str:
    return (
        f"human_escalation:v1:{account_id}:{due_date.isoformat()}"
    )


def _recommendation_key(account_id: int, due_date: date) -> str:
    return (
        "human_escalation_recommendation:v1:"
        f"{account_id}:{due_date.isoformat()}"
    )


@dataclass(frozen=True)
class SupportEvidence:
    days_overdue: int
    amount: Decimal
    classification: dict | None
    average_late_days: float | None
    prior_episodes: tuple[OutcomeEpisodeResult, ...]


@dataclass(frozen=True)
class HumanEscalationEligibilityResult:
    recommendation_key: str
    episode: FinancialEpisode
    status: EligibilityStatus
    reason: IneligibilityReason | None
    suppressing_work_item_id: int | None
    support_evidence: SupportEvidence | None


def _suppressing_work_item(
    db: Session,
    *,
    account_id: int,
    due_date: date,
) -> WorkItem | None:
    repository = WorkRepository(db)

    existing = repository.find_by_key(
        scope_type="account",
        work_key=_work_key(account_id, due_date),
        account_id=account_id,
    )

    if existing is None:
        return None

    if existing.status in _TERMINAL_STATUSES:
        return None

    return existing


def _build_support_evidence(
    db: Session,
    account: Account,
    *,
    due_date: date,
    days_overdue: int,
) -> SupportEvidence:
    context = get_customer_context(db, requested_account=account)

    classification = None
    if context.behavioral_intelligence.classification is not None:
        classification = (
            context.behavioral_intelligence.classification
            .context_data
        )

    average_late_days = None
    if context.behavioral_intelligence.pattern is not None:
        average_late_days = (
            context.behavioral_intelligence.pattern
            .average_late_days
        )

    prior_episodes = tuple(
        episode
        for episode in context.episodes
        if not (
            episode.episode.account_id == account.id
            and episode.episode.due_date == due_date
        )
    )

    return SupportEvidence(
        days_overdue=days_overdue,
        amount=account.valor,
        classification=classification,
        average_late_days=average_late_days,
        prior_episodes=prior_episodes,
    )


def get_human_escalation_eligibility(
    db: Session,
    *,
    account: Account,
    due_date: date,
) -> HumanEscalationEligibilityResult:
    """
    O chamador (rota) e responsavel por buscar `account` e responder
    404 se nao existir -- esta funcao assume que ja existe.
    """

    episode = FinancialEpisode(
        account_id=account.id,
        due_date=due_date,
    )
    recommendation_key = _recommendation_key(account.id, due_date)

    if account.status.strip().lower() == "pago":
        return HumanEscalationEligibilityResult(
            recommendation_key=recommendation_key,
            episode=episode,
            status="ineligible",
            reason="account_paid",
            suppressing_work_item_id=None,
            support_evidence=None,
        )

    lifecycle = evaluate_receivable_lifecycle(
        financial_status=account.status,
        vencimento=account.vencimento,
        today=business_today(),
    )

    if lifecycle.state not in ("overdue", "overdue_alert"):
        return HumanEscalationEligibilityResult(
            recommendation_key=recommendation_key,
            episode=episode,
            status="ineligible",
            reason="lifecycle_not_overdue",
            suppressing_work_item_id=None,
            support_evidence=None,
        )

    suppressing_work_item = _suppressing_work_item(
        db,
        account_id=account.id,
        due_date=due_date,
    )

    if suppressing_work_item is not None:
        return HumanEscalationEligibilityResult(
            recommendation_key=recommendation_key,
            episode=episode,
            status="ineligible",
            reason="active_escalation_exists",
            suppressing_work_item_id=suppressing_work_item.id,
            support_evidence=None,
        )

    support_evidence = _build_support_evidence(
        db,
        account,
        due_date=due_date,
        days_overdue=lifecycle.days_overdue,
    )

    return HumanEscalationEligibilityResult(
        recommendation_key=recommendation_key,
        episode=episode,
        status="eligible",
        reason=None,
        suppressing_work_item_id=None,
        support_evidence=support_evidence,
    )
