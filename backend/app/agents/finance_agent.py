from typing import Any

from app.agents.event_bus import event_bus
from app.database.database import SessionLocal
from app.services.knowledge_service import KnowledgeService


class FinanceAgent:
    """
    Agente Financeiro do Auneron AI.

    Responsável por analisar eventos financeiros
    e registrar conhecimento no Brain.
    """

    @staticmethod
    def on_cliente_criado(payload: dict[str, Any]) -> None:

        cliente = payload.get("cliente", "Cliente não informado")
        valor = float(payload.get("valor", 0) or 0)
        status = str(payload.get("status", "não informado")).lower()
        vencimento = payload.get("vencimento", "")
        account_id = payload.get("id")

        print()
        print("========== FINANCE AGENT ==========")
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
                        f"O cliente {cliente} foi cadastrado "
                        f"com contrato de R$ {valor:,.2f}. "
                        "Recomenda-se acompanhamento prioritário."
                    ),
                    account_id=account_id,
                )

                print("✔ Insight salvo no Brain.")

            if status == "atrasado":

                KnowledgeService.create(
                    db=db,
                    agent_name="FinanceAgent",
                    event_name="cliente_criado",
                    knowledge_type="alert",
                    severity="critical",
                    title="Cliente em atraso",
                    message=(
                        f"{cliente} foi cadastrado com "
                        "status financeiro atrasado."
                    ),
                    account_id=account_id,
                )

                print("✔ Alerta salvo no Brain.")

        finally:

            db.close()

        print("===================================")
        print()


event_bus.subscribe(
    "cliente_criado",
    FinanceAgent.on_cliente_criado,
)