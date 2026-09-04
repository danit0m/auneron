import logging
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.agents.event_bus import event_bus
from app.core.authentication import AuthenticatedSession
from app.core.authentication import require_permission
from app.database.database import get_db
from app.models.account import Account
from app.models.account_event import AccountEvent
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
from app.models.authenticated_advisory_proposal import (
    AuthenticatedAdvisoryProposal,
)
from app.services.authenticated_advisory_proposal_approval_bridge_service import (
    AuthenticatedAdvisoryProposalApprovalBridgeService,
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


def _pilot_mutating_binding_id(
    proposal: AuthenticatedAdvisoryProposal,
) -> int:
    """
    Le o snapshot imutavel da proposal e retorna o binding_id do
    unico candidato mutating (a skill account.mark_overdue, no
    piloto atual). Levanta ValueError se nao houver exatamente um
    candidato -- nunca escolhe um binding "por acaso".
    """
    matches: list[int] = []

    for agent in proposal.snapshot_payload.get("agents", []):
        for binding in agent.get("bindings", []):
            if binding.get("execution_mode") == "mutating":
                matches.append(binding["binding_id"])

    if len(matches) != 1:
        raise ValueError(
            "Esperava exatamente 1 binding mutating na proposal "
            f"{proposal.id}, encontrado {len(matches)}."
        )

    return matches[0]


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
    aprovacoes_solicitadas = 0
    aprovacoes_falharam = 0

    for account in overdue_accounts:
        idempotency_key = f"conta_vencida:{account.id}"

        existente = AuthenticatedAdvisoryProposalRepository(
            db
        ).find_by_idempotency_key(
            idempotency_key=idempotency_key
        )

        if existente is not None:
            ja_com_proposta += 1
            proposal = existente
        else:
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
                creation = AuthenticatedAdvisoryProposalService(db).create(
                    envelope=envelope,
                    idempotency_key=idempotency_key,
                )
                proposal = creation.proposal
                propostas_criadas += 1
            except Exception:
                falhas += 1
                account_logger.exception(
                    "Falha ao registrar proposta advisory autenticada para "
                    "conta_vencida (conta_id=%s).",
                    account.id,
                )
                continue

        try:
            binding_id = _pilot_mutating_binding_id(proposal)
            AuthenticatedAdvisoryProposalApprovalBridgeService(db).request_approval(
                proposal_id=proposal.id,
                authenticated=authenticated,
                binding_id=binding_id,
                input_payload={
                    "account_id": account.id,
                    "expected_status": "aberto",
                    "expected_due_date": str(
                        account.vencimento
                    ),
                },
            )
            aprovacoes_solicitadas += 1
        except Exception:
            aprovacoes_falharam += 1
            account_logger.exception(
                "Falha ao solicitar aprovacao para conta_vencida "
                "(conta_id=%s, proposal_id=%s).",
                account.id,
                proposal.id,
            )

    return {
        "contas_verificadas": len(overdue_accounts),
        "propostas_criadas": propostas_criadas,
        "ja_com_proposta": ja_com_proposta,
        "falhas": falhas,
        "aprovacoes_solicitadas": aprovacoes_solicitadas,
        "aprovacoes_falharam": aprovacoes_falharam,
    }
