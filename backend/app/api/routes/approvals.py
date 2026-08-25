import logging
from typing import NoReturn

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from fastapi import Response
from fastapi import status
from sqlalchemy.orm import Session

from app.core.approval_authorization import (
    authorize_approval_decision,
)
from app.core.approval_authorization import (
    authorize_approval_read,
)
from app.core.approval_authorization import (
    visible_required_permissions,
)
from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalConflictError
from app.core.approval_errors import ApprovalElevationRequiredError
from app.core.approval_errors import ApprovalError
from app.core.approval_errors import ApprovalExpiredError
from app.core.approval_errors import ApprovalIdempotencyConflictError
from app.core.approval_errors import ApprovalNotFoundError
from app.core.approval_errors import ApprovalStateError
from app.core.approval_errors import ApprovalValidationError
from app.core.approval_observability import log_approval_event
from app.core.authentication import AuthenticatedSession
from app.core.authentication import is_session_elevated
from app.core.authentication import require_permission
from app.core.skill_authorization import authorize_skill_execution
from app.core.skill_errors import SkillAuthorizationError
from app.core.skill_errors import SkillNotFoundError
from app.core.skill_errors import SkillStateError
from app.core.skill_errors import SkillValidationError
from app.database.database import get_db
from app.schemas.approval import ApprovalCreateRequest
from app.schemas.approval import ApprovalCreationResponse
from app.schemas.approval import ApprovalDecisionRequest
from app.schemas.approval import ApprovalDecisionResponse
from app.schemas.approval import ApprovalDecisionResultResponse
from app.schemas.approval import ApprovalDetailsResponse
from app.schemas.approval import ApprovalListResponse
from app.schemas.approval import ApprovalRequestResponse
from app.schemas.approval import ApprovalRiskLevel
from app.schemas.approval import ApprovalStatus
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import ApprovalService


router = APIRouter(
    prefix="/approvals",
    tags=["Approval & Autonomy"],
)

approval_logger = logging.getLogger(
    "auneron.approval.api"
)


def get_approval_service(
    db: Session = Depends(get_db),
) -> ApprovalService:
    return ApprovalService(db)


def get_approval_idempotency_key(
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
) -> str:
    return idempotency_key


def _raise_approval_http_error(
    error: ApprovalError,
    *,
    operation: str,
    user_id: int,
    request_id: int | None = None,
    version_id: int | None = None,
) -> NoReturn:
    if isinstance(
        error,
        ApprovalNotFoundError,
    ):
        status_code = status.HTTP_404_NOT_FOUND
        code = "approval_not_found"
        message = (
            "Solicitação de aprovação não encontrada."
        )
    elif isinstance(
        error,
        ApprovalElevationRequiredError,
    ):
        status_code = status.HTTP_403_FORBIDDEN
        code = "approval_elevation_required"
        message = (
            "Esta aprovação exige sessão elevada."
        )
    elif isinstance(
        error,
        ApprovalAuthorizationError,
    ):
        status_code = status.HTTP_403_FORBIDDEN
        code = "approval_forbidden"
        message = (
            "Operação de aprovação não autorizada."
        )
    elif isinstance(
        error,
        ApprovalIdempotencyConflictError,
    ):
        status_code = status.HTTP_409_CONFLICT
        code = "approval_idempotency_conflict"
        message = (
            "A chave idempotente conflita "
            "com outra solicitação."
        )
    elif isinstance(
        error,
        ApprovalExpiredError,
    ):
        status_code = status.HTTP_409_CONFLICT
        code = "approval_expired"
        message = (
            "A solicitação de aprovação expirou."
        )
    elif isinstance(
        error,
        ApprovalStateError,
    ):
        status_code = status.HTTP_409_CONFLICT
        code = "invalid_approval_state"
        message = (
            "Estado de aprovação inválido "
            "para a operação."
        )
    elif isinstance(
        error,
        ApprovalConflictError,
    ):
        status_code = status.HTTP_409_CONFLICT
        code = "approval_conflict"
        message = (
            "Conflito na operação de aprovação."
        )
    elif isinstance(
        error,
        ApprovalValidationError,
    ):
        status_code = (
            status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        code = "invalid_approval_request"
        message = (
            "Requisição de aprovação inválida."
        )
    else:
        raise error

    log_approval_event(
        (
            "approval.access_denied"
            if status_code in {403, 404}
            else "approval.conflict"
            if status_code == 409
            else "approval.request_rejected"
        ),
        level=(
            logging.WARNING
            if status_code in {
                403,
                409,
            }
            else logging.INFO
        ),
        operation=operation,
        user_id=user_id,
        approval_request_id=request_id,
        skill_version_id=version_id,
        error_code=code,
    )

    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
        },
    )


