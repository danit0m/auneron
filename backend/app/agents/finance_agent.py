from typing import Any

from app.core.money import ZERO_MONEY
from app.core.money import to_money
from app.database.database import SessionLocal
from app.orchestrator import registry
from app.services.knowledge_service import KnowledgeService


class FinanceAgent:
    """
    Agente responsável por analisar informações
    financeiras e registrar conhecimentos no Brain.
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
            FinanceAgent.converter_account_id(
                payload.get("id")
            )
        )

        print()
        print(
            "========== FINANCE AGENT =========="
        )
        print(
            "Evento recebido: cliente_criado"
        )
        print(f"Cliente: {cliente}")
        print(f"Valor: R$ {valor:,.2f}")
        print(f"Status: {status}")
        print(f"Vencimento: {vencimento}")

        db = SessionLocal()

        try:
            if valor >= 10000:
                KnowledgeService.create(
                    db=db,
                    agent_name="FinanceAgent",
                    event_name="cliente_criado",
                    knowledge_type="insight",
                    severity="high",
                    title="Cliente de alto valor",
                    message=(
                        f"O cliente {cliente} foi "
                        f"cadastrado com valor de "
                        f"R$ {valor:,.2f}. "
                        "Recomenda-se acompanhamento "
                        "financeiro prioritário."
                    ),
                    account_id=account_id,
                )

                print(
                    "Insight salvo no Brain."
                )

            if status == "atrasado":
                KnowledgeService.create(
                    db=db,
                    agent_name="FinanceAgent",
                    event_name="cliente_criado",
                    knowledge_type="alert",
                    severity="critical",
                    title="Cliente em atraso",
                    message=(
                        f"O cliente {cliente} foi "
                        "cadastrado com status "
                        "financeiro atrasado. "
                        "Recomenda-se iniciar uma "
                        "ação de cobrança."
                    ),
                    account_id=account_id,
                )

                print(
                    "Alerta salvo no Brain."
                )

        except Exception as error:
            db.rollback()

            print(
                "Erro ao registrar conhecimentos "
                f"do FinanceAgent: {error}"
            )

        finally:
            db.close()

        print(
            "==================================="
        )
        print()

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
    FinanceAgent.on_cliente_criado,
    name="FinanceAgent",
    priority=10,
)