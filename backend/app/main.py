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
from app.core.pilot_mutation_maintenance import (
    pilot_mutation_maintenance_loop,
    run_pilot_mutation_recovery_async,
)
from app.core.authenticated_advisory_dispatch_maintenance import (
    advisory_dispatch_maintenance_loop,
    run_advisory_dispatch_recovery_async,
)
from app.core.overdue_detection_maintenance import (
    overdue_detection_maintenance_loop,
    run_overdue_detection_async,
)
from app.core.client_behavior_memory_maintenance import (
    client_behavior_memory_maintenance_loop,
    run_client_behavior_memory_recalculation_async,
)
from app.core.client_classification import (
    client_classification_maintenance_loop,
    run_client_classification_recalculation_async,
)
from app.core.receivables_monitor_maintenance import (
    receivables_monitor_maintenance_loop,
    run_receivables_monitor_async,
)
from app.database.database import (
    check_database_connection,
    engine,
)

from app.api.routes.upload import router as upload_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.accounts import router as accounts_router
from app.api.routes.action_space_evaluator import (
    router as action_space_evaluator_router,
)
from app.api.routes.approvals import router as approvals_router
from app.api.routes.auth import router as auth_router
from app.api.routes.brain import router as brain_router
from app.api.routes.customer_context import (
    router as customer_context_router,
)
from app.api.routes.executive import router as executive_router
from app.api.routes.health import router as health_router
from app.api.routes.human_escalation import (
    router as human_escalation_router,
)
from app.api.routes.mark_overdue_recommendation import (
    router as mark_overdue_recommendation_router,
)
from app.api.routes.mark_paid_recommendation import (
    router as mark_paid_recommendation_router,
)
from app.api.routes.memory import router as memory_router
from app.api.routes.nba_policy import router as nba_policy_router
from app.api.routes.orchestrator import router as orchestrator_router
from app.api.routes.outcome import router as outcome_router
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
        await run_pilot_mutation_recovery_async()
        await run_overdue_detection_async()
        await run_advisory_dispatch_recovery_async()
        await run_client_behavior_memory_recalculation_async()
        await run_client_classification_recalculation_async()
        await run_receivables_monitor_async()

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
        asyncio.create_task(
            pilot_mutation_maintenance_loop()
        ),
        asyncio.create_task(
            advisory_dispatch_maintenance_loop()
        ),
        asyncio.create_task(
            overdue_detection_maintenance_loop()
        ),
        asyncio.create_task(
            client_behavior_memory_maintenance_loop()
        ),
        asyncio.create_task(
            client_classification_maintenance_loop()
        ),
        asyncio.create_task(
            receivables_monitor_maintenance_loop()
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

# Outcome Intelligence expõe somente projeção read-only sobre dados já
# existentes (Knowledge, Approval, Work, AccountEvent). Não muta nada,
# não decide nada. A API key permanece no router; approval:read é
# aplicado no endpoint.
app.include_router(
    outcome_router,
    dependencies=service_dependencies,
)

# Customer Intelligence 360 compõe Account/AccountEvent/Knowledge/
# Memory e Outcome (via get_outcome_episode, nunca por alteração dos
# arquivos de Outcome) numa projeção read-only agrupada por email
# (heurística, não identidade -- ver customer_context.py). A API key
# permanece no router; approval:read é aplicado no endpoint, mesma
# permissão do Outcome, para não expor dado gateado por Approval por
# uma porta lateral.
app.include_router(
    customer_context_router,
    dependencies=service_dependencies,
)

# Human Escalation Recommendation (Pilot Action Space V1.B) expõe
# apenas elegibilidade read-only de escalate_to_human para um
# financial_episode -- nunca cria WorkItem. Compõe Customer 360 e
# Outcome (via suas funções públicas, sem alteração dos 14 arquivos
# daquelas fatias). A API key permanece no router; approval:read é
# aplicado no endpoint, mesma permissão de Outcome/Customer 360, para
# não expor a mesma superfície sensível por uma porta lateral.
app.include_router(
    human_escalation_router,
    dependencies=service_dependencies,
)

# Governed Financial Action Eligibility (Pilot Action Space V1.C) expõe
# apenas a elegibilidade read-only de account.mark_overdue e
# account.mark_paid -- structurally_available/system_recommendable/
# requires_external_fact, nunca eligible_actions()/no_action (isso
# pertence ao futuro Action Space Evaluator). A API key permanece no
# router; approval:read é aplicado no endpoint, mesma permissão de
# Outcome/Customer 360/Human Escalation.
app.include_router(
    mark_overdue_recommendation_router,
    dependencies=service_dependencies,
)

app.include_router(
    mark_paid_recommendation_router,
    dependencies=service_dependencies,
)

# Action Space Evaluator compõe, por leitura, as três capabilities já
# fechadas (mark_overdue/mark_paid/escalate_to_human) em uma única
# projeção por financial_episode -- eligible_actions()/no_action, sem
# ranking. Nenhum dos três contratos fonte é alterado. A API key
# permanece no router; approval:read é aplicado no endpoint, mesma
# permissão da família inteira de recomendação.
app.include_router(
    action_space_evaluator_router,
    dependencies=service_dependencies,
)

# Next Best Action (NBA V1) compõe, por leitura, o Action Space
# Evaluator (autoridade exclusiva sobre elegibilidade) com fatos
# read-only adicionais (lifecycle, Account.valor, Customer 360) para
# aplicar uma política determinística e versionada (nba_policy_v1) que
# escolhe zero, uma ou múltiplas ações já recomendáveis. Nunca recalcula
# elegibilidade, nunca ranqueia, nunca executa. A API key permanece no
# router; approval:read é aplicado no endpoint, mesma permissão da
# família inteira de recomendação.
app.include_router(
    nba_policy_router,
    dependencies=service_dependencies,
)

# Agent Skills expõe apenas execução explícita de versão publicada.
# A sessão autenticada define o ator; RBAC e capability scope são
# resolvidos antes do runtime. Seleção autônoma continua no Commit 24.
app.include_router(
    skills_router,
    dependencies=service_dependencies,
)
