from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.database.database import (
    Base,
    check_database_connection,
    engine,
)

from app.api.routes.upload import router as upload_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.accounts import router as accounts_router
from app.api.routes.brain import router as brain_router
from app.api.routes.executive import router as executive_router
from app.api.routes.orchestrator import router as orchestrator_router
from app.agents.notification_agent import NotificationAgent

import app.agents.finance_agent
import app.agents.analytics_agent
import app.models
import app.agents.risk_agent


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Intelligent Business Operating System",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Home"])
def home():
    return {
        "status": "online",
        "produto": settings.app_name,
        "versao": settings.app_version,
        "ambiente": settings.environment,
        "agent_hub": "ativo",
    }


@app.get("/health", tags=["Health"])
def health():
    database_online = check_database_connection()

    return {
        "status": "healthy" if database_online else "degraded",
        "database": "online" if database_online else "offline",
        "database_engine": (
            "postgresql"
            if settings.is_postgresql
            else "sqlite"
        ),
        "agents": [
            "FinanceAgent",
            "AnalyticsAgent",
            "NotificationAgent",
            "RiskAgent",
        ],
    }


app.include_router(upload_router)
app.include_router(dashboard_router)
app.include_router(accounts_router)
app.include_router(executive_router)
app.include_router(orchestrator_router)
app.include_router(brain_router)
