"""Project persisted accounts onto the financial facts contract."""

from app.core.receivable_eligibility import ReceivableFacts
from app.models.account import Account


def facts_from_account(account: Account) -> ReceivableFacts:
    """Use confirmed balance fields, preserving unknown facts on legacy rows."""
    return ReceivableFacts(
        status=account.status,
        due_date=account.vencimento,
        balance_cents=(
            account.balance_cents
            if account.balance_source and account.balance_observed_at is not None
            else None
        ),
        hold=account.financial_hold,
        grace_days=account.grace_days if account.grace_days is not None else 0,
    )
