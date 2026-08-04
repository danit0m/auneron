from datetime import datetime
from time import perf_counter
from typing import Any

from app.orchestrator.decision import (
    OrchestrationDecision,
)
from app.orchestrator.decision_engine import (
    decision_engine,
)
from app.orchestrator.pipeline import (
    ExecutionPipeline,
    PipelineResult,
)
from app.orchestrator.registry import (
    registry,
)


class AIOrchestrator:
    """
    Coordena o ciclo completo do Auneron AI.

    Fluxo:
    - recebe um evento;
    - solicita uma decisão ao Decision Engine;
    - seleciona os agentes registrados;
    - delega a execução ao Pipeline;
    - apresenta o resumo da operação.
    """

    @staticmethod
    def execute(
        event_name: str,
        payload: dict[str, Any],
    ) -> PipelineResult | None:
        orchestration_start = perf_counter()

        decision = decision_engine.decide(
            event_name=event_name,
            payload=payload,
        )

        available_agents = registry.get_agents(
            event_name,
        )

        selected_agents = (
            registry.get_selected_agents(
                event_name,
                decision.selected_agents,
            )
        )

        AIOrchestrator._print_header(
            event_name=event_name,
            decision=decision,
            available_count=len(
                available_agents,
            ),
            selected_count=len(
                selected_agents,
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
                    decision.decision_name
                ),
                agents=selected_agents,
                payload=payload,
            )
        )

        total_duration = (
            perf_counter()
            - orchestration_start
        )

        ignored_agents = (
            len(available_agents)
            - len(selected_agents)
        )

        AIOrchestrator._print_summary(
            result=pipeline_result,
            ignored_agents=ignored_agents,
            total_duration=total_duration,
        )

        return pipeline_result

    @staticmethod
    def _print_header(
        *,
        event_name: str,
        decision: OrchestrationDecision,
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
            f"Decisão: {decision.decision_name}"
        )
        print(
            "Confiança: "
            f"{decision.confidence * 100:.1f}%"
        )
        print(
            f"Motivo: {decision.reason}"
        )
        print(
            "Agentes disponíveis: "
            f"{available_count}"
        )
        print(
            "Agentes selecionados: "
            f"{selected_count}"
        )

        if decision.selected_agents:
            print(
                "Plano solicitado: "
                + " → ".join(
                    decision.selected_agents
                )
            )

    @staticmethod
    def _print_summary(
        *,
        result: PipelineResult,
        ignored_agents: int,
        total_duration: float,
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
            f"Falhas: {result.failed_agents}"
        )
        print(
            "Tempo do pipeline: "
            f"{result.duration_seconds:.4f}s"
        )
        print(
            "Tempo total: "
            f"{total_duration:.4f}s"
        )
        print(
            "=========================================="
        )
        print()
