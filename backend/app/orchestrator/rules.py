from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.money import money_or_zero
from app.core.money import money_to_json_number
from app.orchestrator.decision import (
    DecisionSignal,
    OrchestrationDecision,
)


@dataclass(frozen=True)
class DecisionContext:
    """
    Contexto normalizado utilizado pelas regras.
    """

    event_name: str
    payload: dict[str, Any]
    cliente: str
    valor: Decimal
    status: str
    vencimento: str
    dias_atraso: int


class DecisionRule(ABC):
    """
    Contrato base de uma regra de decisão.
    """

    name = "BASE_RULE"
    priority = 1000

    @abstractmethod
    def matches(
        self,
        context: DecisionContext,
    ) -> bool:
        """
        Informa se a regra atende ao contexto.
        """

    @abstractmethod
    def build_decision(
        self,
        context: DecisionContext,
    ) -> OrchestrationDecision:
        """
        Constrói a decisão final da regra.
        """


class AccountOverdueDetectionRule(DecisionRule):
    """
    Regra dedicada para o evento "conta_vencida", produzido pela
    varredura agendada de detecção real de vencimento (25Q.0-light).
    Prioridade 0: precisa ser avaliada antes de UnsupportedEventRule
    (prioridade 1), que intercepta qualquer evento diferente de
    "cliente_criado".
    """

    name = "CONTA_VENCIDA_DETECTADA"
    priority = 0

    def matches(
        self,
        context: DecisionContext,
    ) -> bool:
        return context.event_name == "conta_vencida"

    def build_decision(
        self,
        context: DecisionContext,
    ) -> OrchestrationDecision:
        return OrchestrationDecision(
            decision_name=self.name,
            selected_agents=(
                "OverdueDetectionAgent",
            ),
            reason=(
                "Conta identificada como vencida pela varredura "
                "agendada, ainda sem decisão humana registrada."
            ),
            confidence=1.0,
            signals=(
                DecisionSignal(
                    name="event_name",
                    value=context.event_name,
                    description=(
                        "Evento de detecção real de vencimento."
                    ),
                ),
                DecisionSignal(
                    name="valor",
                    value=money_to_json_number(
                        context.valor
                    ),
                    description=(
                        "Valor financeiro da conta vencida."
                    ),
                ),
            ),
        )


class UnsupportedEventRule(DecisionRule):
    name = "EVENTO_NAO_SUPORTADO"
    priority = 1

    def matches(
        self,
        context: DecisionContext,
    ) -> bool:
        return (
            context.event_name
            != "cliente_criado"
        )

    def build_decision(
        self,
        context: DecisionContext,
    ) -> OrchestrationDecision:
        return OrchestrationDecision(
            decision_name=self.name,
            selected_agents=tuple(),
            reason=(
                "Não existe uma regra de decisão "
                f"configurada para o evento "
                f"'{context.event_name}'."
            ),
            confidence=1.0,
            signals=(
                DecisionSignal(
                    name="event_name",
                    value=context.event_name,
                    description=(
                        "Evento recebido pelo "
                        "Decision Engine."
                    ),
                ),
            ),
        )


class CriticalOverdueRule(DecisionRule):
    name = "RISCO_CRITICO"
    priority = 10

    def matches(
        self,
        context: DecisionContext,
    ) -> bool:
        return (
            context.status == "atrasado"
            and context.valor >= 50000
            and context.dias_atraso >= 7
        )

    def build_decision(
        self,
        context: DecisionContext,
    ) -> OrchestrationDecision:
        return OrchestrationDecision(
            decision_name=self.name,
            selected_agents=(
                "FinanceAgent",
                "RiskAgent",
                "AnalyticsAgent",
                "NotificationAgent",
            ),
            reason=(
                "Cliente de alto valor, em atraso "
                "há pelo menos sete dias, exige "
                "tratamento financeiro imediato."
            ),
            confidence=0.99,
            signals=(
                DecisionSignal(
                    name="valor_alto",
                    value=money_to_json_number(
                        context.valor
                    ),
                    description=(
                        "Valor financeiro igual ou "
                        "superior a R$ 50.000."
                    ),
                ),
                DecisionSignal(
                    name="status_atrasado",
                    value=context.status,
                    description=(
                        "Cliente possui situação "
                        "financeira atrasada."
                    ),
                ),
                DecisionSignal(
                    name="dias_atraso",
                    value=context.dias_atraso,
                    description=(
                        "Atraso igual ou superior "
                        "a sete dias."
                    ),
                ),
            ),
        )


