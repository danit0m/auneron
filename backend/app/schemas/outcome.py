from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.core.money import money_to_json_number


Linkage = Literal[
    "direct",
    "correlated",
    "absent",
    "unresolved_due_date_change",
]

EvidenceSource = Literal[
    "knowledge",
    "authenticated_advisory_proposal",
    "approval_request",
    "approval_decision",
    "work_item",
    "work_skill_execution",
    "skill_invocation",
    "account_event",
]

ProvenanceClass = Literal[
    "fact",
    "derived",
    "correlated",
    "inferred",
]
# "inferred" existe apenas para documentar a taxonomia completa do
# Architecture Freeze (D4) -- nenhum campo do V1 produz esse valor.

RuleVersion = Literal["outcome_correlation_v1"]

Money = Annotated[
    Decimal,
    PlainSerializer(
        money_to_json_number,
        return_type=float,
        when_used="json",
    ),
]


class Evidence(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: EvidenceSource
    id: int
    linkage: Linkage


class FinancialEpisode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: int
    due_date: date


class DetectionOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    linkage: Linkage
    first_observed_at: datetime | None
    evidence: list[Evidence] = []


class RecommendationOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    linkage: Linkage
    created_at: datetime | None
    latest_proposal_attempt: int | None
    evidence: list[Evidence] = []


class ApprovalOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    linkage: Linkage
    status: str | None
    decided_at: datetime | None
    decided_by_reference: str | None
    approval_source_attempt: int | None
    attempt_mismatch: bool
    evidence: list[Evidence] = []


class ExecutionOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    linkage: Linkage
    status: str | None
    finished_at: datetime | None
    sourced_from_attempt: int | None
    evidence: list[Evidence] = []


class PaymentOutcome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    linkage: Linkage
    rule_version: RuleVersion
    occurred_at: datetime | None
    evidence: list[Evidence] = []
    candidate_evidence: list[Evidence] = []


class DerivedMetrics(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    days_detection_to_payment: int | None
    provenance_class: Literal["derived"] = "derived"


class AmountInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    value: Money
    source: Literal["account.valor"] = "account.valor"
    provenance_class: Literal["fact"] = "fact"


class OutcomeEpisodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    episode: FinancialEpisode
    detection: DetectionOutcome
    recommendation: RecommendationOutcome
    approval: ApprovalOutcome
    execution: ExecutionOutcome
    payment: PaymentOutcome
    derived: DerivedMetrics
    amount: AmountInfo
