from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.authentication import AuthenticatedSession
from app.core.authentication import require_permission
from app.core.human_escalation_eligibility import (
    get_human_escalation_eligibility,
)
from app.database.database import get_db
from app.models.account import Account
from app.schemas.human_escalation import (
    HumanEscalationEligibilityResponse,
)
from app.schemas.human_escalation import (
    HumanEscalationMaterializationResponse,
)
from app.schemas.work import WorkResponse
from app.services.human_escalation_materialization_service import (
    HumanEscalationMaterializationService,
)
from app.services.human_escalation_materialization_service import (
    HumanEscalationNotRecommendableError,
)
from app.services.human_escalation_materialization_service import (
    HumanEscalationReopeningNotSupportedError,
)
from app.services.work_service import WorkActor


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


def _actor(
    authenticated: AuthenticatedSession,
) -> WorkActor:
    return WorkActor(
        actor_type="user",
        actor_reference=f"user:{authenticated.user.id}",
        actor_user_id=authenticated.user.id,
    )


@router.post(
    "/accounts/{account_id}/episodes/{due_date}/materialize",
    response_model=HumanEscalationMaterializationResponse,
)
def materialize_account_human_escalation(
    account_id: int,
    due_date: date,
    response: Response,
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:create")
    ),
    db: Session = Depends(get_db),
) -> HumanEscalationMaterializationResponse:
    account = db.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    service = HumanEscalationMaterializationService(db)

    try:
        result = service.materialize(
            account=account,
            due_date=due_date,
            actor=_actor(authenticated),
        )
    except HumanEscalationNotRecommendableError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Escalonamento para humano não é mais "
                f"recomendável (reason={error.reason})."
            ),
        ) from error
    except HumanEscalationReopeningNotSupportedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    response.status_code = (
        status.HTTP_200_OK
        if result.duplicate
        else status.HTTP_201_CREATED
    )

    return HumanEscalationMaterializationResponse(
        work_item=WorkResponse.from_work_item(
            result.work_item
        ),
        created=result.created,
        duplicate=result.duplicate,
    )
