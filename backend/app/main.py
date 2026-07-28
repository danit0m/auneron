from fastapi import FastAPI, UploadFile, File, APIRouter, Depends, HTTPException
from typing import Annotated
import pandas as pd
import io
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import func
from .database.database import engine, SessionLocal, Base
from .database.models import Account

# Cria as tabelas no banco de dados
Base.metadata.create_all(bind=engine)

# Função para obter a sessão do banco de dados
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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
async def upload_accounts(file: Annotated[UploadFile, File(description="Arquivo CSV ou Excel com dados das contas a receber")], db: Session = Depends(get_db)):
    print(f"Arquivo recebido: {file.filename}")

    if not (file.filename.endswith(".csv") or file.filename.endswith(".xlsx")):
        raise HTTPException(status_code=400, detail="Formato de arquivo inválido. Por favor, envie um arquivo CSV ou Excel (.csv ou .xlsx)")

    contents = await file.read()
    df = None
    if file.filename.endswith(".csv"):
        # Tentar diferentes delimitadores e encodings para CSV
        try:
            df = pd.read_csv(io.StringIO(contents.decode("utf-8")))
        except Exception:
            try:
                df = pd.read_csv(io.StringIO(contents.decode("latin-1")), sep=";")
            except Exception:
                raise HTTPException(status_code=400, detail="Não foi possível ler o arquivo CSV. Verifique o delimitador e a codificação.")
    elif file.filename.endswith(".xlsx"):
        df = pd.read_excel(io.BytesIO(contents))

    if df is None:
        raise HTTPException(status_code=500, detail="Erro ao ler o arquivo.")

    required_columns = ["cliente", "email", "whatsapp", "valor", "vencimento", "status"]
    if not all(col in df.columns for col in required_columns):
        missing_cols = [col for col in required_columns if col not in df.columns]
        raise HTTPException(status_code=400, detail=f"Colunas obrigatórias faltando: {", ".join(missing_cols)}")

    imported_count = 0
    error_count = 0

    for index, row in df.iterrows():
        try:
            # Validação e conversão de tipos
            cliente = str(row["cliente"])
            email = str(row["email"])
            whatsapp = str(row["whatsapp"])
            valor = float(row["valor"])
            vencimento = pd.to_datetime(row["vencimento"]).date()
            status = str(row["status"])

            account = Account(
                cliente=cliente,
                email=email,
                whatsapp=whatsapp,
                valor=valor,
                vencimento=vencimento,
                status=status
            )
            db.add(account)
            imported_count += 1
        except Exception as e:
            print(f"Erro ao processar linha {index + 1}: {e}")
            error_count += 1
            continue
    
    db.commit()

    # Calcular indicadores iniciais
    total_imported = db.query(Account).count()
    overdue_accounts = db.query(Account).filter(Account.vencimento < date.today(), Account.status != "pago").count()
    
    # Corrigindo o cálculo do total_open_value
    total_open_value_result = db.query(func.sum(Account.valor)).filter(Account.status != "pago").scalar()
    total_open_value = float(total_open_value_result) if total_open_value_result is not None else 0.0

    return {
        "message": "Importação concluída com sucesso!",
        "summary": {
            "total_records_processed": len(df),
            "records_saved_to_db": imported_count,
            "records_with_errors": error_count,
            "total_accounts_in_db": total_imported,
            "overdue_accounts_count": overdue_accounts,
            "total_open_value": total_open_value
        }
    }

app.include_router(router)
