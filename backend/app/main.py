from fastapi import FastAPI

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
