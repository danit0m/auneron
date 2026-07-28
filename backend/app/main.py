from fastapi import FastAPI, UploadFile, File
from typing import Annotated
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

@app.post("/upload/accounts", tags=["Upload"])
async def upload_accounts_excel(file: Annotated[UploadFile, File(description="Arquivo Excel com dados das contas")]):
    if not file.filename.endswith((".xls", ".xlsx")):
        return {"message": "Formato de arquivo inválido. Por favor, envie um arquivo Excel (.xls ou .xlsx)"}

    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        # Aqui você pode processar o DataFrame (df) como desejar
        # Por exemplo, salvar no banco de dados, validar dados, etc.
        return {"message": f"Arquivo {file.filename} recebido e processado com sucesso!", "data_preview": df.head().to_dict()}
    except Exception as e:
        return {"message": f"Erro ao processar o arquivo: {e}"}
