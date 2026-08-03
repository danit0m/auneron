from typing import Any

from app.agents.event_bus import event_bus
from app.database.database import SessionLocal
from app.services.knowledge_service import KnowledgeService


class NotificationAgent:
    """
    Responsável por gerar recomendações
    operacionais para os usuários.
    """

    @staticmethod
    def on_cliente_criado(payload: dict[str, Any]) -> None:

        cliente = payload.get("cliente")
        valor = float(payload.get("valor", 0))
        status = str(payload.get("status", "")).lower()
        vencimento = payload.get("vencimento")
        account_id = payload.get("id")

        print()
        print("========== NOTIFICATION AGENT ==========")
        print(f"Cliente: {cliente}")

        db = SessionLocal()

        try:

            if status == "atrasado":

                KnowledgeService.create(
                    db=db,
                    agent_name="NotificationAgent",
                    event_name="cliente_criado",
                    knowledge_type="notification",
                    severity="critical",
                    title="Enviar cobrança",
                    message=(
                        f"Enviar cobrança imediatamente "
                        f"para {cliente}."
                    ),
                    account_id=account_id,
                )

                print("✔ Cobrança criada.")

            elif valor >= 30000:

                KnowledgeService.create(
                    db=db,
                    agent_name="NotificationAgent",
                    event_name="cliente_criado",
                    knowledge_type="notification",
                    severity="high",
                    title="Contato prioritário",
                    message=(
                        f"Agendar contato comercial "
                        f"com {cliente}."
                    ),
                    account_id=account_id,
                )

                print("✔ Contato prioritário criado.")

        finally:
            db.close()

        print("NotificationAgent finalizado.")
        print("=======================================")
        print()


event_bus.subscribe(
    "cliente_criado",
    NotificationAgent.on_cliente_criado,
)