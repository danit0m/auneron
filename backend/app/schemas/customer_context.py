from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.receivable_lifecycle import ReceivableLifecycleState
from app.schemas.account import AccountStatus
from app.schemas.account import ClientClassificationDetail
from app.schemas.outcome import OutcomeEpisodeResponse


AggregationMethod = Literal[
    "email_exact_match",
    "single_account_no_email",
]

AggregationLinkage = Literal[
    "heuristic_email_exact_match",
    "none_single_account",
]


class AggregationIdentity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    method: AggregationMethod
    value: str | None
    linkage: AggregationLinkage
    anchor_account_id: int


class CustomerAccountSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: int
    cliente: str
    valor: Decimal
    vencimento: date
    status: AccountStatus
    receivable_lifecycle_state: ReceivableLifecycleState


class FinancialSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_exposure: Decimal
    total_exposure_account_ids: list[int]
    overdue_count: int
    overdue_account_ids: list[int]
    due_soon_count: int
    due_soon_account_ids: list[int]
    next_due_date: date | None
    next_due_account_ids: list[int]
    provenance_class: Literal["derived"] = "derived"


class ClientBehaviorPatternSummary(BaseModel):
    """
    Espelha 1:1 o context_data persistido pela Fatia 1
    (_pattern_context_data, client_behavior_memory_maintenance.py),
    exceto `email` (ja exposto em aggregation_identity.value) e
    `cycles` (nao persistido em context_data -- vira MemoryEvidence
    separado, fora do escopo deste wrapper).
    """

    model_config = ConfigDict(from_attributes=True)

    oldest_account_id: int
    resolved_occurrences: int
    average_late_days: float
    min_late_days: int
    max_late_days: int
    payment_rate: float
    confidence: float
    recorded_at: datetime


class BehavioralIntelligence(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    classification_status: Literal[
        "not_classified_yet",
        "classified",
    ]
    pattern: ClientBehaviorPatternSummary | None
    classification: ClientClassificationDetail | None


class EpisodeOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: int
    due_date: date
    outcome: OutcomeEpisodeResponse


class Customer360Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    requested_account_id: int
    aggregation_identity: AggregationIdentity
    accounts: list[CustomerAccountSummary]
    financial_summary: FinancialSummary
    behavioral_intelligence: BehavioralIntelligence
    episodes: list[EpisodeOutcome]
