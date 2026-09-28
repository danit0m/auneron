from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict


class GovernedOperationsSummaryPeriod(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: datetime
    end: datetime
    days: int


class AutonomousEffectVerificationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    verified: int
    checked_other: int
    not_yet_checked: int
    total: int
    verification_rate: float | None


class GovernedOperationsSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: GovernedOperationsSummaryPeriod
    eligible_accounts_identified: int
    autonomous_dispositions: int
    human_governed_dispositions: int
    autonomous_disposition_rate: float | None
    autonomous_effect_verification: AutonomousEffectVerificationSummary
    pending_overdue_accounts_now: int
    estimate: None = None
