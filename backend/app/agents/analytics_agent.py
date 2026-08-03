from typing import Any

from app.agents.event_bus import event_bus


class AnalyticsAgent:
    """
    Agente responsável por analisar eventos do Auneron AI
    e gerar classificações e insights operacionais.
    """

    @staticmethod
    def on_cliente_criado(payload: dict[str, Any]) -> None:
        cliente = str(
            payload.get("cliente", "Cliente não informado")
        )

        valor = float(
            payload.get("valor", 0) or 0
        )

        status = str(
            payload.get("status", "não informado")
        ).lower()

        vencimento = str(
            payload.get("vencimento", "não informado")
        )

        categoria = AnalyticsAgent.classificar_cliente(
            valor=valor,
            status=status,
        )

        prioridade = AnalyticsAgent.definir_prioridade(
            valor=valor,
            status=status,
        )

        print()
        print("========== ANALYTICS AGENT ==========")
        print("Evento recebido: cliente_criado")
        print(f"Cliente: {cliente}")
        print(f"Valor analisado: R$ {valor:,.2f}")
        print(f"Status: {status}")
        print(f"Vencimento: {vencimento}")
        print(f"Categoria: {categoria}")
        print(f"Prioridade: {prioridade}")

        if valor >= 10000:
            print(
                "Insight: o cliente possui valor acima "
                "da faixa padrão da carteira."
            )

        if status == "atrasado":
            print(
                "Risco identificado: cliente cadastrado "
                "com situação financeira atrasada."
            )

        if valor >= 20000 and status == "atrasado":
            print(
                "Recomendação: acompanhamento imediato "
                "e análise de risco de inadimplência."
            )

        print("Análise concluída com sucesso.")
        print("=====================================")
        print()

    @staticmethod
    def classificar_cliente(
        valor: float,
        status: str,
    ) -> str:
        if valor >= 30000:
            return "Estratégico"

        if valor >= 10000:
            return "Premium"

        if status == "atrasado":
            return "Risco financeiro"

        return "Padrão"

    @staticmethod
    def definir_prioridade(
        valor: float,
        status: str,
    ) -> str:
        if status == "atrasado" and valor >= 10000:
            return "Crítica"

        if status == "atrasado":
            return "Alta"

        if valor >= 30000:
            return "Alta"

        if valor >= 10000:
            return "Média"

        return "Normal"


event_bus.subscribe(
    "cliente_criado",
    AnalyticsAgent.on_cliente_criado,
)
