import logging
from datetime import datetime
from typing import Any

from app.orchestrator.decision import (
    OrchestrationDecision,
)
from app.orchestrator.decision_engine import (
    decision_engine,
)
from app.orchestrator.pipeline import (
    PipelineResult,
)
from app.orchestrator.registry import (
    registry,
)
from app.orchestrator.safety import (
    LegacyAutonomyExecutionBlockedError,
)


_log = logging.getLogger(__name__)


def _emit(message: str = "") -> None:
    """Registra a saída de observação via logging (ASCII-safe, sem exceção de codificação)."""

    _log.info("%s", message)


class AIOrchestrator:
    """
    Fronteira legada do Auneron AI em quarentena observe-only.

    O modo de produção preserva decisão explicável, DecisionStore e seleção
    consultiva de agentes, mas não executa handlers. A execução legada não
    possui Work/Skill/Approval/RBAC suficiente para produzir efeitos.
    """

    @staticmethod
    def observe(
        event_name: str,
        payload: dict[str, Any],
    ) -> OrchestrationDecision:
        """
        Produz e registra uma decisão sem executar o plano legado.

        Os agentes selecionados são apenas metadado consultivo. Nenhum nome de
        agente, decisão, modelo, memória ou contexto concede autoridade.
        """

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

        _emit("Modo: OBSERVE-ONLY")
        _emit(
            "Execução legada: BLOQUEADA"
        )
        _emit(
            "Agentes selecionados são metadado consultivo; "
            "nenhum handler foi executado."
        )
        _emit(
            "=========================================="
        )
        _emit()

        return decision

    @staticmethod
    def execute(
        event_name: str,
        payload: dict[str, Any],
    ) -> PipelineResult | None:
        """Compatibility symbol for the quarantined legacy execution path."""

        raise LegacyAutonomyExecutionBlockedError(
            "Legacy AIOrchestrator execution is quarantined. "
            "Use AIOrchestrator.observe for observation-only decisions."
        )

    @staticmethod
    def _print_header(
        *,
        event_name: str,
        decision: OrchestrationDecision,
        available_count: int,
        selected_count: int,
    ) -> None:
        _emit()
        _emit(
            "=========================================="
        )
        _emit("AI ORCHESTRATOR")
        _emit(
            "=========================================="
        )
        _emit(f"Evento: {event_name}")
        _emit(
            "Horário: "
            f"{datetime.now().isoformat(timespec='seconds')}"
        )
        _emit(
            f"Decisão: {decision.decision_name}"
        )
        _emit(
            "Confiança: "
            f"{decision.confidence * 100:.1f}%"
        )
        _emit(
            f"Motivo: {decision.reason}"
        )
        _emit(
            "Agentes disponíveis: "
            f"{available_count}"
        )
        _emit(
            "Agentes selecionados: "
            f"{selected_count}"
        )

        if decision.selected_agents:
            _emit(
                "Plano observado: "
                + " -> ".join(
                    decision.selected_agents
                )
            )
