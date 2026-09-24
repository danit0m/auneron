"""
NBA V1 -- Next Best Action Policy Normalization / Decision Table.

Congelado em Architecture Freeze / PRE-APPLY Mechanical-Composition-RBAC
Gate / Executable Contract (com as 2 emendas finais: applied_rules nao
perde informacao quando os dois gatilhos de R3 sao verdadeiros, e
Decimal preservado ate a comparacao, sem degradar para float) com
Tomaz em 16/09/2026, a partir do baseline `250d15b`.

O NBA NUNCA recalcula elegibilidade -- get_action_space_evaluation() e
autoridade EXCLUSIVA sobre actions/recommendable_actions/no_action
(V1.B + V1.C, ja fechados, nunca modificados por este arquivo). O NBA
compoe fontes publicas read-only adicionais (evaluate_receivable_lifecycle,
Account.valor, get_customer_context) apenas para FATOS usados na
escolha entre acoes ja recomendaveis -- nunca para decidir
elegibilidade.

Invariante mecanica: selected_actions e sempre subconjunto de
recommendable_actions.

Politica R0-R3 (nba_policy_v1):

    R0  recommendable_actions == () -> no_action
    R1  recommendable_actions == (mark_overdue,) -> single_action(mark_overdue)
    R2  recommendable_actions == (escalate_to_human,) -> single_action(escalate)
    R3  ambas presentes -> escalation_trigger decide bundle vs
        single_action(mark_overdue). NUNCA existe um branch que
        descarte mark_overdue mantendo so escalate_to_human quando
        ambas estao em recommendable_actions -- esse resultado so pode
        vir do Action Space upstream (via R2), nunca de o NBA
        "eliminar" mark_overdue.

escalation_trigger = prolonged_overdue_trigger OR high_exposure_early_trigger
-- os dois predicados sao avaliados e registrados SEPARADAMENTE em
applied_rules; quando ambos sao verdadeiros, os dois nomes aparecem
juntos (nunca apenas um, mesmo quando o outro tambem se aplica).

recurrence/history entra em observed_facts (explicavel via
get_customer_context) mas NUNCA participa de nenhum branch de decisao
nesta calibracao -- dois episodios identicos em todos os fatos
decisorios, com recurrence completamente diferente, produzem
exatamente a mesma decision e os mesmos applied_rules.

account.mark_paid nunca aparece em recommendable_actions hoje
(system_recommendable e constante False em V1.C, confirmado por
Survey Q1-Q5 com evidencia mecanica -- ausencia comprovada de qualquer
corredor de payment_observed no dominio). Portanto nunca e
selecionavel pelo NBA V1 atual -- nao e uma restricao imposta aqui, e
uma consequencia herdada do contrato ja congelado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.action_space_evaluator import ActionSpaceEvaluation
from app.core.action_space_evaluator import get_action_space_evaluation
from app.core.config import settings
from app.core.customer_context import get_customer_context
from app.core.outcome_correlation import FinancialEpisode
from app.core.receivable_lifecycle import business_today
from app.core.receivable_lifecycle import evaluate_receivable_lifecycle
from app.models.account import Account
from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalRequest
from app.models.business_effect_verification import (
    BusinessEffectVerification,
)


POLICY_VERSION = "nba_policy_v1"

MARK_OVERDUE_ACTION_KEY = "account.mark_overdue"
ESCALATE_TO_HUMAN_ACTION_KEY = "escalate_to_human"

# R4 -- DW-4 V1 (Design Freeze 2026-09-24). Sinal advisory, paralelo a
# R0-R3, nunca participa de selected_actions/recommendable_actions.
PRIOR_EFFECT_CONTRADICTION_REASON = "prior_effect_contradiction"
PRIOR_EFFECT_CONTRADICTION_RULE = "prior_effect_contradiction_review"

DecisionType = Literal["single_action", "action_bundle", "no_action"]


@dataclass(frozen=True)
class RecurrenceFacts:
    average_late_days: float | None
    resolved_occurrences: int | None


@dataclass(frozen=True)
class ObservedFacts:
    days_overdue: int
    amount: Decimal
    recurrence: RecurrenceFacts | None
    active_escalation: bool


@dataclass(frozen=True)
class CalibrationSnapshot:
    version: str
    critical_overdue_reference_days: int
    early_high_exposure_reference_day: int
    absolute_high_value_reference: Decimal
    operational_cost_floor_reference: Decimal


@dataclass(frozen=True)
class Decision:
    decision_type: DecisionType
    selected_actions: tuple[str, ...]


@dataclass(frozen=True)
class NbaDecisionEvidence:
    episode: FinancialEpisode
    action_space: ActionSpaceEvaluation
    observed_facts: ObservedFacts
    policy_version: str
    calibration: CalibrationSnapshot
    applied_rules: tuple[str, ...]
    decision: Decision
    requires_human_review: bool
    human_review_reasons: tuple[str, ...]


def _calibration_snapshot() -> CalibrationSnapshot:
    return CalibrationSnapshot(
        version=settings.nba_calibration_version,
        critical_overdue_reference_days=(
            settings.nba_critical_overdue_reference_days
        ),
        early_high_exposure_reference_day=(
            settings.nba_early_high_exposure_reference_day
        ),
        absolute_high_value_reference=(
            settings.nba_absolute_high_value_reference
        ),
        operational_cost_floor_reference=(
            settings.nba_operational_cost_floor_reference
        ),
    )


def _observed_facts(
    db: Session,
    *,
    account: Account,
    action_space: ActionSpaceEvaluation,
) -> ObservedFacts:
    lifecycle = evaluate_receivable_lifecycle(
        financial_status=account.status,
        vencimento=account.vencimento,
        today=business_today(),
    )

    context = get_customer_context(db, requested_account=account)
    pattern = context.behavioral_intelligence.pattern
    recurrence = (
        RecurrenceFacts(
            average_late_days=pattern.average_late_days,
            resolved_occurrences=pattern.resolved_occurrences,
        )
        if pattern is not None
        else None
    )

    escalate_action = next(
        action
        for action in action_space.actions
        if action.action_key == ESCALATE_TO_HUMAN_ACTION_KEY
    )
    active_escalation = (
        escalate_action.reason == "active_escalation_exists"
    )

    return ObservedFacts(
        days_overdue=lifecycle.days_overdue,
        amount=account.valor,
        recurrence=recurrence,
        active_escalation=active_escalation,
    )


def _decide(
    *,
    action_space: ActionSpaceEvaluation,
    observed_facts: ObservedFacts,
    calibration: CalibrationSnapshot,
) -> tuple[Decision, tuple[str, ...]]:
    candidates = action_space.recommendable_actions

    if candidates == ():
        return (
            Decision(
                decision_type="no_action",
                selected_actions=(),
            ),
            (),
        )

    if candidates == (MARK_OVERDUE_ACTION_KEY,):
        return (
            Decision(
                decision_type="single_action",
                selected_actions=(MARK_OVERDUE_ACTION_KEY,),
            ),
            ("mark_overdue_only_candidate",),
        )

    if candidates == (ESCALATE_TO_HUMAN_ACTION_KEY,):
        return (
            Decision(
                decision_type="single_action",
                selected_actions=(ESCALATE_TO_HUMAN_ACTION_KEY,),
            ),
            ("escalate_to_human_only_candidate",),
        )

    # Unico caso restante: as duas presentes. mark_paid nunca entra em
    # recommendable_actions (V1.C), e actions sempre tem exatamente 3
    # itens -- nao ha uma quarta combinacao possivel.
    prolonged_overdue_trigger = (
        observed_facts.days_overdue
        > calibration.critical_overdue_reference_days
        and observed_facts.amount
        > calibration.operational_cost_floor_reference
    )
    high_exposure_early_trigger = (
        observed_facts.days_overdue
        >= calibration.early_high_exposure_reference_day
        and observed_facts.amount
        >= calibration.absolute_high_value_reference
    )

    applied_rules: list[str] = []

    if prolonged_overdue_trigger:
        applied_rules.append("prolonged_overdue_escalation")
    if high_exposure_early_trigger:
        applied_rules.append("high_exposure_early_escalation")

    if applied_rules:
        return (
            Decision(
                decision_type="action_bundle",
                selected_actions=(
                    MARK_OVERDUE_ACTION_KEY,
                    ESCALATE_TO_HUMAN_ACTION_KEY,
                ),
            ),
            tuple(applied_rules),
        )

    return (
        Decision(
            decision_type="single_action",
            selected_actions=(MARK_OVERDUE_ACTION_KEY,),
        ),
        ("mark_overdue_precedence",),
    )


def _human_mark_overdue_episode_key(
    account_id: int, due_date: date
) -> str:
    # Duplicado deliberadamente do template usado em
    # human_account_mark_overdue_materialization_service.py (arquivo
    # protegido, nao editado neste V1 -- ver DW-4 Design Freeze). Se
    # aquele produtor mudar o formato da chave, este lookup precisa ser
    # atualizado manualmente; nao ha primitive compartilhada. Isto NAO
    # e uma alegacao de que idempotency_key e UNIQUE local em
    # ApprovalRequest -- a unicidade pratica vem de
    # uq_work_items_account_key em WorkItem, uma invariante de outro
    # modulo.
    return (
        "human_mark_overdue_approval:v1:"
        f"{account_id}:{due_date.isoformat()}"
    )


def _lookup_episode_contradiction(
    db: Session,
    *,
    account_id: int,
    due_date: date,
) -> BusinessEffectVerification | None:
    exact_key = _human_mark_overdue_episode_key(
        account_id, due_date
    )
    statement = (
        select(BusinessEffectVerification)
        .join(
            ApprovalConsumption,
            ApprovalConsumption.id
            == BusinessEffectVerification.approval_consumption_id,
        )
        .join(
            ApprovalRequest,
            ApprovalRequest.id
            == ApprovalConsumption.approval_request_id,
        )
        .where(
            ApprovalRequest.idempotency_key == exact_key,
            BusinessEffectVerification.target_account_id
            == account_id,
            BusinessEffectVerification.skill_key
            == MARK_OVERDUE_ACTION_KEY,
        )
    )
    # scalar_one_or_none() propaga MultipleResultsFound se a
    # cardinalidade zero-ou-um esperada para o episodio for quebrada --
    # fail-closed deliberado, nunca escolher first()/latest(). Essa
    # cardinalidade depende da invariante cross-module de WorkItem,
    # alem das constraints downstream; ApprovalRequest.idempotency_key
    # nao e globalmente UNIQUE.
    return db.execute(statement).scalar_one_or_none()


def _evaluate_prior_effect_review(
    verification: BusinessEffectVerification | None,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    if (
        verification is not None
        and verification.result == "contradicted"
    ):
        return (
            True,
            (PRIOR_EFFECT_CONTRADICTION_REASON,),
            (PRIOR_EFFECT_CONTRADICTION_RULE,),
        )
    return (False, (), ())


def get_nba_decision(
    db: Session,
    *,
    account: Account,
    due_date: date,
) -> NbaDecisionEvidence:
    """
    O chamador (rota) e responsavel por buscar `account` e responder
    404 se nao existir -- esta funcao assume que ja existe.
    """

    action_space = get_action_space_evaluation(
        db, account=account, due_date=due_date
    )
    calibration = _calibration_snapshot()
    observed_facts = _observed_facts(
        db, account=account, action_space=action_space
    )

    decision, applied_rules = _decide(
        action_space=action_space,
        observed_facts=observed_facts,
        calibration=calibration,
    )

    prior_verification = _lookup_episode_contradiction(
        db, account_id=account.id, due_date=due_date
    )
    (
        requires_human_review,
        human_review_reasons,
        review_rules,
    ) = _evaluate_prior_effect_review(prior_verification)

    return NbaDecisionEvidence(
        episode=FinancialEpisode(
            account_id=account.id, due_date=due_date
        ),
        action_space=action_space,
        observed_facts=observed_facts,
        policy_version=POLICY_VERSION,
        calibration=calibration,
        applied_rules=applied_rules + review_rules,
        decision=decision,
        requires_human_review=requires_human_review,
        human_review_reasons=human_review_reasons,
    )
