from typing import Any

from app.orchestrator.decision import (
    OrchestrationDecision,
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

    O Decision Engine:
    - normaliza os dados recebidos;
    - avalia as regras por prioridade;
    - seleciona a primeira regra compatível;
    - gera uma decisão explicável;
    - informa agentes, confiança e sinais.
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
            context
        )

        decision = rule.build_decision(
            context
        )

        self._print_decision(
            context=context,
            decision=decision,
            rule=rule,
        )

        return decision

    def list_rules(self) -> list[dict]:
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
            f"Prioridade da regra: "
            f"{rule.priority}"
        )
        print(
            f"Decisão: "
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
            f"Dias em atraso: "
            f"{context.dias_atraso}"
        )

        if decision.selected_agents:
            print(
                "Agentes definidos: "
                + " → ".join(
                    decision.selected_agents
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