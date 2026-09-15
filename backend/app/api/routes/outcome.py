from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.authentication import require_permission
from app.core.outcome_correlation import get_outcome_episode
from app.database.database import get_db
from app.models.account import Account
from app.schemas.outcome import OutcomeEpisodeResponse


router = APIRouter(
    prefix="/outcomes",
    tags=["Outcome Intelligence"],
)

read_dependencies = [
    Depends(require_permission("approval:read")),
]


@router.get(
    "/accounts/{account_id}/episodes/{due_date}",
    response_model=OutcomeEpisodeResponse,
    dependencies=read_dependencies,
)
def get_account_outcome_episode(
    account_id: int,
    due_date: date,
    db: Session = Depends(get_db),
) -> OutcomeEpisodeResponse:
    account = db.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    result = get_outcome_episode(
        db,
        account=account,
        due_date=due_date,
    )

    return OutcomeEpisodeResponse.model_validate(result)
