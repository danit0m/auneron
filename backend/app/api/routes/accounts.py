import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.agents.event_bus import event_bus
from app.core.authentication import AuthenticatedSession
from app.core.authentication import require_permission
from app.core.client_classification import _oldest_account_id
from app.core.client_classification import (
    MEMORY_KEY as CLIENT_CLASSIFICATION_MEMORY_KEY,
)
from app.core.config import settings
from app.database.database import get_db
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.repositories.memory_repository import MemoryRepository
from app.repositories.skill_repository import SkillRepository
from app.schemas.account import (
    AccountClassificationResponse,
    AccountCreate,
    AccountResponse,
    AccountUpdate,
    ClientClassificationDetail,
)
from app.services.authenticated_advisory_envelope_assembly import (
    AuthenticatedAdvisoryEnvelopeAssemblyService,
)
from app.services.authenticated_advisory_proposal_service import (
    AuthenticatedAdvisoryProposalService,
)
from app.services.orchestrator_skill_binding_projection import (
    OrchestratorSkillBindingProjectionService,
)
from app.services.overdue_detection_service import OverdueDetectionService
from app.api.routes.approvals import _raise_approval_http_error
from app.core.approval_errors import ApprovalError
from app.core.approval_observability import log_approval_event
from app.schemas.account import AccountMarkPaidExecuteRequest
from app.services.account_mark_paid_execution_service import (
    AccountMarkPaidExecutionService,
)

account_logger = logging.getLogger(
    "auneron.account"
)


router = APIRouter(
    prefix="/accounts",
    tags=["Accounts"],
)


read_dependencies = [
    Depends(
        require_permission(
            "clients.view"
        )
    ),
]

manage_dependencies = [
    Depends(
        require_permission(
            "clients.manage"
        )
    ),
]


