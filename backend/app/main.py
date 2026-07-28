from fastapi import FastAPI, UploadFile, File, APIRouter
import pandas as pd
import io

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
async def upload_accounts(file: UploadFile = File(...)):

    print(f"Arquivo recebido: {file.filename}")

    if file.filename.endswith(".csv"):
        return {
            "versao": "CSV",
            "arquivo": file.filename
        }

    if file.filename.endswith(".xlsx"):
        return {
            "versao": "XLSX",
            "arquivo": file.filename
        }

    return {
        "versao": "ERRO",
        "arquivo": file.filename
    }

app.include_router(router)
