import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from fastapi import Response
from fastapi import status
from sqlalchemy.orm import Session

from app.core.authentication import AuthenticatedSession
from app.core.authentication import require_permission
from app.core.authorization import has_permission
from app.core.memory_authorization import authorize_memory_scope
from app.core.memory_errors import MemoryError
from app.core.observability import get_request_id
from app.core.work_authorization import WorkOperation
from app.core.work_authorization import authorize_work_assignee
from app.core.work_authorization import authorize_work_scope
from app.core.work_errors import WorkAuthorizationError
from app.core.work_errors import WorkConflictError
from app.core.work_errors import WorkError
from app.core.work_errors import WorkIdempotencyConflictError
from app.core.work_errors import WorkNotFoundError
from app.core.work_errors import WorkStateError
from app.core.work_errors import WorkValidationError
from app.core.work_errors import WorkVersionConflictError
from app.core.work_observability import log_work_change
from app.database.database import get_db
from app.models.memory import MemoryItem
from app.models.work import WorkItem
from app.schemas.work import MemoryRelation
from app.schemas.work import WorkAssigneeRequest
from app.schemas.work import WorkCommentRequest
from app.schemas.work import WorkCreateRequest
from app.schemas.work import WorkCreationResponse
from app.schemas.work import WorkDependencyListResponse
from app.schemas.work import WorkDependencyRemovalRequest
from app.schemas.work import WorkDependencyRequest
from app.schemas.work import WorkDependencyResponse
from app.schemas.work import WorkDetailsRequest
from app.schemas.work import WorkEventListResponse
from app.schemas.work import WorkEventResponse
from app.schemas.work import WorkListResponse
from app.schemas.work import WorkMemoryLinkListResponse
from app.schemas.work import WorkMemoryLinkRequest
from app.schemas.work import WorkMemoryLinkResponse
from app.schemas.work import WorkMemoryUnlinkRequest
from app.schemas.work import WorkMutationResponse
from app.schemas.work import WorkPriority
from app.schemas.work import WorkPriorityRequest
from app.schemas.work import WorkRecurrenceDisableRequest
from app.schemas.work import WorkRecurrenceGenerateRequest
from app.schemas.work import WorkRecurrenceGenerationResponse
from app.schemas.work import WorkRecurrenceMutationResponse
from app.schemas.work import WorkRecurrenceOccurrenceListResponse
from app.schemas.work import WorkRecurrenceOccurrenceResponse
from app.schemas.work import WorkRecurrenceRequest
from app.schemas.work import WorkRecurrenceResponse
from app.schemas.work import WorkResponse
from app.schemas.work import WorkScheduleRequest
from app.schemas.work import WorkScopeType
from app.schemas.work import WorkSLAListResponse
from app.schemas.work import WorkSLAResponse
from app.schemas.work import WorkStatus
from app.schemas.work import WorkStatusRequest
from app.services.work_service import WorkActor
from app.services.work_service import WorkCreationResult
from app.services.work_service import WorkManagerService
from app.services.work_service import WorkMutationResult


router = APIRouter(
    prefix="/work-items",
    tags=["Work Manager"],
)

work_logger = logging.getLogger(
    "auneron.work"
)


def get_work_service(
    db: Session = Depends(get_db),
) -> WorkManagerService:
    return WorkManagerService(db)


def get_idempotency_key(
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
) -> str | None:
    return idempotency_key


def _actor(
    authenticated: AuthenticatedSession,
) -> WorkActor:
    return WorkActor(
        actor_type="user",
        actor_reference=(
            f"user:{authenticated.user.id}"
        ),
        actor_user_id=authenticated.user.id,
    )


def _scope_operation(
    *,
    db: Session,
    authenticated: AuthenticatedSession,
    operation: WorkOperation,
    scope_type: str,
    account_id: int | None,
    subject_user_id: int | None,
) -> None:
    try:
        authorize_work_scope(
            db=db,
            role=authenticated.user.role,
            actor_user_id=authenticated.user.id,
            operation=operation,
            scope_type=scope_type,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation=operation,
            user_id=authenticated.user.id,
            scope_type=scope_type,
        )


