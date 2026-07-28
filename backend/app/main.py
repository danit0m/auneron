from fastapi import FastAPI, UploadFile, File, APIRouter, Depends, HTTPException
from typing import Annotated
import pandas as pd
import io
from sqlalchemy.orm import Session
from ..database.database import SessionLocal, engine, get_db
from ..modules.financeiro.models import Base
from ..modules.importacao.services import import_accounts_from_excel

# Cria as tabelas no banco de dados
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Auneron Finance API",
    version="0.1.0",
    description="Primeira versão da API do Auneron Finance"
)

@app.get("/")
def root():
    return {
        "status": "online",
        "produto": "Auneron Finance",
        "versao": "0.1.0"
    }

@app.get("/health")
def health():
    return {
        "status": "healthy"
    }

router = APIRouter(
    prefix="/upload",
    tags=["Upload"]
)

@router.post("/accounts")
async def upload_accounts(file: Annotated[UploadFile, File(description="Arquivo Excel com dados das contas a receber")], db: Session = Depends(get_db)):
    if not file.filename.endswith((".xls", ".xlsx")):
        raise HTTPException(status_code=400, detail="Formato de arquivo inválido. Por favor, envie um arquivo Excel (.xls ou .xlsx)")

    try:
        contents = await file.read()
        # Chamar o serviço de importação
        import_summary = import_accounts_from_excel(contents, db)
        return {"message": "Importação concluída com sucesso!", "summary": import_summary}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao processar o arquivo: {e}")

app.include_router(router)
