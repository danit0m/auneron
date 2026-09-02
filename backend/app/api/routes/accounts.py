import logging
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.agents.event_bus import event_bus
from app.core.authentication import AuthenticatedSession
from app.core.authentication import require_permission
from app.database.database import get_db
from app.models.account import Account
from app.repositories.authenticated_advisory_proposal_repository import (
    AuthenticatedAdvisoryProposalRepository,
)
from app.repositories.skill_repository import SkillRepository
from app.schemas.account import (
    AccountCreate,
    AccountResponse,
    AccountUpdate,
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
    dependencies=manage_dependencies,
)
def update_account(
    account_id: int,
    payload: AccountUpdate,
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

    for campo, valor in dados.items():
        setattr(
            account,
            campo,
            valor,
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
    hoje = date.today()

    overdue_accounts = (
        db.query(Account)
        .filter(
            Account.status == "aberto",
            Account.vencimento < hoje,
        )
        .all()
    )

    propostas_criadas = 0
    ja_com_proposta = 0
    falhas = 0

    for account in overdue_accounts:
        idempotency_key = f"conta_vencida:{account.id}"

        existente = AuthenticatedAdvisoryProposalRepository(
            db
        ).find_by_idempotency_key(
            idempotency_key=idempotency_key
        )
        if existente is not None:
            ja_com_proposta += 1
            continue

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

        try:
            projection = OrchestratorSkillBindingProjectionService(
                SkillRepository(db)
            )
            assembly = AuthenticatedAdvisoryEnvelopeAssemblyService(
                projection
            )
            envelope = assembly.assemble(
                authenticated=authenticated,
                event_name="conta_vencida",
                payload=advisory_payload,
            )
            AuthenticatedAdvisoryProposalService(db).create(
                envelope=envelope,
                idempotency_key=idempotency_key,
            )
            propostas_criadas += 1
        except Exception:
            falhas += 1
            account_logger.exception(
                "Falha ao registrar proposta advisory autenticada para "
                "conta_vencida (conta_id=%s).",
                account.id,
            )

    return {
        "contas_verificadas": len(overdue_accounts),
        "propostas_criadas": propostas_criadas,
        "ja_com_proposta": ja_com_proposta,
        "falhas": falhas,
    }
