from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.authentication import require_permission
from app.core.nba_policy import get_nba_decision
from app.database.database import get_db
from app.models.account import Account
from app.schemas.nba_policy import NbaDecisionEvidenceResponse
from app.schemas.nba_policy import NbaRecommendationSnapshotPayload
from app.services.nba_recommendation_snapshot_service import (
    NbaRecommendationSnapshotService,
)


router = APIRouter(
    prefix="/recommendations/next-best-action",
    tags=["Next Best Action"],
)

read_dependencies = [
    Depends(require_permission("approval:read")),
]


@router.get(
    "/accounts/{account_id}/episodes/{due_date}",
    response_model=NbaDecisionEvidenceResponse,
    dependencies=read_dependencies,
)
def get_account_nba_decision(
    account_id: int,
    due_date: date,
    db: Session = Depends(get_db),
) -> NbaDecisionEvidenceResponse:
    account = db.get(Account, account_id)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conta não encontrada.",
        )

    result = get_nba_decision(
        db,
        account=account,
        due_date=due_date,
    )

    # I2 emendado (DW-6.4A): o unico efeito colateral permitido neste
    # GET e o apendice append-only de proveniencia abaixo -- nunca
    # muta Account/WorkItem/Knowledge/MemoryItem/Approval/Execution/
    # BusinessEffectVerification. get_nba_decision() acima permanece
    # puro; o snapshot e construido a partir do NbaDecisionEvidence ja
    # computado, nunca recalculado.
    snapshot = NbaRecommendationSnapshotService(db).persist(
        result
    )

    # NbaDecisionEvidenceResponse exige recommendation_snapshot_id,
    # inexistente em `result` (NbaDecisionEvidence de dominio, DW-6.4A
    # congelado como nunca ganhando esse campo) -- por isso a validacao
    # passa primeiro pela forma sem o ID (mesma usada para o digest) e
    # so entao o ID e adicionado, evitando ValidationError por campo
    # ausente.
    payload = NbaRecommendationSnapshotPayload.model_validate(
        result
    )
    return NbaDecisionEvidenceResponse(
        **payload.model_dump(),
        recommendation_snapshot_id=snapshot.id,
    )
