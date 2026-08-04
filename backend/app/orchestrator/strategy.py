from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OrchestrationPlan:
    strategy_name: str
    selected_agents: tuple[str, ...]
    reason: str


class OrchestrationStrategy:
    """
    Constrói um plano de execução com base
    no evento e nos dados recebidos.
    """

    HIGH_VALUE_THRESHOLD = 10000
    STRATEGIC_VALUE_THRESHOLD = 30000

    @classmethod
    def build_plan(
        cls,
        event_name: str,
        payload: dict[str, Any],
    ) -> OrchestrationPlan:
        if event_name != "cliente_criado":
            return OrchestrationPlan(
                strategy_name="EVENTO_SEM_ESTRATEGIA",
                selected_agents=tuple(),
                reason=(
                    "Não existe estratégia configurada "
                    f"para o evento '{event_name}'."
                ),
            )

        valor = cls._to_float(
            payload.get("valor"),
        )

        status = str(
            payload.get("status", ""),
        ).strip().lower()

        if (
            status == "atrasado"
            and valor
            >= cls.STRATEGIC_VALUE_THRESHOLD
        ):
            return OrchestrationPlan(
                strategy_name=(
                    "ALTO_VALOR_EM_ATRASO"
                ),
                selected_agents=(
                    "FinanceAgent",
                    "RiskAgent",
                    "AnalyticsAgent",
                    "NotificationAgent",
                ),
                reason=(
                    "Cliente estratégico, de alto valor "
                    "e com situação financeira atrasada."
                ),
            )

        if status == "atrasado":
            return OrchestrationPlan(
                strategy_name="CLIENTE_EM_ATRASO",
                selected_agents=(
                    "FinanceAgent",
                    "RiskAgent",
                    "AnalyticsAgent",
                    "NotificationAgent",
                ),
                reason=(
                    "Cliente em atraso exige análise "
                    "financeira, risco e ação operacional."
                ),
            )

        if (
            valor
            >= cls.STRATEGIC_VALUE_THRESHOLD
        ):
            return OrchestrationPlan(
                strategy_name="CLIENTE_ESTRATEGICO",
                selected_agents=(
                    "FinanceAgent",
                    "RiskAgent",
                    "AnalyticsAgent",
                    "NotificationAgent",
                ),
                reason=(
                    "Cliente com valor estratégico "
                    "para a carteira."
                ),
            )

        if (
            valor
            >= cls.HIGH_VALUE_THRESHOLD
        ):
            return OrchestrationPlan(
                strategy_name="CLIENTE_PREMIUM",
                selected_agents=(
                    "FinanceAgent",
                    "RiskAgent",
                    "AnalyticsAgent",
                ),
                reason=(
                    "Cliente acima da faixa financeira "
                    "padrão da carteira."
                ),
            )

        if status == "pago":
            return OrchestrationPlan(
                strategy_name=(
                    "CLIENTE_PAGO_PADRAO"
                ),
                selected_agents=(
                    "FinanceAgent",
                ),
                reason=(
                    "Cliente pago e de baixo valor "
                    "não exige processamento adicional."
                ),
            )

        return OrchestrationPlan(
            strategy_name="CLIENTE_PADRAO",
            selected_agents=(
                "FinanceAgent",
                "RiskAgent",
            ),
            reason=(
                "Cliente padrão recebe análise "
                "financeira e avaliação básica de risco."
            ),
        )

    @staticmethod
    def _to_float(
        value: Any,
    ) -> float:
        try:
            return float(
                value or 0
            )

        except (
            TypeError,
            ValueError,
        ):
            return 0.0