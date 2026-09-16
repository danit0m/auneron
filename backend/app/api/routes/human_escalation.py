from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.authentication import require_permission
from app.core.human_escalation_eligibility import (
    get_human_escalation_eligibility,
)
from app.database.database import get_db
from app.models.account import Account
from app.schemas.human_escalation import (
    HumanEscalationEligibilityResponse,
)


router = APIRouter(
    prefix="/recommendations/human-escalation",
    tags=["Human Escalation Recommendation"],
)

read_dependencies = [
    Depends(require_permission("approval:read")),
]


@router.get(
    "/accounts/{account_id}/episodes/{due_date}",
    response_model=HumanEscalationEligibilityResponse,
    dependencies=read_dependencies,
)
def get_account_human_escalation_eligibility(
    account_id: int,
    due_date: date,
    db: Session = Depends(get_db),
) -> HumanEscalationEligibilityResponse:
    account = db.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    result = get_human_escalation_eligibility(
        db,
        account=account,
        due_date=due_date,
    )

    return HumanEscalationEligibilityResponse.model_validate(
        result
    )