class StrategicOverdueRule(DecisionRule):
    name = "ALTO_VALOR_EM_ATRASO"
    priority = 20

    def matches(
        self,
        context: DecisionContext,
    ) -> bool:
        return (
            context.status == "atrasado"
            and context.valor >= 30000
        )

    def build_decision(
        self,
        context: DecisionContext,
    ) -> OrchestrationDecision:
        return OrchestrationDecision(
            decision_name=self.name,
            selected_agents=(
                "FinanceAgent",
                "RiskAgent",
                "AnalyticsAgent",
                "NotificationAgent",
            ),
            reason=(
                "Cliente estratégico com valor "
                "elevado e situação financeira "
                "atrasada."
            ),
            confidence=0.97,
            signals=(
                DecisionSignal(
                    name="cliente_estrategico",
                    value=money_to_json_number(
                        context.valor
                    ),
                    description=(
                        "Valor financeiro igual ou "
                        "superior a R$ 30.000."
                    ),
                ),
                DecisionSignal(
                    name="status_atrasado",
                    value=context.status,
                    description=(
                        "O pagamento está atrasado."
                    ),
                ),
                DecisionSignal(
                    name="dias_atraso",
                    value=context.dias_atraso,
                    description=(
                        "Quantidade de dias calculada "
                        "desde o vencimento."
                    ),
                ),
            ),
        )


class OverdueClientRule(DecisionRule):
    name = "CLIENTE_EM_ATRASO"
    priority = 30

    def matches(
        self,
        context: DecisionContext,
    ) -> bool:
        return context.status == "atrasado"

    def build_decision(
        self,
        context: DecisionContext,
    ) -> OrchestrationDecision:
        return OrchestrationDecision(
            decision_name=self.name,
            selected_agents=(
                "FinanceAgent",
                "RiskAgent",
                "AnalyticsAgent",
                "NotificationAgent",
            ),
            reason=(
                "Clientes em atraso necessitam "
                "avaliação de risco e preparação "
                "de uma ação de cobrança."
            ),
            confidence=0.95,
            signals=(
                DecisionSignal(
                    name="status_atrasado",
                    value=context.status,
                    description=(
                        "Situação financeira exige "
                        "acompanhamento."
                    ),
                ),
                DecisionSignal(
                    name="valor",
                    value=money_to_json_number(
                        context.valor
                    ),
                    description=(
                        "Valor financeiro analisado."
                    ),
                ),
                DecisionSignal(
                    name="dias_atraso",
                    value=context.dias_atraso,
                    description=(
                        "Quantidade atual de dias "
                        "em atraso."
                    ),
                ),
            ),
        )


class StrategicClientRule(DecisionRule):
    name = "CLIENTE_ESTRATEGICO"
    priority = 40

    def matches(
        self,
        context: DecisionContext,
    ) -> bool:
        return context.valor >= 30000

    def build_decision(
        self,
        context: DecisionContext,
    ) -> OrchestrationDecision:
        return OrchestrationDecision(
            decision_name=self.name,
            selected_agents=(
                "FinanceAgent",
                "RiskAgent",
                "AnalyticsAgent",
                "NotificationAgent",
            ),
            reason=(
                "Cliente possui valor estratégico "
                "para a carteira e merece análise "
                "financeira e acompanhamento comercial."
            ),
            confidence=0.93,
            signals=(
                DecisionSignal(
                    name="cliente_estrategico",
                    value=money_to_json_number(
                        context.valor
                    ),
                    description=(
                        "Valor igual ou superior "
                        "a R$ 30.000."
                    ),
                ),
                DecisionSignal(
                    name="status",
                    value=context.status,
                    description=(
                        "Situação financeira atual "
                        "do cliente."
                    ),
                ),
            ),
        )


