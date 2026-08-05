from decimal import Decimal
from typing import Any

from app.core.money import ZERO_MONEY
from app.core.money import to_money
from app.database.database import SessionLocal
from app.orchestrator import registry
from app.services.knowledge_service import KnowledgeService


class AnalyticsAgent:
    """
    Analisa eventos, classifica clientes
    e registra conhecimentos estratégicos no Brain.
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

        valor = to_money(
            payload.get("valor"),
            default=ZERO_MONEY,
        )

        status = str(
            payload.get(
                "status",
                "não informado",
            )
        ).strip().lower()

        vencimento = str(
            payload.get(
                "vencimento",
                "não informado",
            )
        )

        account_id = (
            AnalyticsAgent.converter_account_id(
                payload.get("id")
            )
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
        valor: Decimal,
        status: str,
        vencimento: str,
        categoria: str,
        prioridade: str,
        account_id: int | None,
    ) -> None:
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
                    "classificado como estratégico, "
                    f"com valor de R$ {valor:,.2f}. "
                    "Recomenda-se acompanhamento "
                    "prioritário da participação "
                    "na carteira."
                ),
                account_id=account_id,
            )

            print(
                "Insight estratégico salvo."
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
                    "classificado como premium, "
                    f"com valor de R$ {valor:,.2f}."
                ),
                account_id=account_id,
            )

            print(
                "Insight premium salvo."
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
                    f"status atrasado, vencimento "
                    f"em {vencimento} e prioridade "
                    f"{prioridade.lower()}."
                ),
                account_id=account_id,
            )

            print(
                "Risco analítico salvo."
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
                    "imediato e plano de cobrança."
                ),
                account_id=account_id,
            )

            print(
                "Recomendação crítica salva."
            )

    @staticmethod
    def classificar_cliente(
        valor: Decimal,
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
        valor: Decimal,
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

    @staticmethod
    def converter_account_id(
        account_id: Any,
    ) -> int | None:
        if account_id is None:
            return None

        try:
            return int(account_id)

        except (TypeError, ValueError):
            return None


registry.register(
    "cliente_criado",
    AnalyticsAgent.on_cliente_criado,
    name="AnalyticsAgent",
    priority=30,
)