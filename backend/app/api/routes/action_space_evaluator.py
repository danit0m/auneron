from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.action_space_evaluator import get_action_space_evaluation
from app.core.authentication import require_permission
from app.database.database import get_db
from app.models.account import Account
from app.schemas.action_space_evaluator import (
    ActionSpaceEvaluationResponse,
)


router = APIRouter(
    prefix="/recommendations/action-space",
    tags=["Action Space Evaluator"],
)

read_dependencies = [
    Depends(require_permission("approval:read")),
]


@router.get(
    "/accounts/{account_id}/episodes/{due_date}",
    response_model=ActionSpaceEvaluationResponse,
    dependencies=read_dependencies,
)
def get_account_action_space_evaluation(
    account_id: int,
    due_date: date,
    db: Session = Depends(get_db),
) -> ActionSpaceEvaluationResponse:
    account = db.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    result = get_action_space_evaluation(
        db,
        account=account,
        due_date=due_date,
    )

    return ActionSpaceEvaluationResponse.model_validate(result)
