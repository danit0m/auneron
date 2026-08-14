import logging
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Response
from fastapi import status
from sqlalchemy.orm import Session

from app.core.authentication import AuthenticatedSession
from app.core.authentication import require_permission
from app.core.authorization import has_permission
from app.core.memory_authorization import MemoryOperation
from app.core.memory_authorization import authorize_memory_scope
from app.core.memory_errors import EvidenceDuplicateError
from app.core.memory_errors import InvalidCursorError
from app.core.memory_errors import MemoryAuthorizationError
from app.core.memory_errors import MemoryConflictError
from app.core.memory_errors import MemoryError
from app.core.memory_errors import MemoryNotFoundError
from app.core.memory_errors import MemoryStateError
from app.core.memory_errors import MemoryValidationError
from app.core.observability import get_request_id
from app.database.database import get_db
from app.models.memory import MemoryEvidence
from app.models.memory import MemoryItem
from app.schemas.memory import EvidenceListResponse
from app.schemas.memory import EvidenceMutationResponse
from app.schemas.memory import EvidenceCreateRequest
from app.schemas.memory import EvidenceResponse
from app.schemas.memory import MemoryArchiveRequest
from app.schemas.memory import MemoryCreateRequest
from app.schemas.memory import MemoryHistoryResponse
from app.schemas.memory import MemoryLifecycleRequest
from app.schemas.memory import MemoryMutationResponse
from app.schemas.memory import MemoryPageResponse
from app.schemas.memory import MemoryRecallResponse
from app.schemas.memory import MemoryResponse
from app.schemas.memory import MemorySort
from app.schemas.memory import MemorySourceType
from app.schemas.memory import MemoryStatus
from app.schemas.memory import MemorySupersedeRequest
from app.schemas.memory import MemorySupersedeResponse
from app.schemas.memory import MemoryType
from app.services.memory_service import EvidenceInput
from app.services.memory_service import MemoryService


router = APIRouter(
    prefix="/memories",
    tags=["Memory"],
)

memory_logger = logging.getLogger(
    "auneron.memory"
)


def get_memory_service(
    db: Session = Depends(get_db),
) -> MemoryService:
    return MemoryService(db)


def _scope_operation(
    *,
    db: Session,
    authenticated: AuthenticatedSession,
    operation: MemoryOperation,
    scope_type: str,
    account_id: int | None,
    subject_user_id: int | None,
) -> None:
    try:
        authorize_memory_scope(
            db=db,
            role=authenticated.user.role,
            actor_user_id=authenticated.user.id,
            operation=operation,
            scope_type=scope_type,
            account_id=account_id,
            subject_user_id=subject_user_id,
        )
    except MemoryError as error:
        _raise_memory_http_error(
            error,
            operation=operation,
            user_id=authenticated.user.id,
            scope_type=scope_type,
        )


def _require_history_permission(
    authenticated: AuthenticatedSession,
) -> None:
    if has_permission(
        authenticated.user.role,
        "memory:history",
    ):
        return

    _raise_memory_http_error(
        MemoryAuthorizationError(
            "Ator sem permissao para historico."
        ),
        operation="history",
        user_id=authenticated.user.id,
    )


