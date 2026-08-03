from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Response
from fastapi import status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.knowledge import KnowledgeResponse
from app.schemas.knowledge import KnowledgeResolveResponse
from app.services.knowledge_service import KnowledgeService


router = APIRouter(
    prefix="/brain",
    tags=["Brain"],
)


@router.get(
    "/",
    response_model=list[KnowledgeResponse],
)
def list_knowledge(
    skip: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    resolved: bool | None = Query(
        default=None,
    ),
    agent_name: str | None = Query(
        default=None,
    ),
    severity: str | None = Query(
        default=None,
    ),
    account_id: int | None = Query(
        default=None,
        ge=1,
    ),
    db: Session = Depends(get_db),
):
    if account_id is not None:
        return KnowledgeService.find_by_account(
            db=db,
            account_id=account_id,
            skip=skip,
            limit=limit,
        )

    if agent_name:
        return KnowledgeService.find_by_agent(
            db=db,
            agent_name=agent_name,
            skip=skip,
            limit=limit,
        )

    if severity:
        return KnowledgeService.find_by_severity(
            db=db,
            severity=severity,
            skip=skip,
            limit=limit,
        )

    return KnowledgeService.list(
        db=db,
        skip=skip,
        limit=limit,
        resolved=resolved,
    )


@router.get(
    "/{knowledge_id}",
    response_model=KnowledgeResponse,
)
def get_knowledge(
    knowledge_id: int,
    db: Session = Depends(get_db),
):
    knowledge = KnowledgeService.get_by_id(
        db=db,
        knowledge_id=knowledge_id,
    )

    if knowledge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conhecimento não encontrado.",
        )

    return knowledge


@router.patch(
    "/{knowledge_id}/resolve",
    response_model=KnowledgeResolveResponse,
)
def resolve_knowledge(
    knowledge_id: int,
    db: Session = Depends(get_db),
):
    knowledge = KnowledgeService.mark_resolved(
        db=db,
        knowledge_id=knowledge_id,
    )

    if knowledge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conhecimento não encontrado.",
        )

    return {
        "id": knowledge.id,
        "resolved": knowledge.resolved,
        "message": "Conhecimento marcado como resolvido.",
    }


@router.patch(
    "/{knowledge_id}/reopen",
    response_model=KnowledgeResolveResponse,
)
def reopen_knowledge(
    knowledge_id: int,
    db: Session = Depends(get_db),
):
    knowledge = KnowledgeService.reopen(
        db=db,
        knowledge_id=knowledge_id,
    )

    if knowledge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conhecimento não encontrado.",
        )

    return {
        "id": knowledge.id,
        "resolved": knowledge.resolved,
        "message": "Conhecimento reaberto.",
    }


@router.delete(
    "/{knowledge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_knowledge(
    knowledge_id: int,
    db: Session = Depends(get_db),
):
    deleted = KnowledgeService.delete(
        db=db,
        knowledge_id=knowledge_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conhecimento não encontrado.",
        )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
    )