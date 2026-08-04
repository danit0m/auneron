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
        print()
        print(
            "------------------------------------------"
        )
        print("DECISION ENGINE")
        print(
            "------------------------------------------"
        )
        print(
            f"Regra aplicada: {rule.name}"
        )
        print(
            "Prioridade da regra: "
            f"{rule.priority}"
        )
        print(
            "Decisão: "
            f"{decision.decision_name}"
        )
        print(
            "Confiança: "
            f"{decision.confidence * 100:.1f}%"
        )
        print(
            f"Cliente: {context.cliente}"
        )
        print(
            f"Valor: R$ {context.valor:,.2f}"
        )
        print(
            f"Status: {context.status}"
        )
        print(
            "Dias em atraso: "
            f"{context.dias_atraso}"
        )
        print(
            f"Decision ID: {decision_id}"
        )

        if decision.selected_agents:
            print(
                "Agentes definidos: "
                + " → ".join(
                    decision.selected_agents,
                )
            )
        else:
            print(
                "Agentes definidos: nenhum"
            )

        print("Sinais identificados:")

        for signal in decision.signals:
            print(
                f"- {signal.name}: "
                f"{signal.value}"
            )

        print(
            "------------------------------------------"
        )
        print()


decision_engine = DecisionEngine()