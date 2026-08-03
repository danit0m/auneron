from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database.database import Base, engine

from app.api.routes.upload import router as upload_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.accounts import router as accounts_router
from app.api.routes.brain import router as brain_router
from app.api.routes.executive import router as executive_router
from app.agents.notification_agent import NotificationAgent

# ======================================================
# AGENT HUB
# Apenas importar os agentes já registra os eventos
# ======================================================

import app.agents.finance_agent
import app.agents.analytics_agent
import app.models

# Futuramente:
# import app.agents.analytics_agent
# import app.agents.crm_agent
# import app.agents.notification_agent

# ======================================================
# BANCO DE DADOS
# ======================================================

Base.metadata.create_all(bind=engine)

# ======================================================
# FASTAPI
# ======================================================

app = FastAPI(
    title="Auneron AI",
    version="3.0 Alpha",
    description="Intelligent Business Operating System",
)

# ======================================================
# CORS
# ======================================================

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

# ======================================================
# HOME
# ======================================================

@app.get("/", tags=["Home"])
def home():
    return {
        "status": "online",
        "produto": "Auneron AI",
        "versao": "3.0 Alpha",
        "agent_hub": "ativo",
    }

# ======================================================
# HEALTH
# ======================================================

@app.get("/health", tags=["Health"])
def health():
    return {
        "status": "healthy",
        "database": "online",
        "agents": [
            "FinanceAgent",
            "AnalyticsAgent",
        ],
    }
# ======================================================
# ROTAS
# ======================================================

app.include_router(upload_router)
app.include_router(dashboard_router)
app.include_router(accounts_router)
app.include_router(executive_router)
app.include_router(brain_router)
