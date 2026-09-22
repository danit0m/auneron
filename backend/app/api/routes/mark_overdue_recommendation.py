from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalConsumptionConflictError
from app.core.approval_errors import ApprovalExpiredError
from app.core.approval_errors import ApprovalNotFoundError
from app.core.approval_errors import ApprovalRequiredError
from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.core.authentication import AuthenticatedSession
from app.core.authentication import require_permission
from app.core.governed_financial_action_eligibility import (
    get_mark_overdue_eligibility,
)
from app.core.skill_errors import SkillAuthorizationError
from app.core.skill_errors import SkillNotFoundError
from app.core.skill_errors import SkillScopeNotFoundError
from app.core.skill_errors import SkillValidationError
from app.database.database import get_db
from app.models.account import Account
from app.schemas.approval import ApprovalRequestResponse
from app.schemas.work import WorkResponse
from app.schemas.governed_financial_action_eligibility import (
    HumanAccountMarkOverdueExecutionResponse,
)
from app.schemas.governed_financial_action_eligibility import (
    HumanAccountMarkOverdueMaterializationResponse,
)
from app.schemas.governed_financial_action_eligibility import (
    MarkOverdueEligibilityResponse,
)
from app.services.human_account_mark_overdue_execution_service import (
    HumanAccountMarkOverdueExecutionService,
)
from app.services.human_account_mark_overdue_execution_service import (
    HumanMarkOverdueApprovalMissingError,
)
from app.services.human_account_mark_overdue_execution_service import (
    HumanMarkOverdueApprovalRejectedError,
)
from app.services.human_account_mark_overdue_execution_service import (
    HumanMarkOverdueWorkItemConflictError,
)
from app.services.human_account_mark_overdue_execution_service import (
    HumanMarkOverdueWorkItemNotFoundError,
)
from app.services.human_account_mark_overdue_materialization_service import (
    HumanAccountMarkOverdueMaterializationService,
)
from app.services.human_account_mark_overdue_materialization_service import (
    HumanMarkOverdueCatalogError,
)
from app.services.human_account_mark_overdue_materialization_service import (
    HumanMarkOverdueMaterializationConflictError,
)
from app.services.human_account_mark_overdue_materialization_service import (
    HumanMarkOverdueNotRecommendableError,
)
from app.services.human_account_mark_overdue_materialization_service import (
    HumanMarkOverdueReopeningNotSupportedError,
)
from app.services.human_account_mark_overdue_materialization_service import (
    SKILL_KEY,
)


router = APIRouter(
    prefix="/recommendations/mark-overdue",
    tags=["Governed Financial Action Eligibility"],
)

read_dependencies = [
    Depends(require_permission("approval:read")),
]


@router.get(
    "/accounts/{account_id}/episodes/{due_date}",
    response_model=MarkOverdueEligibilityResponse,
    dependencies=read_dependencies,
)
def get_account_mark_overdue_eligibility(
    account_id: int,
    due_date: date,
    db: Session = Depends(get_db),
) -> MarkOverdueEligibilityResponse:
    account = db.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    result = get_mark_overdue_eligibility(
        db,
        account=account,
        due_date=due_date,
    )

    return MarkOverdueEligibilityResponse.model_validate(result)


@router.post(
    "/accounts/{account_id}/episodes/{due_date}/materialize",
    response_model=HumanAccountMarkOverdueMaterializationResponse,
)
def materialize_account_mark_overdue(
    account_id: int,
    due_date: date,
    response: Response,
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:create")
    ),
    db: Session = Depends(get_db),
) -> HumanAccountMarkOverdueMaterializationResponse:
    account = db.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    service = HumanAccountMarkOverdueMaterializationService(db)

    try:
        result = service.materialize(
            account=account,
            due_date=due_date,
            authenticated=authenticated,
        )
    except HumanMarkOverdueNotRecommendableError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "account.mark_overdue não é mais recomendável "
                f"(reason={error.reason})."
            ),
        ) from error
    except HumanMarkOverdueReopeningNotSupportedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except HumanMarkOverdueCatalogError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except HumanMarkOverdueMaterializationConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except SkillNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except (
        SkillAuthorizationError,
        SkillScopeNotFoundError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    except SkillValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    response.status_code = (
        status.HTTP_200_OK
        if result.duplicate
        else status.HTTP_201_CREATED
    )

    return HumanAccountMarkOverdueMaterializationResponse(
        work_item=WorkResponse.from_work_item(result.work_item),
        approval_request=ApprovalRequestResponse.from_request(
            result.approval_request,
            skill_key=SKILL_KEY,
        ),
        created=result.created,
        duplicate=result.duplicate,
    )


@router.post(
    "/accounts/{account_id}/episodes/{due_date}/execute",
    response_model=HumanAccountMarkOverdueExecutionResponse,
)
def execute_account_mark_overdue(
    account_id: int,
    due_date: date,
    authenticated: AuthenticatedSession = Depends(
        require_permission("skill:execute")
    ),
    db: Session = Depends(get_db),
) -> HumanAccountMarkOverdueExecutionResponse:
    service = HumanAccountMarkOverdueExecutionService(db)

    try:
        result = service.execute(
            account_id=account_id,
            due_date=due_date,
            authority_user_id=authenticated.user.id,
        )
    except HumanMarkOverdueWorkItemNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except HumanMarkOverdueApprovalMissingError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except HumanMarkOverdueApprovalRejectedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except HumanMarkOverdueWorkItemConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ApprovalNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except ApprovalRequiredError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ApprovalExpiredError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ApprovalConsumptionConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ApprovalStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ApprovalAuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    except (
        SkillAuthorizationError,
        SkillScopeNotFoundError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    except (
        ApprovalValidationError,
        SkillValidationError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    return HumanAccountMarkOverdueExecutionResponse(
        work_item_id=result.work_item_id,
        approval_request_id=result.approval_request_id,
        approval_consumption_id=result.approval_consumption_id,
        invocation_id=result.invocation_id,
        invocation_status=result.invocation_status,
        duplicate=result.duplicate,
        output=result.output,
    )