def _map_skill_proposal_error(
    error: Exception,
) -> ApprovalError:
    if isinstance(
        error,
        SkillNotFoundError,
    ):
        return ApprovalNotFoundError(
            "Ação proposta inexistente "
            "ou não acessível."
        )

    if isinstance(
        error,
        SkillAuthorizationError,
    ):
        return ApprovalAuthorizationError(
            "Ator sem autoridade para propor "
            "esta execução."
        )

    if isinstance(
        error,
        SkillStateError,
    ):
        return ApprovalStateError(
            "Estado de skill inválido "
            "para proposta."
        )

    if isinstance(
        error,
        SkillValidationError,
    ):
        return ApprovalValidationError(
            "Ação proposta inválida."
        )

    raise error


def _authorize_skill_proposal(
    *,
    db: Session,
    authenticated: AuthenticatedSession,
    version_id: int,
    input_payload,
) -> None:
    try:
        authorize_skill_execution(
            db=db,
            role=authenticated.user.role,
            actor_user_id=authenticated.user.id,
            session_elevated=(
                is_session_elevated(
                    authenticated.session
                )
            ),
            version_id=version_id,
            input_payload=input_payload,
        )
    except Exception as error:
        mapped = _map_skill_proposal_error(
            error
        )
        _raise_approval_http_error(
            mapped,
            operation="create",
            user_id=authenticated.user.id,
            version_id=version_id,
        )


def _request_response(
    request,
) -> ApprovalRequestResponse:
    return ApprovalRequestResponse.from_request(
        request
    )


def _decision_response(
    decision,
) -> ApprovalDecisionResponse:
    return ApprovalDecisionResponse.from_decision(
        decision
    )


def _get_authorized_request(
    request_id: int,
    *,
    authenticated: AuthenticatedSession,
    service: ApprovalService,
):
    try:
        request = service.get_request(
            request_id
        )
        authorize_approval_read(
            role=authenticated.user.role,
            request=request,
        )
        return request
    except ApprovalError as error:
        _raise_approval_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
            request_id=request_id,
        )


