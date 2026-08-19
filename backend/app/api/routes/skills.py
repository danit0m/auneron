import logging
from typing import NoReturn

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app.core.authentication import AuthenticatedSession
from app.core.authentication import is_session_elevated
from app.core.authentication import require_permission
from app.core.observability import get_request_id
from app.core.skill_authorization import SkillExecutionGrant
from app.core.skill_rate_limiting import skill_rate_limiter
from app.core.skill_authorization import authorize_skill_execution
from app.core.skill_errors import SkillAuthorizationError
from app.core.skill_errors import SkillConflictError
from app.core.skill_errors import SkillExecutionError
from app.core.skill_errors import SkillExecutionTimeoutError
from app.core.skill_errors import SkillHandlerNotAllowedError
from app.core.skill_errors import SkillIdempotencyConflictError
from app.core.skill_errors import SkillInputValidationError
from app.core.skill_errors import SkillInvocationInProgressError
from app.core.skill_errors import SkillNotFoundError
from app.core.skill_errors import SkillOutputLimitError
from app.core.skill_errors import SkillOutputValidationError
from app.core.skill_errors import SkillRuntimeBusyError
from app.core.skill_errors import SkillRuntimeError
from app.core.skill_errors import SkillSchemaError
from app.core.skill_errors import SkillStateError
from app.core.skill_errors import SkillValidationError
from app.database.database import get_db
from app.schemas.skill import SkillInvocationResponse
from app.schemas.skill import SkillInvokeRequest
from app.services.skill_runtime import SkillInvocationActor
from app.services.skill_runtime import SkillInvocationResult
from app.services.skill_runtime import SkillRuntimeService


router = APIRouter(
    prefix="/agent-skills",
    tags=["Agent Skills"],
)

skill_logger = logging.getLogger(
    "auneron.skill"
)


def get_skill_runtime_service(
    db: Session = Depends(get_db),
) -> SkillRuntimeService:
    return SkillRuntimeService(db)


def get_skill_idempotency_key(
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
) -> str | None:
    return idempotency_key


def _enforce_skill_rate_limit(
    *,
    user_id: int,
    version_id: int,
) -> None:
    retry_after = skill_rate_limiter.consume(
        user_id=user_id
    )
    if retry_after is None:
        return

    skill_logger.warning(
        "skill.rate_limit.exceeded",
        extra={
            "event": "skill.rate_limit.exceeded",
            "request_id": get_request_id(),
            "operation": "invoke",
            "user_id": user_id,
            "skill_version_id": version_id,
            "retry_after_seconds": retry_after,
        },
    )

    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "skill_rate_limited",
            "message": (
                "Limite de requisições de skill excedido."
            ),
        },
        headers={
            "Retry-After": str(
                max(1, retry_after)
            ),
        },
    )


def _actor(
    authenticated: AuthenticatedSession,
) -> SkillInvocationActor:
    return SkillInvocationActor(
        actor_type="user",
        actor_reference=(
            f"user:{authenticated.user.id}"
        ),
        actor_user_id=authenticated.user.id,
    )


def _raise_skill_http_error(
    error: Exception,
    *,
    user_id: int,
    version_id: int,
) -> NoReturn:
    if isinstance(
        error,
        SkillNotFoundError,
    ):
        status_code = status.HTTP_404_NOT_FOUND
        code = "skill_not_found"
        message = "Skill não encontrada."
    elif isinstance(
        error,
        SkillAuthorizationError,
    ):
        status_code = status.HTTP_403_FORBIDDEN
        code = "skill_forbidden"
        message = "Execução de skill não autorizada."
    elif isinstance(
        error,
        SkillIdempotencyConflictError,
    ):
        status_code = status.HTTP_409_CONFLICT
        code = "skill_idempotency_conflict"
        message = (
            "A chave idempotente conflita "
            "com outro pedido."
        )
    elif isinstance(
        error,
        SkillInvocationInProgressError,
    ):
        status_code = status.HTTP_409_CONFLICT
        code = "skill_invocation_in_progress"
        message = (
            "A invocação idempotente ainda "
            "está em execução."
        )
    elif isinstance(
        error,
        SkillStateError,
    ):
        status_code = status.HTTP_409_CONFLICT
        code = "invalid_skill_state"
        message = (
            "Estado de skill inválido "
            "para execução."
        )
    elif isinstance(
        error,
        SkillConflictError,
    ):
        status_code = status.HTTP_409_CONFLICT
        code = "skill_conflict"
        message = "Conflito na execução de skill."
    elif isinstance(
        error,
        (
            SkillInputValidationError,
            SkillValidationError,
        ),
    ):
        status_code = (
            status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        code = "invalid_skill_request"
        message = "Requisição de skill inválida."
    elif isinstance(
        error,
        SkillRuntimeBusyError,
    ):
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
        )
        code = "skill_runtime_busy"
        message = (
            "Runtime de skills temporariamente "
            "sem capacidade."
        )
    elif isinstance(
        error,
        SkillExecutionTimeoutError,
    ):
        status_code = (
            status.HTTP_504_GATEWAY_TIMEOUT
        )
        code = "skill_timeout"
        message = (
            "A execução da skill excedeu "
            "o tempo permitido."
        )
    elif isinstance(
        error,
        (
            SkillHandlerNotAllowedError,
            SkillSchemaError,
        ),
    ):
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
        )
        code = "skill_runtime_unavailable"
        message = "Runtime de skills indisponível."
    elif isinstance(
        error,
        (
            SkillExecutionError,
            SkillOutputLimitError,
            SkillOutputValidationError,
            SkillRuntimeError,
        ),
    ):
        status_code = status.HTTP_502_BAD_GATEWAY
        code = "skill_execution_failed"
        message = "A execução da skill falhou."
    else:
        raise error

    if status_code in {
        403,
        404,
    }:
        event = "skill.access_denied"
    elif status_code == 409:
        event = "skill.conflict"
    elif status_code == 422:
        event = "skill.request_rejected"
    else:
        event = "skill.execution_error"

    skill_logger.log(
        (
            logging.WARNING
            if status_code in {
                403,
                409,
            }
            else logging.INFO
        ),
        event,
        extra={
            "event": event,
            "request_id": get_request_id(),
            "operation": "invoke",
            "user_id": user_id,
            "skill_version_id": version_id,
            "error_code": code,
        },
    )

    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
        },
    )


