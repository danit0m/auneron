from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database.database import Base, engine

from app.api.routes.upload import router as upload_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.accounts import router as accounts_router


# Cria as tabelas do banco
Base.metadata.create_all(bind=engine)


app = FastAPI(
    title="Auneron Finance",
    version="2.0",
    description="Sistema de gestão financeira do Auneron",
)


# Permite que o frontend React acesse a API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Home"])
def home():
    return {
        "status": "online",
        "produto": "Auneron Finance",
        "versao": "2.0",
    }


@app.get("/health", tags=["Health"])
def health():
    return {
        "status": "healthy",
    }


# ===========================
# REGISTRO DAS ROTAS
# ===========================

app.include_router(upload_router)
app.include_router(dashboard_router)
app.include_router(accounts_router)