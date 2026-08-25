import asyncio
import logging
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import Depends
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.approval_http import ApprovalHTTPMiddleware
from app.core.approval_http import (
    application_http_exception_handler as approval_http_exception_handler,
)
from app.core.approval_http import (
    application_validation_exception_handler as approval_validation_exception_handler,
)
from app.core.authentication import require_permission
from app.core.config import settings
from app.core.http_security import (
    SecurityHeadersMiddleware,
)
from app.core.memory_http import MemoryHTTPMiddleware
from app.core.skill_http import SkillHTTPMiddleware
from app.core.work_http import WorkHTTPMiddleware
from app.core.observability import (
    RequestObservabilityMiddleware,
)
from app.core.observability import (
    configure_logging,
)
from app.core.security import require_api_key
from app.core.session_maintenance import (
    auth_session_maintenance_loop,
)
from app.core.session_maintenance import (
    run_auth_session_cleanup,
)
from app.core.skill_maintenance import (
    run_skill_invocation_recovery,
)
from app.core.skill_maintenance import (
    skill_invocation_maintenance_loop,
)
from app.core.work_skill_maintenance import (
    run_work_skill_execution_recovery_async,
)
from app.core.work_skill_maintenance import (
    work_skill_execution_maintenance_loop,
)
from app.core.work_outcome_evaluation_maintenance import (
    run_work_outcome_evaluation_recovery_async,
)
from app.core.work_outcome_evaluation_maintenance import (
    work_outcome_evaluation_maintenance_loop,
)
from app.database.database import (
    check_database_connection,
    engine,
)

from app.api.routes.upload import router as upload_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.accounts import router as accounts_router
from app.api.routes.approvals import router as approvals_router
from app.api.routes.auth import router as auth_router
from app.api.routes.brain import router as brain_router
from app.api.routes.executive import router as executive_router
from app.api.routes.health import router as health_router
from app.api.routes.memory import router as memory_router
from app.api.routes.orchestrator import router as orchestrator_router
from app.api.routes.skills import router as skills_router
from app.api.routes.work import router as work_router

import app.agents.finance_agent
import app.agents.analytics_agent
import app.models
import app.agents.risk_agent


configure_logging()

application_logger = logging.getLogger(
    "auneron.application"
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database_online = (
        check_database_connection()
    )

    if database_online:
        await asyncio.to_thread(
            run_auth_session_cleanup
        )
        await asyncio.to_thread(
            run_skill_invocation_recovery
        )
        await run_work_skill_execution_recovery_async()
        await run_work_outcome_evaluation_recovery_async()

    maintenance_tasks = (
        asyncio.create_task(
            auth_session_maintenance_loop()
        ),
        asyncio.create_task(
            skill_invocation_maintenance_loop()
        ),
        asyncio.create_task(
            work_skill_execution_maintenance_loop()
        ),
        asyncio.create_task(
            work_outcome_evaluation_maintenance_loop()
        ),
    )

    application_logger.info(
        "application_started",
        extra={
            "event": "application_lifecycle",
            "state": "started",
            "environment": settings.environment,
            "version": settings.app_version,
            "database_online": database_online,
        },
    )

    try:
        yield
    finally:
        for maintenance_task in maintenance_tasks:
            maintenance_task.cancel()

        for maintenance_task in maintenance_tasks:
            with suppress(
                asyncio.CancelledError
            ):
                await maintenance_task

        engine.dispose()

        application_logger.info(
            "application_stopped",
            extra={
                "event": "application_lifecycle",
                "state": "stopped",
                "environment": settings.environment,
                "version": settings.app_version,
                "database_pool_disposed": True,
            },
        )


production_mode = (
    settings.environment == "production"
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Intelligent Business Operating System"
    ),
    lifespan=lifespan,
    docs_url=(
        None
        if production_mode
        else "/docs"
    ),
    redoc_url=(
        None
        if production_mode
        else "/redoc"
    ),
    openapi_url=(
        None
        if production_mode
        else "/openapi.json"
    ),
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=(
            settings.cors_origin_list
        ),
        allow_credentials=True,
        allow_methods=[
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
        ],
        allow_headers=[
            "Accept",
            "Content-Type",
            "X-Request-ID",
            "Idempotency-Key",
        ],
        expose_headers=[
            "X-Request-ID",
        ],
        max_age=600,
    )

app.add_middleware(
    ApprovalHTTPMiddleware,
)

app.add_middleware(
    MemoryHTTPMiddleware,
)

app.add_middleware(
    WorkHTTPMiddleware,
)

app.add_middleware(
    SkillHTTPMiddleware,
)

app.add_middleware(
    SecurityHeadersMiddleware,
    production=production_mode,
)

app.add_middleware(
    RequestObservabilityMiddleware
)

app.add_exception_handler(
    StarletteHTTPException,
    approval_http_exception_handler,
)
app.add_exception_handler(
    RequestValidationError,
    approval_validation_exception_handler,
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


# Health/readiness são públicos para probes operacionais.
# /health mede somente o processo; /ready valida dependências.
app.include_router(
    health_router
)


service_dependencies = [
    Depends(require_api_key),
]


def business_dependencies(
    permission,
):
    return [
        *service_dependencies,
        Depends(
            require_permission(
                permission
            )
        ),
    ]


app.include_router(
    auth_router,
    dependencies=service_dependencies,
)

app.include_router(
    upload_router,
    dependencies=business_dependencies(
        "imports.execute"
    ),
)
app.include_router(
    dashboard_router,
    dependencies=business_dependencies(
        "dashboard.view"
    ),
)

# Accounts têm permissões diferentes para leitura e escrita.
# O X-API-Key permanece no nível do router; clients.view/manage
# são aplicados diretamente em cada endpoint.
app.include_router(
    accounts_router,
    dependencies=service_dependencies,
)

app.include_router(
    executive_router,
    dependencies=business_dependencies(
        "executive.view"
    ),
)

# O router do Orchestrator possui endpoints executivos e
# administrativos. A autorização de usuário é aplicada
# diretamente em cada endpoint.
app.include_router(
    orchestrator_router,
    dependencies=service_dependencies,
)

app.include_router(
    brain_router,
    dependencies=business_dependencies(
        "brain.view"
    ),
)

# Memory possui permissoes distintas por operacao e escopo.
# A API key permanece no nivel do router; sessao, RBAC e scope
# authorization sao aplicados diretamente em cada endpoint.
app.include_router(
    memory_router,
    dependencies=service_dependencies,
)

# Work Manager possui RBAC por operação, autorização por escopo
# e ator vinculado exclusivamente à sessão autenticada.
app.include_router(
    work_router,
    dependencies=service_dependencies,
)

# Approval expõe somente operações humanas de proposta, fila e decisão.
# A API key permanece no router; sessão e RBAC são aplicados por endpoint.
# Nenhuma rota desta camada executa Skills.
app.include_router(
    approvals_router,
    dependencies=service_dependencies,
)

# Agent Skills expõe apenas execução explícita de versão publicada.
# A sessão autenticada define o ator; RBAC e capability scope são
# resolvidos antes do runtime. Seleção autônoma continua no Commit 24.
app.include_router(
    skills_router,
    dependencies=service_dependencies,
)