def _authorize(
    *,
    db: Session,
    authenticated: AuthenticatedSession,
    version_id: int,
    input_payload,
) -> SkillExecutionGrant:
    try:
        return authorize_skill_execution(
            db=db,
            role=authenticated.user.role,
            actor_user_id=authenticated.user.id,
            session_elevated=is_session_elevated(
                authenticated.session
            ),
            version_id=version_id,
            input_payload=input_payload,
        )
    except Exception as error:
        if isinstance(
            error,
            (
                SkillAuthorizationError,
                SkillConflictError,
                SkillNotFoundError,
                SkillStateError,
                SkillValidationError,
            ),
        ):
            _raise_skill_http_error(
                error,
                user_id=authenticated.user.id,
                version_id=version_id,
            )
        raise


def _response(
    result: SkillInvocationResult,
) -> SkillInvocationResponse:
    invocation = result.invocation
    return SkillInvocationResponse(
        invocation_id=invocation.id,
        skill_version_id=(
            invocation.skill_version_id
        ),
        status=invocation.status,
        duplicate=result.duplicate,
        output=result.output,
        started_at=invocation.started_at,
        finished_at=invocation.finished_at,
        duration_ms=invocation.duration_ms,
    )


@router.post(
    "/versions/{version_id}/invoke",
    response_model=SkillInvocationResponse,
)
def invoke_skill(
    version_id: int,
    payload: SkillInvokeRequest,
    idempotency_key: str | None = Depends(
        get_skill_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("skill:execute")
    ),
    db: Session = Depends(get_db),
    runtime: SkillRuntimeService = Depends(
        get_skill_runtime_service
    ),
) -> SkillInvocationResponse:
    _enforce_skill_rate_limit(
        user_id=authenticated.user.id,
        version_id=version_id,
    )

    grant = _authorize(
        db=db,
        authenticated=authenticated,
        version_id=version_id,
        input_payload=payload.input_payload,
    )

    try:
        result = runtime.invoke(
            grant.version.id,
            actor=_actor(
                authenticated
            ),
            input_payload=payload.input_payload,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        if isinstance(
            error,
            (
                SkillConflictError,
                SkillExecutionError,
                SkillExecutionTimeoutError,
                SkillHandlerNotAllowedError,
                SkillIdempotencyConflictError,
                SkillInputValidationError,
                SkillInvocationInProgressError,
                SkillNotFoundError,
                SkillOutputLimitError,
                SkillOutputValidationError,
                SkillRuntimeBusyError,
                SkillRuntimeError,
                SkillSchemaError,
                SkillStateError,
                SkillValidationError,
            ),
        ):
            _raise_skill_http_error(
                error,
                user_id=authenticated.user.id,
                version_id=grant.version.id,
            )
        raise

    skill_logger.info(
        "skill.invoked",
        extra={
            "event": "skill.invoked",
            "request_id": get_request_id(),
            "user_id": authenticated.user.id,
            "skill_version_id": grant.version.id,
            "invocation_id": (
                result.invocation.id
            ),
            "duplicate": result.duplicate,
            "status": (
                result.invocation.status
            ),
            "has_account_scope": (
                grant.account_id is not None
            ),
            "has_user_scope": (
                grant.subject_user_id
                is not None
            ),
        },
    )

    return _response(result)
