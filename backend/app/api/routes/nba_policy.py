from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.authentication import require_permission
from app.core.nba_policy import get_nba_decision
from app.database.database import get_db
from app.models.account import Account
from app.schemas.nba_policy import NbaDecisionEvidenceResponse


router = APIRouter(
    prefix="/recommendations/next-best-action",
    tags=["Next Best Action"],
)

read_dependencies = [
    Depends(require_permission("approval:read")),
]


@router.get(
    "/accounts/{account_id}/episodes/{due_date}",
    response_model=NbaDecisionEvidenceResponse,
    dependencies=read_dependencies,
)
def get_account_nba_decision(
    account_id: int,
    due_date: date,
    db: Session = Depends(get_db),
) -> NbaDecisionEvidenceResponse:
    account = db.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    result = get_nba_decision(
        db,
        account=account,
        due_date=due_date,
    )

    return NbaDecisionEvidenceResponse.model_validate(result)
