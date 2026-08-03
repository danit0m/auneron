from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.services.executive_service import ExecutiveService

router = APIRouter(
    prefix="/brain",
    tags=["Executive AI"],
)


@router.get("/executive")
def executive_report(
    db: Session = Depends(get_db),
):
    """
    Retorna o resumo executivo produzido
    pelo ExecutiveService.
    """

    return ExecutiveService.generate_report(db)