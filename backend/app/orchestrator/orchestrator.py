from datetime import datetime
from time import perf_counter
from typing import Any

from app.orchestrator.pipeline import (
    ExecutionPipeline,
    PipelineResult,
)
from app.orchestrator.registry import (
    registry,
)
from app.orchestrator.strategy import (
    OrchestrationPlan,
    OrchestrationStrategy,
)


class AIOrchestrator:
    """
    Coordena o ciclo completo de orquestração.

    Responsabilidades:
    - receber o evento;
    - solicitar o plano à Strategy Engine;
    - selecionar os agentes;
    - delegar a execução ao Pipeline;
    - apresentar o resumo.
    """

    @staticmethod
    def execute(
        event_name: str,
        payload: dict[str, Any],
    ) -> PipelineResult | None:
        orchestration_start = perf_counter()

        plan = (
            OrchestrationStrategy.build_plan(
                event_name=event_name,
                payload=payload,
            )
        )

        available_agents = (
            registry.get_agents(
                event_name,
            )
        )

        selected_agents = (
            registry.get_selected_agents(
                event_name,
                plan.selected_agents,
            )
        )

        AIOrchestrator._print_header(
            event_name=event_name,
            plan=plan,
            available_count=len(
                available_agents
            ),
            selected_count=len(
                selected_agents
            ),
        )

        if not selected_agents:
            duration = (
                perf_counter()
                - orchestration_start
            )

            print(
                "Nenhum agente foi selecionado."
            )
            print(
                f"Tempo total: {duration:.4f}s"
            )
            print(
                "=========================================="
            )
            print()

            return None

        pipeline_result = (
            ExecutionPipeline.execute(
                event_name=event_name,
                strategy_name=(
                    plan.strategy_name
                ),
                agents=selected_agents,
                payload=payload,
            )
        )

        orchestration_duration = (
            perf_counter()
            - orchestration_start
        )

        ignored = (
            len(available_agents)
            - len(selected_agents)
        )

        AIOrchestrator._print_summary(
            result=pipeline_result,
            ignored_agents=ignored,
            orchestration_duration=(
                orchestration_duration
            ),
        )

        return pipeline_result

    @staticmethod
    def _print_header(
        *,
        event_name: str,
        plan: OrchestrationPlan,
        available_count: int,
        selected_count: int,
    ) -> None:
        print()
        print(
            "=========================================="
        )
        print("AI ORCHESTRATOR")
        print(
            "=========================================="
        )
        print(f"Evento: {event_name}")
        print(
            "Horário: "
            f"{datetime.now().isoformat(timespec='seconds')}"
        )
        print(
            f"Estratégia: {plan.strategy_name}"
        )
        print(f"Motivo: {plan.reason}")
        print(
            f"Agentes disponíveis: "
            f"{available_count}"
        )
        print(
            f"Agentes selecionados: "
            f"{selected_count}"
        )

        if plan.selected_agents:
            print(
                "Plano solicitado: "
                + " → ".join(
                    plan.selected_agents
                )
            )

    @staticmethod
    def _print_summary(
        *,
        result: PipelineResult,
        ignored_agents: int,
        orchestration_duration: float,
    ) -> None:
        print()
        print(
            "Resumo da orquestração:"
        )
        print(
            "Agentes concluídos: "
            f"{result.completed_agents}"
        )
        print(
            "Agentes ignorados: "
            f"{ignored_agents}"
        )
        print(
            "Falhas: "
            f"{result.failed_agents}"
        )
        print(
            "Tempo do pipeline: "
            f"{result.duration_seconds:.4f}s"
        )
        print(
            "Tempo total: "
            f"{orchestration_duration:.4f}s"
        )
        print(
            "=========================================="
        )
        print()