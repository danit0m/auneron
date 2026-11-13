import logging
from typing import Any

from app.orchestrator.decision import (
    OrchestrationDecision,
)
from app.orchestrator.decision_store import (
    decision_store,
)
from app.orchestrator.rules import (
    DEFAULT_RULES,
    DecisionContext,
    DecisionRule,
    build_context,
)


_log = logging.getLogger(__name__)


def _emit(message: str = "") -> None:
    """Registra a saída de observação via logging (ASCII-safe, sem exceção de codificação)."""

    _log.info("%s", message)


class DecisionEngine:
    """
    Núcleo central de decisão do Auneron AI.

    Responsabilidades:
    - normalizar o evento e o payload;
    - avaliar as regras por prioridade;
    - produzir uma decisão explicável;
    - armazenar a decisão no DecisionStore.
    """

    def __init__(
        self,
        rules: tuple[
            DecisionRule,
            ...,
        ] | None = None,
    ) -> None:
        self._rules = (
            rules
            if rules is not None
            else DEFAULT_RULES
        )

    def decide(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
    ) -> OrchestrationDecision:
        context = build_context(
            event_name=event_name,
            payload=payload,
        )

        rule = self._find_matching_rule(
            context,
        )

        decision = rule.build_decision(
            context,
        )

        stored_decision = decision_store.save(
            event_name=event_name,
            decision=decision,
            cliente=context.cliente,
            valor=context.valor,
            status=context.status,
            vencimento=context.vencimento,
            dias_atraso=context.dias_atraso,
        )

        self._print_decision(
            context=context,
            decision=decision,
            rule=rule,
            decision_id=(
                stored_decision.decision_id
            ),
        )

        return decision

    def list_rules(
        self,
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": rule.name,
                "priority": rule.priority,
                "class_name": (
                    rule.__class__.__name__
                ),
            }
            for rule in self._rules
        ]

    def _find_matching_rule(
        self,
        context: DecisionContext,
    ) -> DecisionRule:
        for rule in self._rules:
            if rule.matches(context):
                return rule

        raise RuntimeError(
            "Nenhuma regra de decisão "
            "correspondeu ao contexto."
        )

    @staticmethod
    def _print_decision(
        *,
        context: DecisionContext,
        decision: OrchestrationDecision,
        rule: DecisionRule,
        decision_id: str,
    ) -> None:
        _emit()
        _emit(
            "------------------------------------------"
        )
        _emit("DECISION ENGINE")
        _emit(
            "------------------------------------------"
        )
        _emit(
            f"Regra aplicada: {rule.name}"
        )
        _emit(
            "Prioridade da regra: "
            f"{rule.priority}"
        )
        _emit(
            "Decisão: "
            f"{decision.decision_name}"
        )
        _emit(
            "Confiança: "
            f"{decision.confidence * 100:.1f}%"
        )
        _emit(
            f"Cliente: {context.cliente}"
        )
        _emit(
            f"Valor: R$ {context.valor:,.2f}"
        )
        _emit(
            f"Status: {context.status}"
        )
        _emit(
            "Dias em atraso: "
            f"{context.dias_atraso}"
        )
        _emit(
            f"Decision ID: {decision_id}"
        )

        if decision.selected_agents:
            _emit(
                "Agentes definidos: "
                + " -> ".join(
                    decision.selected_agents,
                )
            )
        else:
            _emit(
                "Agentes definidos: nenhum"
            )

        _emit("Sinais identificados:")

        for signal in decision.signals:
            _emit(
                f"- {signal.name}: "
                f"{signal.value}"
            )

        _emit(
            "------------------------------------------"
        )
        _emit()


decision_engine = DecisionEngine()