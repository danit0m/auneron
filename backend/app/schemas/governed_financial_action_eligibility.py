from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.outcome import FinancialEpisode


MarkOverdueReason = Literal[
    "due_date_mismatch",
    "already_paid",
    "status_not_open",
    "not_overdue",
]

MarkPaidReason = Literal["already_paid"]


class MarkOverdueEligibilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    eligibility_policy: str
    episode: FinancialEpisode
    structurally_available: bool
    system_recommendable: bool
    requires_external_fact: None
    reason: MarkOverdueReason | None


class MarkPaidEligibilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    eligibility_policy: str
    episode: FinancialEpisode
    structurally_available: bool
    system_recommendable: bool
    requires_external_fact: Literal["payment_observed"] | None
    reason: MarkPaidReason | None
    due_date_matches_current_vencimento: bool
