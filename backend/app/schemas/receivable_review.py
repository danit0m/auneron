"""Operator-confirmed financial facts for a receivable."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BalanceReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    cents: Annotated[int, Field(strict=True, ge=0, le=9_223_372_036_854_775_807)]
    evidence: Annotated[str, Field(min_length=1, max_length=240)]


class HoldReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    blocked: Annotated[bool, Field(strict=True)]
    reason: Annotated[str, Field(min_length=1, max_length=255)]


class ReceivableReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    balance: BalanceReview | None = None
    hold: HoldReview | None = None

    @model_validator(mode="after")
    def requires_review(self):
        if self.balance is None and self.hold is None:
            raise ValueError("Informe saldo ou bloqueio para revisar.")
        return self


class ReceivableReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    balance_cents: int | None
    balance_source: str | None
    balance_observed_at: datetime | None
    balance_reviewed_by: int | None
    financial_hold: bool | None
    hold_reason: str | None
    hold_reviewed_by: int | None
    hold_reviewed_at: datetime | None