def _raise_memory_http_error(
    error: MemoryError,
    *,
    operation: str,
    user_id: int,
    scope_type: str | None = None,
) -> None:
    if isinstance(error, InvalidCursorError):
        status_code = status.HTTP_400_BAD_REQUEST
        code = "invalid_cursor"
        message = "Cursor de memoria invalido."
    elif isinstance(error, MemoryNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
        code = "memory_not_found"
        message = "Memoria nao encontrada."
    elif isinstance(error, MemoryAuthorizationError):
        status_code = status.HTTP_403_FORBIDDEN
        code = "memory_forbidden"
        message = "Operacao de memoria nao autorizada."
    elif isinstance(error, MemoryConflictError):
        status_code = status.HTTP_409_CONFLICT
        code = "memory_active_key_conflict"
        message = "Existe memoria ativa conflitante."
    elif isinstance(error, MemoryStateError):
        status_code = status.HTTP_409_CONFLICT
        code = "invalid_memory_state"
        message = "Estado de memoria invalido."
    elif isinstance(error, EvidenceDuplicateError):
        status_code = status.HTTP_409_CONFLICT
        code = "evidence_duplicate"
        message = "Evidence duplicada."
    elif isinstance(error, MemoryValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        code = "invalid_memory_request"
        message = "Requisicao de memoria invalida."
    else:
        raise error

    log_level = (
        logging.WARNING
        if status_code in {403, 409}
        else logging.INFO
    )
    event = (
        "memory.access_denied"
        if status_code in {403, 404}
        else "memory.conflict"
        if status_code == 409
        else "memory.request_rejected"
    )

    memory_logger.log(
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


def _evidence_inputs(
    items: Sequence[EvidenceCreateRequest],
    *,
    user_id: int,
) -> tuple[EvidenceInput, ...]:
    return tuple(
        EvidenceInput(
            relation=item.relation,
            source_type=item.source_type,
            source_reference=item.source_reference,
            source_memory_id=item.source_memory_id,
            evidence_text=item.evidence_text,
            weight=item.weight,
            observed_at=item.observed_at,
            created_by_user_id=user_id,
            context_data=item.context_data,
        )
        for item in items
    )


def _authorize_source_memories(
    items: Sequence[EvidenceCreateRequest],
    *,
    db: Session,
    authenticated: AuthenticatedSession,
    service: MemoryService,
) -> None:
    source_memory_ids = {
        item.source_memory_id
        for item in items
        if item.source_memory_id is not None
    }

    for source_memory_id in source_memory_ids:
        try:
            source_memory = service.get(
                source_memory_id
            )
        except MemoryError as error:
            _raise_memory_http_error(
                error,
                operation="read",
                user_id=authenticated.user.id,
            )

        _scope_operation(
            db=db,
            authenticated=authenticated,
            operation="read",
            scope_type=source_memory.scope_type,
            account_id=source_memory.account_id,
            subject_user_id=(
                source_memory.subject_user_id
            ),
        )

        if source_memory.status != "active":
            _require_history_permission(
                authenticated
            )


def _get_authorized_memory(
    memory_id: int,
    *,
    operation: MemoryOperation,
    db: Session,
    authenticated: AuthenticatedSession,
    service: MemoryService,
) -> MemoryItem:
    try:
        memory = service.get(memory_id)
    except MemoryError as error:
        _raise_memory_http_error(
            error,
            operation=operation,
            user_id=authenticated.user.id,
        )

    _scope_operation(
        db=db,
        authenticated=authenticated,
        operation=operation,
        scope_type=memory.scope_type,
        account_id=memory.account_id,
        subject_user_id=memory.subject_user_id,
    )

    return memory


def _can_read_source_memory(
    source_memory_id: int,
    *,
    db: Session,
    authenticated: AuthenticatedSession,
    service: MemoryService,
) -> bool:
    try:
        source = service.get(source_memory_id)
        authorize_memory_scope(
            db=db,
            role=authenticated.user.role,
            actor_user_id=authenticated.user.id,
            operation="read",
            scope_type=source.scope_type,
            account_id=source.account_id,
            subject_user_id=(
                source.subject_user_id
            ),
        )
    except MemoryError:
        return False

    return (
        source.status == "active"
        or has_permission(
            authenticated.user.role,
            "memory:history",
        )
    )


def _authorized_evidence_response(
    evidence: MemoryEvidence,
    *,
    db: Session,
    authenticated: AuthenticatedSession,
    service: MemoryService,
) -> EvidenceResponse:
    response = EvidenceResponse.from_evidence(
        evidence
    )

    if (
        evidence.source_memory_id is not None
        and not _can_read_source_memory(
            evidence.source_memory_id,
            db=db,
            authenticated=authenticated,
            service=service,
        )
    ):
        return response.model_copy(
            update={
                "source_memory_id": None,
            }
        )

    return response


@router.post(
    "",
    response_model=MemoryMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_memory(
    payload: MemoryCreateRequest,
    response: Response,
    authenticated: AuthenticatedSession = Depends(
        require_permission("memory:create")
    ),
    db: Session = Depends(get_db),
    service: MemoryService = Depends(
        get_memory_service
    ),
) -> MemoryMutationResponse:
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
    _authorize_source_memories(
        payload.evidence,
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.remember(
            memory_type=payload.memory_type,
            title=payload.title,
            content=payload.content,
            memory_key=payload.memory_key,
            scope_type=payload.scope.type,
            account_id=payload.scope.account_id,
            subject_user_id=(
                payload.scope.subject_user_id
            ),
            created_by_user_id=(
                authenticated.user.id
            ),
            importance=payload.importance,
            confidence=payload.confidence,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            source_type=payload.source.type,
            source_reference=(
                payload.source.reference
            ),
            context_data=payload.context_data,
            evidence=_evidence_inputs(
                payload.evidence,
                user_id=authenticated.user.id,
            ),
        )
    except MemoryError as error:
        _raise_memory_http_error(
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

    if result.created:
        memory_logger.info(
            "memory.created",
            extra={
                "event": "memory.created",
                "request_id": get_request_id(),
                "memory_id": result.memory.id,
                "user_id": authenticated.user.id,
                "scope_type": result.memory.scope_type,
            },
        )

    return MemoryMutationResponse(
        memory=MemoryResponse.from_memory(
            result.memory
        ),
        created=result.created,
        duplicate=result.duplicate,
        evidence=[
            EvidenceResponse.from_evidence(item)
            for item in result.evidence
        ],
    )


@router.get(
    "",
    response_model=MemoryRecallResponse,
)
def recall_memories(
    scope_type: str = Query(...),
    account_id: int | None = Query(
        default=None,
        gt=0,
    ),
    subject_user_id: int | None = Query(
        default=None,
        gt=0,
    ),
    memory_type: list[MemoryType] | None = Query(
        default=None,
    ),
    status_filter: list[MemoryStatus] | None = Query(
        default=None,
        alias="status",
    ),
    memory_key: str | None = Query(
        default=None,
        max_length=255,
    ),
    source_type: list[MemorySourceType] | None = Query(
        default=None,
    ),
    min_importance: Decimal | None = Query(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
    ),
    min_confidence: Decimal | None = Query(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
    ),
    valid_at: datetime | None = Query(
        default=None,
    ),
    created_after: datetime | None = Query(
        default=None,
    ),
    created_before: datetime | None = Query(
        default=None,
    ),
    text_query: str | None = Query(
        default=None,
        alias="q",
        max_length=500,
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    cursor: str | None = Query(
        default=None,
        min_length=1,
        max_length=4096,
    ),
    sort: MemorySort | None = Query(
        default=None,
    ),
    authenticated: AuthenticatedSession = Depends(
        require_permission("memory:read")
    ),
    db: Session = Depends(get_db),
    service: MemoryService = Depends(
        get_memory_service
    ),
) -> MemoryRecallResponse:
    requested_statuses = tuple(
        status_filter or ("active",)
    )

    _scope_operation(
        db=db,
        authenticated=authenticated,
        operation="read",
        scope_type=scope_type,
        account_id=account_id,
        subject_user_id=subject_user_id,
    )

    if any(
        item != "active"
        for item in requested_statuses
    ):
        _require_history_permission(
            authenticated
        )

    try:
        result = service.recall(
            scope_type=scope_type,
            account_id=account_id,
            subject_user_id=subject_user_id,
            memory_types=memory_type,
            statuses=requested_statuses,
            memory_key=memory_key,
            source_types=source_type,
            min_importance=min_importance,
            min_confidence=min_confidence,
            as_of=valid_at,
            created_after=created_after,
            created_before=created_before,
            text_query=text_query,
            limit=limit,
            cursor=cursor,
            sort=sort,
        )
    except MemoryError as error:
        _raise_memory_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
            scope_type=scope_type,
        )

    return MemoryRecallResponse(
        items=[
            MemoryResponse.from_memory(item)
            for item in result.items
        ],
        page=MemoryPageResponse(
            limit=result.limit,
            has_more=result.has_more,
            next_cursor=result.next_cursor,
        ),
    )


@router.get(
    "/{memory_id}",
    response_model=MemoryResponse,
)
def get_memory(
    memory_id: int,
    authenticated: AuthenticatedSession = Depends(
        require_permission("memory:read")
    ),
    db: Session = Depends(get_db),
    service: MemoryService = Depends(
        get_memory_service
    ),
) -> MemoryResponse:
    try:
        memory = service.get(memory_id)
    except MemoryError as error:
        _raise_memory_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
        )

    _scope_operation(
        db=db,
        authenticated=authenticated,
        operation="read",
        scope_type=memory.scope_type,
        account_id=memory.account_id,
        subject_user_id=memory.subject_user_id,
    )

    if memory.status != "active":
        _require_history_permission(
            authenticated
        )

    return MemoryResponse.from_memory(
        memory
    )


@router.post(
    "/{memory_id}/supersede",
    response_model=MemorySupersedeResponse,
    status_code=status.HTTP_201_CREATED,
)
def supersede_memory(
    memory_id: int,
    payload: MemorySupersedeRequest,
    authenticated: AuthenticatedSession = Depends(
        require_permission("memory:supersede")
    ),
    db: Session = Depends(get_db),
    service: MemoryService = Depends(
        get_memory_service
    ),
) -> MemorySupersedeResponse:
    previous = _get_authorized_memory(
        memory_id,
        operation="supersede",
        db=db,
        authenticated=authenticated,
        service=service,
    )
    _authorize_source_memories(
        payload.evidence,
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.supersede(
            previous.id,
            reason=payload.reason,
            memory_type=payload.memory_type,
            title=payload.title,
            content=payload.content,
            source_type=payload.source.type,
            source_reference=(
                payload.source.reference
            ),
            confidence=payload.confidence,
            created_by_user_id=(
                authenticated.user.id
            ),
            importance=payload.importance,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            context_data=payload.context_data,
            evidence=_evidence_inputs(
                payload.evidence,
                user_id=authenticated.user.id,
            ),
        )
    except MemoryError as error:
        _raise_memory_http_error(
            error,
            operation="supersede",
            user_id=authenticated.user.id,
            scope_type=previous.scope_type,
        )

    memory_logger.info(
        "memory.superseded",
        extra={
            "event": "memory.superseded",
            "request_id": get_request_id(),
            "memory_id": result.previous.id,
            "replacement_memory_id": (
                result.replacement.id
            ),
            "user_id": authenticated.user.id,
            "scope_type": result.previous.scope_type,
        },
    )

    return MemorySupersedeResponse(
        previous=MemoryResponse.from_memory(
            result.previous
        ),
        replacement=MemoryResponse.from_memory(
            result.replacement
        ),
        evidence=[
            EvidenceResponse.from_evidence(item)
            for item in result.evidence
        ],
    )


@router.post(
    "/{memory_id}/invalidate",
    response_model=MemoryResponse,
)
def invalidate_memory(
    memory_id: int,
    payload: MemoryLifecycleRequest,
    authenticated: AuthenticatedSession = Depends(
        require_permission("memory:invalidate")
    ),
    db: Session = Depends(get_db),
    service: MemoryService = Depends(
        get_memory_service
    ),
) -> MemoryResponse:
    memory = _get_authorized_memory(
        memory_id,
        operation="invalidate",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        invalidated = service.invalidate(
            memory.id,
            reason=payload.reason,
        )
    except MemoryError as error:
        _raise_memory_http_error(
            error,
            operation="invalidate",
            user_id=authenticated.user.id,
            scope_type=memory.scope_type,
        )

    memory_logger.info(
        "memory.invalidated",
        extra={
            "event": "memory.invalidated",
            "request_id": get_request_id(),
            "memory_id": invalidated.id,
            "user_id": authenticated.user.id,
            "scope_type": invalidated.scope_type,
        },
    )

    return MemoryResponse.from_memory(
        invalidated
    )


@router.post(
    "/{memory_id}/archive",
    response_model=MemoryResponse,
)
def archive_memory(
    memory_id: int,
    payload: MemoryArchiveRequest,
    authenticated: AuthenticatedSession = Depends(
        require_permission("memory:archive")
    ),
    db: Session = Depends(get_db),
    service: MemoryService = Depends(
        get_memory_service
    ),
) -> MemoryResponse:
    memory = _get_authorized_memory(
        memory_id,
        operation="archive",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        archived = service.archive(
            memory.id,
            reason=payload.reason,
        )
    except MemoryError as error:
        _raise_memory_http_error(
            error,
            operation="archive",
            user_id=authenticated.user.id,
            scope_type=memory.scope_type,
        )

    memory_logger.info(
        "memory.archived",
        extra={
            "event": "memory.archived",
            "request_id": get_request_id(),
            "memory_id": archived.id,
            "user_id": authenticated.user.id,
            "scope_type": archived.scope_type,
        },
    )

    return MemoryResponse.from_memory(
        archived
    )


@router.post(
    "/{memory_id}/evidence",
    response_model=EvidenceMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_memory_evidence(
    memory_id: int,
    payload: EvidenceCreateRequest,
    response: Response,
    authenticated: AuthenticatedSession = Depends(
        require_permission("memory:evidence")
    ),
    db: Session = Depends(get_db),
    service: MemoryService = Depends(
        get_memory_service
    ),
) -> EvidenceMutationResponse:
    memory = _get_authorized_memory(
        memory_id,
        operation="evidence",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    if memory.status != "active":
        _require_history_permission(
            authenticated
        )

    _authorize_source_memories(
        (payload,),
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        result = service.add_evidence(
            memory.id,
            relation=payload.relation,
            source_type=payload.source_type,
            source_reference=(
                payload.source_reference
            ),
            source_memory_id=(
                payload.source_memory_id
            ),
            evidence_text=payload.evidence_text,
            weight=payload.weight,
            observed_at=payload.observed_at,
            created_by_user_id=(
                authenticated.user.id
            ),
            context_data=payload.context_data,
        )
    except MemoryError as error:
        _raise_memory_http_error(
            error,
            operation="evidence",
            user_id=authenticated.user.id,
            scope_type=memory.scope_type,
        )

    response.status_code = (
        status.HTTP_201_CREATED
        if result.created
        else status.HTTP_200_OK
    )

    if result.created:
        memory_logger.info(
            "memory.evidence_added",
            extra={
                "event": "memory.evidence_added",
                "request_id": get_request_id(),
                "memory_id": memory.id,
                "evidence_id": result.evidence.id,
                "user_id": authenticated.user.id,
                "scope_type": memory.scope_type,
            },
        )

    return EvidenceMutationResponse(
        evidence=EvidenceResponse.from_evidence(
            result.evidence
        ),
        created=result.created,
        duplicate=result.duplicate,
    )


@router.get(
    "/{memory_id}/evidence",
    response_model=EvidenceListResponse,
)
def list_memory_evidence(
    memory_id: int,
    authenticated: AuthenticatedSession = Depends(
        require_permission("memory:read")
    ),
    db: Session = Depends(get_db),
    service: MemoryService = Depends(
        get_memory_service
    ),
) -> EvidenceListResponse:
    memory = _get_authorized_memory(
        memory_id,
        operation="read",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    if memory.status != "active":
        _require_history_permission(
            authenticated
        )

    try:
        evidence = service.list_evidence(
            memory.id
        )
    except MemoryError as error:
        _raise_memory_http_error(
            error,
            operation="read",
            user_id=authenticated.user.id,
            scope_type=memory.scope_type,
        )

    return EvidenceListResponse(
        items=[
            _authorized_evidence_response(
                item,
                db=db,
                authenticated=authenticated,
                service=service,
            )
            for item in evidence
        ]
    )


@router.get(
    "/{memory_id}/history",
    response_model=MemoryHistoryResponse,
)
def get_memory_history(
    memory_id: int,
    authenticated: AuthenticatedSession = Depends(
        require_permission("memory:history")
    ),
    db: Session = Depends(get_db),
    service: MemoryService = Depends(
        get_memory_service
    ),
) -> MemoryHistoryResponse:
    memory = _get_authorized_memory(
        memory_id,
        operation="history",
        db=db,
        authenticated=authenticated,
        service=service,
    )

    try:
        history = service.history(memory.id)
    except MemoryError as error:
        _raise_memory_http_error(
            error,
            operation="history",
            user_id=authenticated.user.id,
            scope_type=memory.scope_type,
        )

    for item in history:
        _scope_operation(
            db=db,
            authenticated=authenticated,
            operation="history",
            scope_type=item.scope_type,
            account_id=item.account_id,
            subject_user_id=(
                item.subject_user_id
            ),
        )

    return MemoryHistoryResponse(
        items=[
            MemoryResponse.from_memory(item)
            for item in history
        ]
    )
