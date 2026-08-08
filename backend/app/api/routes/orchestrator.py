from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query

from app.core.authentication import (
    require_elevated_permission,
    require_permission,
)
from app.orchestrator import (
    decision_engine,
    decision_store,
    metrics_collector,
    registry,
    telemetry_service,
)


router = APIRouter(
    prefix="/orchestrator",
    tags=["AI Orchestrator"],
)


executive_dependencies = [
    Depends(
        require_permission(
            "executive.view"
        )
    ),
]

administration_dependencies = [
    Depends(
        require_elevated_permission(
            "administration.ai-operations"
        )
    ),
]


@router.get(
    "/health",
    dependencies=executive_dependencies,
)
def orchestrator_health() -> dict[str, Any]:
    registry_data = registry.list_registry()
    metrics_summary = metrics_collector.get_summary()

    registered_agents = sum(
        len(agents)
        for agents in registry_data.values()
    )

    return {
        "status": "healthy",
        "orchestrator": "online",
        "registered_events": len(
            registry_data
        ),
        "registered_agents": (
            registered_agents
        ),
        "executions": (
            metrics_summary["executions"]
        ),
        "successes": (
            metrics_summary["successes"]
        ),
        "failures": (
            metrics_summary["failures"]
        ),
        "success_rate": (
            metrics_summary["success_rate"]
        ),
        "stored_decisions": (
            decision_store.count()
        ),
    }


@router.get(
    "/registry",
    dependencies=administration_dependencies,
)
def orchestrator_registry() -> dict[str, Any]:
    registry_data = registry.list_registry()

    registered_agents = sum(
        len(agents)
        for agents in registry_data.values()
    )

    return {
        "events": registry_data,
        "registered_events": len(
            registry_data
        ),
        "registered_agents": (
            registered_agents
        ),
    }


@router.get(
    "/metrics",
    dependencies=administration_dependencies,
)
def orchestrator_metrics() -> dict[str, Any]:
    return {
        "summary": (
            metrics_collector.get_summary()
        ),
        "agents": (
            metrics_collector.get_all_metrics()
        ),
    }


@router.get(
    "/telemetry",
    dependencies=administration_dependencies,
)
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
        pattern=(
            "^(SUCCESS|ERROR|success|error)$"
        ),
    ),
) -> dict[str, Any]:
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


@router.get(
    "/decision/latest",
    dependencies=executive_dependencies,
)
def latest_decision() -> dict[str, Any]:
    latest = decision_store.get_latest()

    if latest is None:
        return {
            "available": False,
            "decision": None,
        }

    return {
        "available": True,
        "decision": latest.to_dict(),
    }


@router.get(
    "/decisions",
    dependencies=executive_dependencies,
)
def list_decisions(
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    decision_name: str | None = Query(
        default=None,
        min_length=2,
        max_length=100,
    ),
    event_name: str | None = Query(
        default=None,
        min_length=2,
        max_length=100,
    ),
) -> dict[str, Any]:
    records = decision_store.list_records(
        limit=limit,
        decision_name=decision_name,
        event_name=event_name,
    )

    return {
        "total_returned": len(records),
        "stored_decisions": (
            decision_store.count()
        ),
        "limit": limit,
        "filters": {
            "decision_name": decision_name,
            "event_name": event_name,
        },
        "records": records,
    }


@router.get(
    "/rules",
    dependencies=administration_dependencies,
)
def orchestrator_rules() -> dict[str, Any]:
    rules = decision_engine.list_rules()

    return {
        "total": len(rules),
        "rules": rules,
    }
