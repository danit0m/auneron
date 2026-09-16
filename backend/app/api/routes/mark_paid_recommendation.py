from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.authentication import require_permission
from app.core.governed_financial_action_eligibility import (
    get_mark_paid_eligibility,
)
from app.database.database import get_db
from app.models.account import Account
from app.schemas.governed_financial_action_eligibility import (
    MarkPaidEligibilityResponse,
)


router = APIRouter(
    prefix="/recommendations/mark-paid",
    tags=["Governed Financial Action Eligibility"],
)

read_dependencies = [
    Depends(require_permission("approval:read")),
]


@router.get(
    "/accounts/{account_id}/episodes/{due_date}",
    response_model=MarkPaidEligibilityResponse,
    dependencies=read_dependencies,
)
def get_account_mark_paid_eligibility(
    account_id: int,
    due_date: date,
    db: Session = Depends(get_db),
) -> MarkPaidEligibilityResponse:
    account = db.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    result = get_mark_paid_eligibility(
        db,
        account=account,
        due_date=due_date,
    )

    return MarkPaidEligibilityResponse.model_validate(result)