def _authorize_assignee(
    assignee_user_id: int | None,
    *,
    db: Session,
    authenticated: AuthenticatedSession,
) -> None:
    try:
        authorize_work_assignee(
            db=db,
            role=authenticated.user.role,
            actor_user_id=authenticated.user.id,
            assignee_user_id=assignee_user_id,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="assign",
            user_id=authenticated.user.id,
        )


def _raise_work_http_error(
    error: WorkError,
    *,
    operation: str,
    user_id: int,
    scope_type: str | None = None,
) -> None:
    if isinstance(error, WorkNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
        code = "work_not_found"
        message = "Trabalho não encontrado."
    elif isinstance(error, WorkAuthorizationError):
        status_code = status.HTTP_403_FORBIDDEN
        code = "work_forbidden"
        message = "Operação de trabalho não autorizada."
    elif isinstance(error, WorkVersionConflictError):
        status_code = status.HTTP_409_CONFLICT
        code = "work_version_conflict"
        message = "A versão do trabalho foi alterada."
    elif isinstance(error, WorkIdempotencyConflictError):
        status_code = status.HTTP_409_CONFLICT
        code = "work_idempotency_conflict"
        message = "A chave idempotente conflita com outro pedido."
    elif isinstance(error, WorkStateError):
        status_code = status.HTTP_409_CONFLICT
        code = "invalid_work_state"
        message = "Estado de trabalho inválido para a operação."
    elif isinstance(error, WorkConflictError):
        status_code = status.HTTP_409_CONFLICT
        code = "work_conflict"
        message = "Conflito na operação de trabalho."
    elif isinstance(error, WorkValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        code = "invalid_work_request"
        message = "Requisição de trabalho inválida."
    else:
        raise error

    log_level = (
        logging.WARNING
        if status_code in {403, 409}
        else logging.INFO
    )
    event = (
        "work.access_denied"
        if status_code in {403, 404}
        else "work.conflict"
        if status_code == 409
        else "work.request_rejected"
    )

    work_logger.log(
        log_level,
        event,
        extra={
            "event": event,
            "request_id": get_request_id(),
            "operation": operation,
            "user_id": user_id,
            "scope_type": scope_type,
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


def _get_authorized_work(
    work_item_id: int,
    *,
    operation: WorkOperation,
    db: Session,
    authenticated: AuthenticatedSession,
    service: WorkManagerService,
) -> WorkItem:
    try:
        item = service.get(work_item_id)
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation=operation,
            user_id=authenticated.user.id,
        )

    _scope_operation(
        db=db,
        authenticated=authenticated,
        operation=operation,
        scope_type=item.scope_type,
        account_id=item.account_id,
        subject_user_id=item.subject_user_id,
    )
    return item


def _memory_is_authorized(
    memory_id: int,
    *,
    db: Session,
    authenticated: AuthenticatedSession,
) -> bool:
    memory = db.get(MemoryItem, memory_id)

    if memory is None:
        return False

    try:
        authorize_memory_scope(
            db=db,
            role=authenticated.user.role,
            actor_user_id=authenticated.user.id,
            operation="read",
            scope_type=memory.scope_type,
            account_id=memory.account_id,
            subject_user_id=memory.subject_user_id,
        )
    except MemoryError:
        return False

    return (
        memory.status == "active"
        or has_permission(
            authenticated.user.role,
            "memory:history",
        )
    )


def _require_memory_authorized(
    memory_id: int,
    *,
    db: Session,
    authenticated: AuthenticatedSession,
) -> None:
    if _memory_is_authorized(
        memory_id,
        db=db,
        authenticated=authenticated,
    ):
        return

    _raise_work_http_error(
        WorkNotFoundError(
            "Memória inexistente ou não acessível."
        ),
        operation="memory_link",
        user_id=authenticated.user.id,
    )


def _creation_response(
    result: WorkCreationResult,
) -> WorkCreationResponse:
    log_work_change(
        work_item=result.work_item,
        event=result.event,
        applied=result.created,
        duplicate=result.duplicate,
    )
    return WorkCreationResponse(
        work_item=WorkResponse.from_work_item(
            result.work_item
        ),
        event=WorkEventResponse.from_event(
            result.event
        ),
        created=result.created,
        duplicate=result.duplicate,
    )


def _mutation_response(
    result: WorkMutationResult,
) -> WorkMutationResponse:
    log_work_change(
        work_item=result.work_item,
        event=result.event,
        applied=result.applied,
        duplicate=result.duplicate,
    )
    return WorkMutationResponse(
        work_item=WorkResponse.from_work_item(
            result.work_item
        ),
        event=WorkEventResponse.from_event(
            result.event
        ),
        applied=result.applied,
        duplicate=result.duplicate,
    )


@router.post(
    "",
    response_model=WorkCreationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_work_item(
    payload: WorkCreateRequest,
    response: Response,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:create")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkCreationResponse:
    _scope_operation(
        db=db,
        authenticated=authenticated,
        operation="create",
        scope_type=payload.scope.type,
        account_id=payload.scope.account_id,
        subject_user_id=(
            payload.scope.subject_user_id
        ),
    )
    _authorize_assignee(
        payload.assignee_user_id,
        db=db,
        authenticated=authenticated,
    )

    if payload.parent_work_item_id is not None:
        _get_authorized_work(
            payload.parent_work_item_id,
            operation="read",
            db=db,
            authenticated=authenticated,
            service=service,
        )

    try:
        result = service.create(
            work_type=payload.work_type,
            title=payload.title,
            description=payload.description,
            work_key=payload.work_key,
            scope_type=payload.scope.type,
            account_id=payload.scope.account_id,
            subject_user_id=(
                payload.scope.subject_user_id
            ),
            parent_work_item_id=(
                payload.parent_work_item_id
            ),
            assignee_user_id=(
                payload.assignee_user_id
            ),
            priority=payload.priority,
            due_at=payload.due_at,
            sla_due_at=payload.sla_due_at,
            context_data=payload.context_data,
            origin_type="api",
            origin_reference=(
                f"work-api:user:{authenticated.user.id}"
            ),
            actor=_actor(authenticated),
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="create",
            user_id=authenticated.user.id,
            scope_type=payload.scope.type,
        )

    response.status_code = (
        status.HTTP_201_CREATED
        if result.created
        else status.HTTP_200_OK
    )

    return _creation_response(result)


@router.get(
    "",
    response_model=WorkListResponse,
)
def list_work_items(
    scope_type: WorkScopeType = Query(...),
    account_id: int | None = Query(
        default=None,
        gt=0,
    ),
    subject_user_id: int | None = Query(
        default=None,
        gt=0,
    ),
    status_filter: list[WorkStatus] | None = Query(
        default=None,
        alias="status",
    ),
    priority: list[WorkPriority] | None = Query(
        default=None,
    ),
    assignee_user_id: int | None = Query(
        default=None,
        gt=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:read")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkListResponse:
    _scope_operation(
        db=db,
        authenticated=authenticated,
        operation="read",
        scope_type=scope_type,
        account_id=account_id,
        subject_user_id=subject_user_id,
    )

    try:
        items = service.list_items(
            scope_type=scope_type,
            account_id=account_id,
            subject_user_id=subject_user_id,
            statuses=(
                tuple(status_filter)
                if status_filter is not None
                else None
            ),
            priorities=(
                tuple(priority)
                if priority is not None
                else None
            ),
            assignee_user_id=assignee_user_id,
            limit=limit,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
            scope_type=scope_type,
        )

    return WorkListResponse(
        items=[
            WorkResponse.from_work_item(item)
            for item in items
        ]
    )


@router.get(
    "/sla/breaches",
    response_model=WorkSLAListResponse,
)
def list_work_sla_breaches(
    scope_type: WorkScopeType = Query(...),
    account_id: int | None = Query(
        default=None,
        gt=0,
    ),
    subject_user_id: int | None = Query(
        default=None,
        gt=0,
    ),
    as_of: datetime | None = Query(default=None),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:read")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkSLAListResponse:
    _scope_operation(
        db=db,
        authenticated=authenticated,
        operation="read",
        scope_type=scope_type,
        account_id=account_id,
        subject_user_id=subject_user_id,
    )

    try:
        items = service.list_sla_breaches(
            as_of=as_of,
            limit=limit,
            scope_type=scope_type,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
            scope_type=scope_type,
        )

    return WorkSLAListResponse(
        items=[
            WorkResponse.from_work_item(item)
            for item in items
        ]
    )


@router.get(
    "/{work_item_id}",
    response_model=WorkResponse,
)
def get_work_item(
    work_item_id: int,
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:read")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="read",
        db=db,
        authenticated=authenticated,
        service=service,
    )
    return WorkResponse.from_work_item(item)


@router.get(
    "/{work_item_id}/events",
    response_model=WorkEventListResponse,
)
def list_work_events(
    work_item_id: int,
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    after_id: int | None = Query(
        default=None,
        gt=0,
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:read")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkEventListResponse:
    _get_authorized_work(
        work_item_id,
        operation="read",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        events = service.list_events(
            work_item_id,
            limit=limit,
            after_id=after_id,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
        )

    page = events[:limit]
    return WorkEventListResponse(
        items=[
            WorkEventResponse.from_event(event)
            for event in page
        ],
        next_cursor=(
            page[-1].id
            if len(events) > limit
            else None
        ),
    )


@router.patch(
    "/{work_item_id}/details",
    response_model=WorkMutationResponse,
)
def update_work_details(
    work_item_id: int,
    payload: WorkDetailsRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:update")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="update",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.update_details(
            item.id,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            title=payload.title,
            description=payload.description,
            context_data=payload.context_data,
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="update",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return _mutation_response(result)


@router.patch(
    "/{work_item_id}/priority",
    response_model=WorkMutationResponse,
)
def change_work_priority(
    work_item_id: int,
    payload: WorkPriorityRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:update")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="update",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.change_priority(
            item.id,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            priority=payload.priority,
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="update",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return _mutation_response(result)


@router.patch(
    "/{work_item_id}/assignee",
    response_model=WorkMutationResponse,
)
def change_work_assignee(
    work_item_id: int,
    payload: WorkAssigneeRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:update")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="update",
        db=db,
        authenticated=authenticated,
        service=service,
    )
    _authorize_assignee(
        payload.assignee_user_id,
        db=db,
        authenticated=authenticated,
    )

    try:
        result = service.change_assignee(
            item.id,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            assignee_user_id=payload.assignee_user_id,
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="assign",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return _mutation_response(result)


@router.post(
    "/{work_item_id}/comments",
    response_model=WorkMutationResponse,
)
def add_work_comment(
    work_item_id: int,
    payload: WorkCommentRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:comment")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="comment",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.add_comment(
            item.id,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            comment=payload.comment,
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="comment",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return _mutation_response(result)


@router.post(
    "/{work_item_id}/status",
    response_model=WorkMutationResponse,
)
def transition_work_status(
    work_item_id: int,
    payload: WorkStatusRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:update")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="update",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.transition_status(
            item.id,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            status=payload.status,
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="update",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return _mutation_response(result)


@router.patch(
    "/{work_item_id}/schedule",
    response_model=WorkMutationResponse,
)
def change_work_schedule(
    work_item_id: int,
    payload: WorkScheduleRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:update")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="update",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.change_schedule(
            item.id,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            due_at=payload.due_at,
            sla_due_at=payload.sla_due_at,
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="update",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return _mutation_response(result)


@router.get(
    "/{work_item_id}/dependencies",
    response_model=WorkDependencyListResponse,
)
def list_work_dependencies(
    work_item_id: int,
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    after_id: int | None = Query(
        default=None,
        gt=0,
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:read")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkDependencyListResponse:
    _get_authorized_work(
        work_item_id,
        operation="read",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        dependencies = service.list_dependencies(
            work_item_id,
            limit=limit,
            after_id=after_id,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
        )

    page = dependencies[:limit]
    return WorkDependencyListResponse(
        items=[
            WorkDependencyResponse.from_dependency(
                dependency
            )
            for dependency, _ in page
        ],
        next_cursor=(
            page[-1][0].id
            if len(dependencies) > limit
            else None
        ),
    )


@router.post(
    "/{work_item_id}/dependencies",
    response_model=WorkMutationResponse,
)
def add_work_dependency(
    work_item_id: int,
    payload: WorkDependencyRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:manage_dependencies")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="dependency",
        db=db,
        authenticated=authenticated,
        service=service,
    )
    _get_authorized_work(
        payload.depends_on_work_item_id,
        operation="read",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.add_dependency(
            item.id,
            depends_on_work_item_id=(
                payload.depends_on_work_item_id
            ),
            dependency_type=payload.dependency_type,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="dependency",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return _mutation_response(result)


@router.delete(
    "/{work_item_id}/dependencies/{depends_on_work_item_id}",
    response_model=WorkMutationResponse,
)
def remove_work_dependency(
    work_item_id: int,
    depends_on_work_item_id: int,
    payload: WorkDependencyRemovalRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:manage_dependencies")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="dependency",
        db=db,
        authenticated=authenticated,
        service=service,
    )
    _get_authorized_work(
        depends_on_work_item_id,
        operation="read",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.remove_dependency(
            item.id,
            depends_on_work_item_id=(
                depends_on_work_item_id
            ),
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="dependency",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return _mutation_response(result)


@router.get(
    "/{work_item_id}/memory-links",
    response_model=WorkMemoryLinkListResponse,
)
def list_work_memory_links(
    work_item_id: int,
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    after_id: int | None = Query(
        default=None,
        gt=0,
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:read")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMemoryLinkListResponse:
    _get_authorized_work(
        work_item_id,
        operation="read",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        links = service.list_memory_links(
            work_item_id,
            limit=limit,
            after_id=after_id,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
        )

    page = links[:limit]
    return WorkMemoryLinkListResponse(
        items=[
            WorkMemoryLinkResponse.from_link(link)
            for link in page
            if _memory_is_authorized(
                link.memory_id,
                db=db,
                authenticated=authenticated,
            )
        ],
        next_cursor=(
            page[-1].id
            if len(links) > limit
            else None
        ),
    )


@router.post(
    "/{work_item_id}/memory-links",
    response_model=WorkMutationResponse,
)
def link_work_memory(
    work_item_id: int,
    payload: WorkMemoryLinkRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:update")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="update",
        db=db,
        authenticated=authenticated,
        service=service,
    )
    _require_memory_authorized(
        payload.memory_id,
        db=db,
        authenticated=authenticated,
    )

    try:
        result = service.link_memory(
            item.id,
            memory_id=payload.memory_id,
            relation=payload.relation,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="memory_link",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return _mutation_response(result)


@router.delete(
    "/{work_item_id}/memory-links/{memory_id}/{relation}",
    response_model=WorkMutationResponse,
)
def unlink_work_memory(
    work_item_id: int,
    memory_id: int,
    relation: MemoryRelation,
    payload: WorkMemoryUnlinkRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:update")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="update",
        db=db,
        authenticated=authenticated,
        service=service,
    )
    _require_memory_authorized(
        memory_id,
        db=db,
        authenticated=authenticated,
    )

    try:
        result = service.unlink_memory(
            item.id,
            memory_id=memory_id,
            relation=relation,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="memory_link",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return _mutation_response(result)


@router.get(
    "/{work_item_id}/sla",
    response_model=WorkSLAResponse,
)
def get_work_sla(
    work_item_id: int,
    as_of: datetime | None = Query(default=None),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:read")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkSLAResponse:
    _get_authorized_work(
        work_item_id,
        operation="read",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        sla = service.evaluate_sla(
            work_item_id,
            as_of=as_of,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
        )

    return WorkSLAResponse.from_status(sla)


@router.post(
    "/{work_item_id}/recurrence",
    response_model=WorkRecurrenceMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def configure_work_recurrence(
    work_item_id: int,
    payload: WorkRecurrenceRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:manage_recurrence")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkRecurrenceMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="recurrence",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.configure_recurrence(
            item.id,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            frequency=payload.frequency,
            interval_value=payload.interval_value,
            timezone_name=payload.timezone_name,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            max_occurrences=payload.max_occurrences,
            sla_lead_minutes=payload.sla_lead_minutes,
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="recurrence",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return WorkRecurrenceMutationResponse(
        recurrence=WorkRecurrenceResponse.from_rule(
            result.rule
        ),
        mutation=_mutation_response(
            result.mutation
        ),
    )


@router.get(
    "/{work_item_id}/recurrence",
    response_model=WorkRecurrenceResponse,
)
def get_work_recurrence(
    work_item_id: int,
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:read")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkRecurrenceResponse:
    _get_authorized_work(
        work_item_id,
        operation="read",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        rule = service.get_recurrence(
            work_item_id
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
        )

    return WorkRecurrenceResponse.from_rule(rule)


@router.post(
    "/{work_item_id}/recurrence/disable",
    response_model=WorkRecurrenceMutationResponse,
)
def disable_work_recurrence(
    work_item_id: int,
    payload: WorkRecurrenceDisableRequest,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:manage_recurrence")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkRecurrenceMutationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="recurrence",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.disable_recurrence(
            item.id,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="recurrence",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    return WorkRecurrenceMutationResponse(
        recurrence=WorkRecurrenceResponse.from_rule(
            result.rule
        ),
        mutation=_mutation_response(
            result.mutation
        ),
    )


@router.get(
    "/{work_item_id}/recurrence/occurrences",
    response_model=WorkRecurrenceOccurrenceListResponse,
)
def list_work_recurrence_occurrences(
    work_item_id: int,
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
    after_id: int | None = Query(
        default=None,
        gt=0,
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:read")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkRecurrenceOccurrenceListResponse:
    _get_authorized_work(
        work_item_id,
        operation="read",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        occurrences = (
            service.list_recurrence_occurrences(
                work_item_id,
                limit=limit,
                after_id=after_id,
            )
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
        )

    page = occurrences[:limit]
    return WorkRecurrenceOccurrenceListResponse(
        items=[
            WorkRecurrenceOccurrenceResponse.from_occurrence(
                occurrence
            )
            for occurrence in page
        ],
        next_cursor=(
            page[-1].id
            if len(occurrences) > limit
            else None
        ),
    )


@router.post(
    "/{work_item_id}/recurrence/generate",
    response_model=WorkRecurrenceGenerationResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_work_recurrence(
    work_item_id: int,
    payload: WorkRecurrenceGenerateRequest,
    response: Response,
    idempotency_key: str | None = Depends(
        get_idempotency_key
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("work:manage_recurrence")
    ),
    db: Session = Depends(get_db),
    service: WorkManagerService = Depends(
        get_work_service
    ),
) -> WorkRecurrenceGenerationResponse:
    item = _get_authorized_work(
        work_item_id,
        operation="recurrence",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.generate_due_occurrence(
            item.id,
            expected_version=payload.expected_version,
            actor=_actor(authenticated),
            as_of=payload.as_of,
            idempotency_key=idempotency_key,
        )
    except WorkError as error:
        _raise_work_http_error(
            error,
            operation="recurrence",
            user_id=authenticated.user.id,
            scope_type=item.scope_type,
        )

    response.status_code = (
        status.HTTP_201_CREATED
        if result.applied
        else status.HTTP_200_OK
    )

    log_work_change(
        work_item=result.template,
        event=result.event,
        applied=result.applied,
        duplicate=result.duplicate,
    )

    return WorkRecurrenceGenerationResponse(
        template=WorkResponse.from_work_item(
            result.template
        ),
        occurrence_work_item=(
            WorkResponse.from_work_item(
                result.occurrence_work_item
            )
        ),
        occurrence=(
            WorkRecurrenceOccurrenceResponse.from_occurrence(
                result.occurrence
            )
        ),
        recurrence=WorkRecurrenceResponse.from_rule(
            result.rule
        ),
        event=WorkEventResponse.from_event(
            result.event
        ),
        applied=result.applied,
        duplicate=result.duplicate,
    )
