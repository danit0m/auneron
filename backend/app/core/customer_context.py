"""
Customer Intelligence 360 V1 -- projecao read-only por composicao sobre
dados ja existentes. Nao cria, nao muta, nao persiste nada.

Congelado em tres documentos de design freeze com Tomaz em 15/09/2026:
Architecture Freeze (D1-D14), PRE-APPLY Aggregation Contract (algoritmo
de grupo, formulas de financial_summary, wrapper de comportamento).

O Auneron nao possui identidade canonica de cliente (D4). email e uma
convencao de agregacao heuristica ja usada por
client_behavior_memory_maintenance.py/client_classification.py (D5) --
NUNCA normalizada (sem lower()/trim(), D6) para nao divergir da mesma
convencao ja usada por esses dois modulos. anchor_account_id e uma
ancora tecnica (menor account_id do grupo), nunca um "customer_id"
(D7). requested_account_id (a conta que originou a consulta) permanece
sempre distinto de anchor_account_id -- ver _resolve_customer_group.

Outcome Intelligence V1 e consumido exclusivamente por
get_outcome_episode() (D9) -- nenhum dos 7 arquivos daquela fatia e
importado por nome privado nem alterado por esta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.client_behavior_memory_maintenance import (
    MEMORY_KEY as BEHAVIOR_PATTERN_MEMORY_KEY,
)
from app.core.client_classification import (
    MEMORY_KEY as CLIENT_CLASSIFICATION_MEMORY_KEY,
)
from app.core.outcome_correlation import OutcomeEpisodeResult
from app.core.outcome_correlation import get_outcome_episode
from app.core.receivable_lifecycle import ReceivableLifecycleState
from app.core.receivable_lifecycle import business_today
from app.core.receivable_lifecycle import evaluate_receivable_lifecycle
from app.models.account import Account
from app.repositories.memory_repository import MemoryRepository


AggregationMethod = str
# "email_exact_match" | "single_account_no_email"

AggregationLinkage = str
# "heuristic_email_exact_match" | "none_single_account"


@dataclass(frozen=True)
class AggregationIdentity:
    method: AggregationMethod
    value: str | None
    linkage: AggregationLinkage
    anchor_account_id: int


@dataclass(frozen=True)
class AccountSummary:
    account_id: int
    cliente: str
    valor: Decimal
    vencimento: date
    status: str
    receivable_lifecycle_state: ReceivableLifecycleState


@dataclass(frozen=True)
class FinancialSummary:
    total_exposure: Decimal
    total_exposure_account_ids: tuple[int, ...]
    overdue_count: int
    overdue_account_ids: tuple[int, ...]
    due_soon_count: int
    due_soon_account_ids: tuple[int, ...]
    next_due_date: date | None
    next_due_account_ids: tuple[int, ...]


@dataclass(frozen=True)
class BehaviorPattern:
    oldest_account_id: int
    resolved_occurrences: int
    average_late_days: float
    min_late_days: int
    max_late_days: int
    payment_rate: float
    confidence: float
    recorded_at: datetime


@dataclass(frozen=True)
class ClassificationInfo:
    context_data: dict
    recorded_at: datetime


@dataclass(frozen=True)
class BehavioralIntelligence:
    classification_status: str
    # "not_classified_yet" | "classified"
    pattern: BehaviorPattern | None
    classification: ClassificationInfo | None


@dataclass(frozen=True)
class CustomerContextResult:
    requested_account_id: int
    aggregation_identity: AggregationIdentity
    accounts: tuple[AccountSummary, ...]
    financial_summary: FinancialSummary
    behavioral_intelligence: BehavioralIntelligence
    episodes: tuple[OutcomeEpisodeResult, ...]


def _resolve_customer_group(
    db: Session,
    requested_account: Account,
) -> tuple[
    list[Account],
    int,
    AggregationMethod,
    AggregationLinkage,
]:
    if requested_account.email is None:
        return (
            [requested_account],
            requested_account.id,
            "single_account_no_email",
            "none_single_account",
        )

    accounts = list(
        db.execute(
            select(Account)
            .where(Account.email == requested_account.email)
            .order_by(Account.id.asc())
        )
        .scalars()
        .all()
    )

    return (
        accounts,
        accounts[0].id,
        "email_exact_match",
        "heuristic_email_exact_match",
    )


def _account_summary(
    account: Account,
    *,
    today: date,
) -> AccountSummary:
    lifecycle = evaluate_receivable_lifecycle(
        financial_status=account.status,
        vencimento=account.vencimento,
        today=today,
    )

    return AccountSummary(
        account_id=account.id,
        cliente=account.cliente,
        valor=account.valor,
        vencimento=account.vencimento,
        status=account.status,
        receivable_lifecycle_state=lifecycle.state,
    )


def _financial_summary(
    accounts: list[Account],
    *,
    today: date,
) -> FinancialSummary:
    lifecycle_by_account = {
        account.id: evaluate_receivable_lifecycle(
            financial_status=account.status,
            vencimento=account.vencimento,
            today=today,
        )
        for account in accounts
    }

    total_exposure = sum(
        (account.valor for account in accounts),
        Decimal("0"),
    )
    total_exposure_account_ids = tuple(
        account.id for account in accounts
    )

    overdue_ids = tuple(
        account.id
        for account in accounts
        if lifecycle_by_account[account.id].state
        in ("overdue", "overdue_alert")
    )
    due_soon_ids = tuple(
        account.id
        for account in accounts
        if lifecycle_by_account[account.id].state == "due_soon"
    )

    open_accounts = [
        account
        for account in accounts
        if lifecycle_by_account[account.id].state != "paid"
    ]

    next_due_date = (
        min(account.vencimento for account in open_accounts)
        if open_accounts
        else None
    )

    next_due_account_ids = (
        tuple(
            account.id
            for account in open_accounts
            if account.vencimento == next_due_date
        )
        if next_due_date is not None
        else ()
    )

    return FinancialSummary(
        total_exposure=total_exposure,
        total_exposure_account_ids=total_exposure_account_ids,
        overdue_count=len(overdue_ids),
        overdue_account_ids=overdue_ids,
        due_soon_count=len(due_soon_ids),
        due_soon_account_ids=due_soon_ids,
        next_due_date=next_due_date,
        next_due_account_ids=next_due_account_ids,
    )


def _behavioral_intelligence(
    db: Session,
    *,
    anchor_account_id: int,
) -> BehavioralIntelligence:
    repository = MemoryRepository(db)

    pattern_memory = repository.find_active_by_key(
        scope_type="account",
        memory_key=BEHAVIOR_PATTERN_MEMORY_KEY,
        account_id=anchor_account_id,
    )

    pattern = None

    if pattern_memory is not None:
        context = pattern_memory.context_data or {}

        pattern = BehaviorPattern(
            oldest_account_id=context["oldest_account_id"],
            resolved_occurrences=context[
                "ocorrencias_resolvidas"
            ],
            average_late_days=context["atraso_medio_dias"],
            min_late_days=context["atraso_min_dias"],
            max_late_days=context["atraso_max_dias"],
            payment_rate=context["taxa_pagamento"],
            confidence=context["confidence"],
            recorded_at=pattern_memory.created_at,
        )

    classification_memory = repository.find_active_by_key(
        scope_type="account",
        memory_key=CLIENT_CLASSIFICATION_MEMORY_KEY,
        account_id=anchor_account_id,
    )

    classification = None
    classification_status = "not_classified_yet"

    if classification_memory is not None:
        classification = ClassificationInfo(
            context_data=(
                classification_memory.context_data or {}
            ),
            recorded_at=classification_memory.created_at,
        )
        classification_status = "classified"

    return BehavioralIntelligence(
        classification_status=classification_status,
        pattern=pattern,
        classification=classification,
    )


def get_customer_context(
    db: Session,
    *,
    requested_account: Account,
) -> CustomerContextResult:
    """
    O chamador (rota) e responsavel por buscar `requested_account` e
    responder 404 se nao existir -- esta funcao assume que ja existe.
    """

    today = business_today()

    accounts, anchor_account_id, method, linkage = (
        _resolve_customer_group(db, requested_account)
    )

    aggregation_identity = AggregationIdentity(
        method=method,
        value=requested_account.email,
        linkage=linkage,
        anchor_account_id=anchor_account_id,
    )

    account_summaries = tuple(
        _account_summary(account, today=today)
        for account in accounts
    )

    financial_summary = _financial_summary(
        accounts, today=today
    )

    behavioral_intelligence = _behavioral_intelligence(
        db, anchor_account_id=anchor_account_id
    )

    episodes = tuple(
        get_outcome_episode(
            db,
            account=account,
            due_date=account.vencimento,
        )
        for account in accounts
    )

    return CustomerContextResult(
        requested_account_id=requested_account.id,
        aggregation_identity=aggregation_identity,
        accounts=account_summaries,
        financial_summary=financial_summary,
        behavioral_intelligence=behavioral_intelligence,
        episodes=episodes,
    )
