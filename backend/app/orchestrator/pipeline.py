from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

from app.orchestrator.metrics import (
    metrics_collector,
)
from app.orchestrator.registry import (
    RegisteredAgent,
)
from app.orchestrator.telemetry import (
    telemetry_service,
)


@dataclass(frozen=True)
class AgentExecutionResult:
    agent_name: str
    priority: int
    success: bool
    duration_seconds: float
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    event_name: str
    strategy_name: str
    selected_agents: int
    completed_agents: int
    failed_agents: int
    duration_seconds: float
    executions: tuple[
        AgentExecutionResult,
        ...,
    ]


class ExecutionPipeline:
    """
    Executa agentes selecionados pelo Orchestrator.

    Não decide quais agentes serão usados.
    Essa responsabilidade pertence à Strategy Engine.
    """

    @staticmethod
    def execute(
        *,
        event_name: str,
        strategy_name: str,
        agents: list[RegisteredAgent],
        payload: dict[str, Any],
    ) -> PipelineResult:
        pipeline_start = perf_counter()

        results: list[
            AgentExecutionResult
        ] = []

        for agent in agents:
            result = (
                ExecutionPipeline._execute_agent(
                    event_name=event_name,
                    strategy_name=strategy_name,
                    agent=agent,
                    payload=payload,
                )
            )

            results.append(result)

        duration = (
            perf_counter()
            - pipeline_start
        )

        completed = sum(
            1
            for result in results
            if result.success
        )

        failed = sum(
            1
            for result in results
            if not result.success
        )

        return PipelineResult(
            event_name=event_name,
            strategy_name=strategy_name,
            selected_agents=len(agents),
            completed_agents=completed,
            failed_agents=failed,
            duration_seconds=duration,
            executions=tuple(results),
        )

    @staticmethod
    def _execute_agent(
        *,
        event_name: str,
        strategy_name: str,
        agent: RegisteredAgent,
        payload: dict[str, Any],
    ) -> AgentExecutionResult:
        started_at = datetime.now()
        timer_start = perf_counter()

        print()
        print(
            f"Executando: {agent.name} "
            f"(prioridade {agent.priority})"
        )

        error: Exception | None = None

        try:
            agent.handler(payload)
            success = True

        except Exception as caught_error:
            success = False
            error = caught_error

        finished_at = datetime.now()

        duration = (
            perf_counter()
            - timer_start
        )

        metrics_collector.record_execution(
            agent_name=agent.name,
            duration_seconds=duration,
            success=success,
        )

        telemetry_service.create_record(
            event_name=event_name,
            strategy_name=strategy_name,
            agent_name=agent.name,
            priority=agent.priority,
            status=(
                "SUCCESS"
                if success
                else "ERROR"
            ),
            duration_seconds=duration,
            started_at=started_at,
            finished_at=finished_at,
            error=error,
        )

        if success:
            print(
                f"{agent.name} concluído "
                f"em {duration:.4f}s."
            )
        else:
            print(
                f"Erro ao executar {agent.name} "
                f"após {duration:.4f}s."
            )
            print(
                f"{type(error).__name__}: "
                f"{error}"
            )

        return AgentExecutionResult(
            agent_name=agent.name,
            priority=agent.priority,
            success=success,
            duration_seconds=duration,
            error_type=(
                type(error).__name__
                if error is not None
                else None
            ),
            error_message=(
                str(error)
                if error is not None
                else None
            ),
        )