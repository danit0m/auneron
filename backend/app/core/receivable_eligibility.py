"""Versioned financial facts and eligibility for proposing overdue status."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

CONTRACT_VERSION = "receivable-facts.v1"


@dataclass(frozen=True)
class ReceivableFacts:
    status: Literal["aberto", "atrasado", "pago"]
    due_date: date
    balance_cents: int | None
    hold: bool | None
    grace_days: int = 0
    payment_confirmed: bool = False


@dataclass(frozen=True)
class OverdueEligibility:
    eligible: bool
    reason: str


def evaluate_mark_overdue(
    facts: ReceivableFacts, *, business_date: date
) -> OverdueEligibility:
    """Evaluate a proposal using the caller's business-local civil date."""
    balance = facts.balance_cents
    if (
        facts.status not in {"aberto", "atrasado", "pago"}
        or type(facts.due_date) is not date
        or type(business_date) is not date
        or type(facts.grace_days) is not int
        or facts.grace_days < 0
        or (balance is not None and (type(balance) is not int or balance < 0))
        or (facts.hold is not None and type(facts.hold) is not bool)
        or type(facts.payment_confirmed) is not bool
    ):
        return OverdueEligibility(False, "invalid_facts")
    if (facts.status == "pago" or facts.payment_confirmed) and balance not in (None, 0):
        return OverdueEligibility(False, "contradictory_facts")
    if facts.status == "pago" or facts.payment_confirmed:
        return OverdueEligibility(False, "paid")
    if facts.hold is True:
        return OverdueEligibility(False, "blocked")
    if facts.status == "atrasado":
        return OverdueEligibility(False, "already_overdue")
    if balance is None:
        return OverdueEligibility(False, "unknown_balance")
    if balance == 0:
        return OverdueEligibility(False, "zero_balance_without_payment")
    if facts.hold is None:
        return OverdueEligibility(False, "unknown_hold")
    if (business_date - facts.due_date).days <= facts.grace_days:
        return OverdueEligibility(False, "not_due")
    return OverdueEligibility(True, "eligible")
