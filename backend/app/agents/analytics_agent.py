from typing import Any

from app.agents.event_bus import event_bus
from app.database.database import SessionLocal
from app.services.knowledge_service import KnowledgeService


class AnalyticsAgent:
    """
    Agente responsável por analisar eventos do Auneron AI,
    classificar clientes e registrar conhecimentos no Brain.
    """

    @staticmethod
    def on_cliente_criado(
        payload: dict[str, Any],
    ) -> None:
        cliente = str(
            payload.get(
                "cliente",
                "Cliente não informado",
            )
        )

        valor = float(
            payload.get("valor", 0) or 0
        )

        status = str(
            payload.get(
                "status",
                "não informado",
            )
        ).lower()

        vencimento = str(
            payload.get(
                "vencimento",
                "não informado",
            )
        )

        account_id_raw = payload.get("id")

        account_id = (
            int(account_id_raw)
            if account_id_raw is not None
            else None
        )

        categoria = (
            AnalyticsAgent.classificar_cliente(
                valor=valor,
                status=status,
            )
        )

        prioridade = (
            AnalyticsAgent.definir_prioridade(
                valor=valor,
                status=status,
            )
        )

        print()
        print(
            "========== ANALYTICS AGENT =========="
        )
        print(
            "Evento recebido: cliente_criado"
        )
        print(f"Cliente: {cliente}")
        print(
            f"Valor analisado: R$ {valor:,.2f}"
        )
        print(f"Status: {status}")
        print(f"Vencimento: {vencimento}")
        print(f"Categoria: {categoria}")
        print(f"Prioridade: {prioridade}")

        db = SessionLocal()

        try:
            AnalyticsAgent.registrar_conhecimentos(
                db=db,
                cliente=cliente,
                valor=valor,
                status=status,
                vencimento=vencimento,
                categoria=categoria,
                prioridade=prioridade,
                account_id=account_id,
            )

            print(
                "Conhecimentos analíticos "
                "registrados no Brain."
            )
        except Exception as error:
            db.rollback()

            print(
                "Erro ao registrar conhecimentos "
                f"do AnalyticsAgent: {error}"
            )
        finally:
            db.close()

        print(
            "Análise concluída com sucesso."
        )
        print(
            "====================================="
        )
        print()

    @staticmethod
    def registrar_conhecimentos(
        *,
        db: Any,
        cliente: str,
        valor: float,
        status: str,
        vencimento: str,
        categoria: str,
        prioridade: str,
        account_id: int | None,
    ) -> None:
        """
        Registra no Brain apenas conhecimentos
        relevantes produzidos pelo AnalyticsAgent.
        """

        if categoria == "Estratégico":
            KnowledgeService.create(
                db=db,
                agent_name="AnalyticsAgent",
                event_name="cliente_criado",
                knowledge_type="insight",
                severity="high",
                title="Cliente estratégico",
                message=(
                    f"O cliente {cliente} foi "
                    f"classificado como estratégico, "
                    f"com valor de R$ {valor:,.2f}. "
                    "Recomenda-se acompanhamento "
                    "prioritário e monitoramento "
                    "da participação na carteira."
                ),
                account_id=account_id,
            )

            print(
                "✔ Insight estratégico salvo "
                "no Brain."
            )

        elif categoria == "Premium":
            KnowledgeService.create(
                db=db,
                agent_name="AnalyticsAgent",
                event_name="cliente_criado",
                knowledge_type="insight",
                severity="medium",
                title="Cliente premium",
                message=(
                    f"O cliente {cliente} foi "
                    f"classificado como premium, "
                    f"com valor de R$ {valor:,.2f}. "
                    "O cliente está acima da faixa "
                    "padrão da carteira."
                ),
                account_id=account_id,
            )

            print(
                "✔ Insight premium salvo "
                "no Brain."
            )

        if status == "atrasado":
            severidade = (
                "critical"
                if valor >= 10000
                else "high"
            )

            KnowledgeService.create(
                db=db,
                agent_name="AnalyticsAgent",
                event_name="cliente_criado",
                knowledge_type="risk",
                severity=severidade,
                title="Risco analítico identificado",
                message=(
                    f"O cliente {cliente} possui "
                    f"status atrasado, vencimento em "
                    f"{vencimento} e prioridade "
                    f"{prioridade.lower()}. "
                    "A situação aumenta o risco "
                    "financeiro da carteira."
                ),
                account_id=account_id,
            )

            print(
                "✔ Risco analítico salvo "
                "no Brain."
            )

        if (
            valor >= 20000
            and status == "atrasado"
        ):
            KnowledgeService.create(
                db=db,
                agent_name="AnalyticsAgent",
                event_name="cliente_criado",
                knowledge_type="recommendation",
                severity="critical",
                title=(
                    "Acompanhamento imediato "
                    "recomendado"
                ),
                message=(
                    f"O cliente {cliente} combina "
                    f"alto valor financeiro "
                    f"(R$ {valor:,.2f}) com status "
                    "atrasado. Recomenda-se contato "
                    "imediato, análise de risco e "
                    "plano de cobrança prioritário."
                ),
                account_id=account_id,
            )

            print(
                "✔ Recomendação crítica salva "
                "no Brain."
            )

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
        if (
            status == "atrasado"
            and valor >= 10000
        ):
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