@router.post(
    "/skill-executions/{version_id}",
    response_model=ApprovalCreationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_skill_execution_approval(
    version_id: int,
    payload: ApprovalCreateRequest,
    response: Response,
    idempotency_key: str = Depends(
        get_approval_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("skill:execute")
    ),
    db: Session = Depends(get_db),
    service: ApprovalService = Depends(
        get_approval_service
    ),
) -> ApprovalCreationResponse:
    _authorize_skill_proposal(
        db=db,
        authenticated=authenticated,
        version_id=version_id,
        input_payload=payload.input_payload,
    )

    try:
        result = (
            service.create_skill_execution_request(
                version_id=version_id,
                requester=ApprovalRequester(
                    actor_type="user",
                    actor_reference=(
                        f"user:{authenticated.user.id}"
                    ),
                    actor_user_id=(
                        authenticated.user.id
                    ),
                ),
                input_payload=(
                    payload.input_payload
                ),
                idempotency_key=idempotency_key,
            )
        )
    except ApprovalError as error:
        _raise_approval_http_error(
            error,
            operation="create",
            user_id=authenticated.user.id,
            version_id=version_id,
        )

    response.status_code = (
        status.HTTP_200_OK
        if result.duplicate
        else status.HTTP_201_CREATED
    )

    log_approval_event(
        "approval.request_recorded",
        operation="create",
        user_id=authenticated.user.id,
        approval_request_id=result.request.id,
        skill_version_id=(
            result.request.skill_version_id
        ),
        risk_level=result.request.risk_level,
        status=result.request.status,
        duplicate=result.duplicate,
        sensitive=(
            result.request.required_permission
            == "approval:decide_sensitive"
        ),
    )

    return ApprovalCreationResponse(
        request=_request_response(
            result.request
        ),
        created=not result.duplicate,
        duplicate=result.duplicate,
    )


@router.get(
    "",
    response_model=ApprovalListResponse,
)
def list_approval_requests(
    status_filter: list[
        ApprovalStatus
    ] | None = Query(
        default=None,
        alias="status",
    ),
    risk_level: list[
        ApprovalRiskLevel
    ] | None = Query(
        default=None,
    ),
    after_id: int | None = Query(
        default=None,
        gt=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("approval:read")
    ),
    service: ApprovalService = Depends(
        get_approval_service
    ),
) -> ApprovalListResponse:
    required_permissions = (
        visible_required_permissions(
            authenticated.user.role
        )
    )

    try:
        requests = service.list_requests(
            statuses=(
                tuple(status_filter)
                if status_filter is not None
                else None
            ),
            risk_levels=(
                tuple(risk_level)
                if risk_level is not None
                else None
            ),
            required_permissions=(
                required_permissions
            ),
            after_id=after_id,
            limit=limit + 1,
        )
    except ApprovalError as error:
        _raise_approval_http_error(
            error,
            operation="list",
            user_id=authenticated.user.id,
        )

    page = requests[:limit]

    log_approval_event(
        "approval.requests_listed",
        operation="list",
        user_id=authenticated.user.id,
        count=len(page),
    )

    return ApprovalListResponse(
        items=[
            _request_response(request)
            for request in page
        ],
        next_cursor=(
            page[-1].id
            if len(requests) > limit
            else None
        ),
    )


@router.get(
    "/{request_id}",
    response_model=ApprovalDetailsResponse,
)
def get_approval_request(
    request_id: int,
    authenticated: AuthenticatedSession = Depends(
        require_permission("approval:read")
    ),
    service: ApprovalService = Depends(
        get_approval_service
    ),
) -> ApprovalDetailsResponse:
    request = _get_authorized_request(
        request_id,
        authenticated=authenticated,
        service=service,
    )

    try:
        decision = service.get_decision(
            request.id
        )
    except ApprovalError as error:
        _raise_approval_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
            request_id=request.id,
        )

    log_approval_event(
        "approval.request_read",
        operation="read",
        user_id=authenticated.user.id,
        approval_request_id=request.id,
        skill_version_id=(
            request.skill_version_id
        ),
        risk_level=request.risk_level,
        status=request.status,
        sensitive=(
            request.required_permission
            == "approval:decide_sensitive"
        ),
    )

    return ApprovalDetailsResponse(
        request=_request_response(
            request
        ),
        decision=(
            _decision_response(decision)
            if decision is not None
            else None
        ),
    )


@router.post(
    "/{request_id}/decision",
    response_model=ApprovalDecisionResultResponse,
)
def decide_approval_request(
    request_id: int,
    payload: ApprovalDecisionRequest,
    authenticated: AuthenticatedSession = Depends(
        require_permission("approval:decide")
    ),
    service: ApprovalService = Depends(
        get_approval_service
    ),
) -> ApprovalDecisionResultResponse:
    request = _get_authorized_request(
        request_id,
        authenticated=authenticated,
        service=service,
    )

    try:
        authorize_approval_decision(
            role=authenticated.user.role,
            session_elevated=(
                is_session_elevated(
                    authenticated.session
                )
            ),
            request=request,
        )

        result = service.decide(
            request.id,
            decider_user_id=(
                authenticated.user.id
            ),
            decision=payload.decision,
            decision_note=(
                payload.decision_note
            ),
            sensitive_elevation_verified=(
                is_session_elevated(
                    authenticated.session
                )
            ),
        )
    except ApprovalError as error:
        _raise_approval_http_error(
            error,
            operation="decide",
            user_id=authenticated.user.id,
            request_id=request.id,
        )

    log_approval_event(
        "approval.request_decided",
        operation="decide",
        user_id=authenticated.user.id,
        approval_request_id=(
            result.request.id
        ),
        skill_version_id=(
            result.request.skill_version_id
        ),
        risk_level=result.request.risk_level,
        status=result.request.status,
        decision=result.decision.decision,
        sensitive=(
            result.request.required_permission
            == "approval:decide_sensitive"
        ),
    )

    return ApprovalDecisionResultResponse(
        request=_request_response(
            result.request
        ),
        decision=_decision_response(
            result.decision
        ),
    )