class PremiumClientRule(DecisionRule):
    name = "CLIENTE_PREMIUM"
    priority = 50

    def matches(
        self,
        context: DecisionContext,
    ) -> bool:
        return context.valor >= 10000

    def build_decision(
        self,
        context: DecisionContext,
    ) -> OrchestrationDecision:
        return OrchestrationDecision(
            decision_name=self.name,
            selected_agents=(
                "FinanceAgent",
                "RiskAgent",
                "AnalyticsAgent",
            ),
            reason=(
                "Cliente possui valor acima da faixa "
                "padrão e deve receber análise "
                "financeira, analítica e de risco."
            ),
            confidence=0.90,
            signals=(
                DecisionSignal(
                    name="cliente_premium",
                    value=money_to_json_number(
                        context.valor
                    ),
                    description=(
                        "Valor igual ou superior "
                        "a R$ 10.000."
                    ),
                ),
            ),
        )


class StandardPaidClientRule(DecisionRule):
    name = "CLIENTE_PAGO_PADRAO"
    priority = 60

    def matches(
        self,
        context: DecisionContext,
    ) -> bool:
        return context.status == "pago"

    def build_decision(
        self,
        context: DecisionContext,
    ) -> OrchestrationDecision:
        return OrchestrationDecision(
            decision_name=self.name,
            selected_agents=(
                "FinanceAgent",
            ),
            reason=(
                "Cliente pago e de baixo valor "
                "não exige processamento adicional."
            ),
            confidence=0.96,
            signals=(
                DecisionSignal(
                    name="status_pago",
                    value=context.status,
                    description=(
                        "Cliente encontra-se em dia."
                    ),
                ),
                DecisionSignal(
                    name="valor",
                    value=money_to_json_number(
                        context.valor
                    ),
                    description=(
                        "Valor abaixo da faixa premium."
                    ),
                ),
            ),
        )


class StandardClientRule(DecisionRule):
    name = "CLIENTE_PADRAO"
    priority = 999

    def matches(
        self,
        context: DecisionContext,
    ) -> bool:
        return True

    def build_decision(
        self,
        context: DecisionContext,
    ) -> OrchestrationDecision:
        return OrchestrationDecision(
            decision_name=self.name,
            selected_agents=(
                "FinanceAgent",
                "RiskAgent",
            ),
            reason=(
                "Cliente padrão recebe análise "
                "financeira e avaliação básica "
                "de risco."
            ),
            confidence=0.85,
            signals=(
                DecisionSignal(
                    name="valor",
                    value=money_to_json_number(
                        context.valor
                    ),
                    description=(
                        "Valor dentro da faixa padrão."
                    ),
                ),
                DecisionSignal(
                    name="status",
                    value=context.status,
                    description=(
                        "Status financeiro atual."
                    ),
                ),
            ),
        )


def build_context(
    *,
    event_name: str,
    payload: dict[str, Any],
) -> DecisionContext:
    cliente = str(
        payload.get(
            "cliente",
            "Cliente não informado",
        )
    )

    valor = _to_money(
        payload.get("valor")
    )

    status = str(
        payload.get("status", "")
    ).strip().lower()

    vencimento = str(
        payload.get("vencimento", "")
    ).strip()

    dias_atraso = _calculate_overdue_days(
        status=status,
        vencimento=vencimento,
    )

    return DecisionContext(
        event_name=event_name,
        payload=payload,
        cliente=cliente,
        valor=valor,
        status=status,
        vencimento=vencimento,
        dias_atraso=dias_atraso,
    )


def _to_money(
    value: Any,
) -> Decimal:
    return money_or_zero(value)


def _calculate_overdue_days(
    *,
    status: str,
    vencimento: str,
) -> int:
    if (
        status != "atrasado"
        or not vencimento
    ):
        return 0

    try:
        due_date = datetime.strptime(
            vencimento,
            "%Y-%m-%d",
        ).date()

    except ValueError:
        return 0

    return max(
        (
            date.today()
            - due_date
        ).days,
        0,
    )


DEFAULT_RULES: tuple[
    DecisionRule,
    ...,
] = tuple(
    sorted(
        (
            UnsupportedEventRule(),
            AccountOverdueDetectionRule(),
            CriticalOverdueRule(),
            StrategicOverdueRule(),
            OverdueClientRule(),
            StrategicClientRule(),
            PremiumClientRule(),
            StandardPaidClientRule(),
            StandardClientRule(),
        ),
        key=lambda rule: rule.priority,
    )
)