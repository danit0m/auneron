from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.outcome import FinancialEpisode
from app.schemas.outcome import Money
from app.schemas.outcome import OutcomeEpisodeResponse


EligibilityStatus = Literal["eligible", "ineligible"]

IneligibilityReason = Literal[
    "account_paid",
    "lifecycle_not_overdue",
    "active_escalation_exists",
]


class SupportEvidence(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    days_overdue: int
    amount: Money
    classification: dict | None
    average_late_days: float | None
    prior_episodes: list[OutcomeEpisodeResponse]


class HumanEscalationEligibilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recommendation_key: str
    episode: FinancialEpisode
    status: EligibilityStatus
    reason: IneligibilityReason | None
    suppressing_work_item_id: int | None
    support_evidence: SupportEvidence | None
