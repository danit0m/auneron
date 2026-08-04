from fastapi import APIRouter
from fastapi import Query

from app.orchestrator import (
    metrics_collector,
    registry,
    telemetry_service,
)


router = APIRouter(
    prefix="/orchestrator",
    tags=["AI Orchestrator"],
)


@router.get("/health")
def orchestrator_health() -> dict:
    """
    Retorna o estado geral do AI Orchestrator.
    """

    registry_data = registry.list_registry()
    metrics_summary = metrics_collector.get_summary()

    registered_agents = sum(
        len(agents)
        for agents in registry_data.values()
    )

    return {
        "status": "healthy",
        "orchestrator": "online",
        "registered_events": len(registry_data),
        "registered_agents": registered_agents,
        "executions": metrics_summary["executions"],
        "successes": metrics_summary["successes"],
        "failures": metrics_summary["failures"],
        "success_rate": metrics_summary["success_rate"],
    }


@router.get("/registry")
def orchestrator_registry() -> dict:
    """
    Lista os eventos e agentes registrados,
    incluindo suas prioridades.
    """

    registry_data = registry.list_registry()

    registered_agents = sum(
        len(agents)
        for agents in registry_data.values()
    )

    return {
        "events": registry_data,
        "registered_events": len(registry_data),
        "registered_agents": registered_agents,
    }


@router.get("/metrics")
def orchestrator_metrics() -> dict:
    """
    Retorna as métricas acumuladas dos agentes.

    As métricas permanecem em memória e são
    reiniciadas quando o backend é reiniciado.
    """

    return {
        "summary": metrics_collector.get_summary(),
        "agents": metrics_collector.get_all_metrics(),
    }


@router.get("/telemetry")
def orchestrator_telemetry(
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
    agent_name: str | None = Query(
        default=None,
        min_length=2,
        max_length=100,
    ),
    status: str | None = Query(
        default=None,
        pattern="^(SUCCESS|ERROR|success|error)$",
    ),
) -> dict:
    """
    Retorna os registros recentes de telemetria.

    Permite filtrar por agente e status.
    """

    normalized_status = (
        status.upper()
        if status is not None
        else None
    )

    records = telemetry_service.list_records(
        limit=limit,
        agent_name=agent_name,
        status=normalized_status,
    )

    return {
        "total_returned": len(records),
        "limit": limit,
        "filters": {
            "agent_name": agent_name,
            "status": normalized_status,
        },
        "records": records,
    }