@router.get(
    "/",
    response_model=list[AccountResponse],
    dependencies=read_dependencies,
)
def list_accounts(
    status_filter: str | None = Query(
        default=None,
        alias="status",
    ),
    cliente: str | None = None,
    skip: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
    ),
    db: Session = Depends(get_db),
):
    query = db.query(Account)

    if status_filter:
        query = query.filter(
            Account.status.ilike(
                status_filter
            )
        )

    if cliente:
        query = query.filter(
            Account.cliente.ilike(
                f"%{cliente}%"
            )
        )

    return (
        query
        .order_by(
            Account.vencimento.asc(),
            Account.id.asc(),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "/{account_id}",
    response_model=AccountResponse,
    dependencies=read_dependencies,
)
def get_account(
    account_id: int,
    db: Session = Depends(get_db),
):
    account = db.get(
        Account,
        account_id,
    )

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    return account


@router.post(
    "/",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_account(
    payload: AccountCreate,
    authenticated: AuthenticatedSession = Depends(
        require_permission("clients.manage")
    ),
    db: Session = Depends(get_db),
):
    account = Account(
        **payload.model_dump()
    )

    db.add(account)
    db.commit()
    db.refresh(account)

    advisory_payload = {
        "id": account.id,
        "cliente": account.cliente,
        "email": account.email,
        "whatsapp": account.whatsapp,
        "valor": account.valor,
        "vencimento": str(
            account.vencimento
        ),
        "status": account.status,
    }
    event_bus.publish(
        "cliente_criado",
        advisory_payload,
    )

    try:
        projection = OrchestratorSkillBindingProjectionService(
            SkillRepository(db)
        )
        assembly = AuthenticatedAdvisoryEnvelopeAssemblyService(
            projection
        )
        envelope = assembly.assemble(
            authenticated=authenticated,
            event_name="cliente_criado",
            payload=advisory_payload,
        )
        AuthenticatedAdvisoryProposalService(db).create(
            envelope=envelope,
            idempotency_key=(
                f"cliente_criado:{account.id}"
            ),
        )
    except Exception:
        account_logger.exception(
            "Falha ao registrar proposta advisory autenticada para "
            "cliente_criado (nao bloqueia a criacao da conta)."
        )

    return account


@router.put(
    "/{account_id}",
    response_model=AccountResponse,
)
def update_account(
    account_id: int,
    payload: AccountUpdate,
    authenticated: AuthenticatedSession = Depends(
        require_permission("clients.manage")
    ),
    db: Session = Depends(get_db),
):
    account = db.get(
        Account,
        account_id,
    )

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    dados = payload.model_dump(
        exclude_unset=True
    )

    previous_status = account.status

    for campo, valor in dados.items():
        setattr(
            account,
            campo,
            valor,
        )

    if "status" in dados and dados["status"] != previous_status:
        db.add(
            AccountEvent(
                account_id=account.id,
                event_type="status_changed",
                actor_type="user",
                actor_reference=f"user:{authenticated.user.id}",
                actor_user_id=authenticated.user.id,
                previous_status=previous_status,
                new_status=dados["status"],
            )
        )

    db.commit()
    db.refresh(account)

    return account


@router.delete(
    "/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=manage_dependencies,
)
def delete_account(
    account_id: int,
    db: Session = Depends(get_db),
):
    account = db.get(
        Account,
        account_id,
    )

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    db.delete(account)
    db.commit()

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
    )


@router.post(
    "/detect-overdue",
)
def detect_overdue_accounts(
    authenticated: AuthenticatedSession = Depends(
        require_permission("clients.detect_overdue")
    ),
    db: Session = Depends(get_db),
):
    """
    Compatibility HTTP trigger for the F1 governed overdue scan.

    The caller's own permission (clients.detect_overdue) only gates
    who may trigger a scan manually -- the observation itself always
    runs under the system_principal provenance, exactly like the
    overdue_detection_maintenance_loop. This route does not build an
    advisory chain under the caller's own AuthenticatedSession
    anymore; it delegates to the same governed OverdueDetectionService
    the background loop uses, so there is only one implementation of
    the scan.
    """
    result = OverdueDetectionService(db).run_scan()
    return result.as_dict()


@router.post(
    "/{account_id}/execute-mark-paid",
)
def execute_account_mark_paid(
    account_id: int,
    payload: AccountMarkPaidExecuteRequest,
    authenticated: AuthenticatedSession = Depends(
        require_permission("approval:decide")
    ),
    db: Session = Depends(get_db),
):
    """
    Executa a transicao governada de uma Account para 'pago'.

    Pressupoe que a aprovacao ja foi solicitada via a rota generica
    POST /approvals/skill-executions/{version_id} (requester humano) e
    decidida via a rota generica POST /approvals/{request_id}/decision
    (decisor humano diferente do requester, garantido pela separacao
    de deveres ja existente em ApprovalService.decide()). Esta rota
    apenas consome a aprovacao ja aprovada, via
    AccountMarkPaidExecutionService (ADR 009 do design doc).
    """
    service = AccountMarkPaidExecutionService(db)

    try:
        result = service.execute(
            account_id=account_id,
            approval_request_id=payload.approval_request_id,
            expected_status=payload.expected_status,
            authority_user_id=authenticated.user.id,
        )
    except ApprovalError as error:
        _raise_approval_http_error(
            error,
            operation="execute",
            user_id=authenticated.user.id,
            request_id=payload.approval_request_id,
        )

    log_approval_event(
        "approval.skill_execution_consumed",
        operation="execute",
        user_id=authenticated.user.id,
        approval_request_id=result.approval_request_id,
        status=result.invocation_status,
        duplicate=result.duplicate,
    )

    return {
        "approval_request_id": result.approval_request_id,
        "approval_consumption_id": result.approval_consumption_id,
        "invocation_id": result.invocation_id,
        "invocation_status": result.invocation_status,
        "duplicate": result.duplicate,
        "output": result.output,
    }


@router.get(
    "/{account_id}/classification",
    response_model=AccountClassificationResponse,
    dependencies=read_dependencies,
)
def get_account_classification(
    account_id: int,
    db: Session = Depends(get_db),
):
    """
    Fatia 2B -- leitura read-only da classificacao ja calculada e
    persistida pela Fatia 2A. Nao recalcula via HTTP, nao cria e nao
    altera classificacao -- so expoe a memoria ativa
    (memory_type='decision', memory_key=client_classification_v1) ja
    existente para o e-mail desta conta.

    status='not_classified_yet' cobre tanto "nunca houve ciclo
    resolvido pra esse e-mail" quanto "conta sem e-mail cadastrado"
    (Fatia 1/2A nunca classificam contas sem e-mail -- mesma regra
    aqui). status='classified' cobre os tres labels, incluindo
    INSUFFICIENT_DATA (e uma classificacao real e persistida, nao
    'ainda nao processado').
    """
    account = db.get(
        Account,
        account_id,
    )

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    memory = None

    if account.email is not None:
        oldest_account_id = _oldest_account_id(
            db,
            account.email,
        )

        if oldest_account_id is not None:
            memory = MemoryRepository(db).find_active_by_key(
                scope_type="account",
                memory_key=CLIENT_CLASSIFICATION_MEMORY_KEY,
                account_id=oldest_account_id,
            )

    if memory is None:
        return AccountClassificationResponse(
            account_id=account.id,
            email=account.email,
            status="not_classified_yet",
            classification=None,
        )

    context = memory.context_data or {}

    return AccountClassificationResponse(
        account_id=account.id,
        email=account.email,
        status="classified",
        classification=ClientClassificationDetail(
            label=context["label"],
            reason=context.get("reason"),
            rule_version=context["rule_version"],
            classified_at=memory.created_at,
            resolved_occurrences=context[
                "ocorrencias_resolvidas"
            ],
            late_occurrences=context.get(
                "ciclos_em_atraso"
            ),
            late_ratio=context.get("proporcao_atraso"),
            minimum_required_occurrences=(
                settings
                .client_behavior_min_occurrences_for_pattern
            ),
            late_ratio_threshold=(
                settings.client_classification_atraso_threshold
            ),
            analysis_scope=context["analysis_scope"],
            period_start=context.get("period_start"),
            period_end=context.get("period_end"),
        ),
    )
