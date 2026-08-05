from typing import Any

from app.core.money import ZERO_MONEY
from app.core.money import to_money
from app.database.database import SessionLocal
from app.orchestrator import registry
from app.services.knowledge_service import KnowledgeService


class NotificationAgent:
    """
    Gera recomendações operacionais
    e notificações para o Brain.
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

        account_id = (
            NotificationAgent.converter_account_id(
                payload.get("id")
            )
        )

        print()
        print(
            "========== NOTIFICATION AGENT =========="
        )
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
                        "Preparar e enviar uma cobrança "
                        f"para o cliente {cliente}."
                    ),
                    account_id=account_id,
                )

                print(
                    "Notificação de cobrança criada."
                )

            elif valor >= 30000:
                KnowledgeService.create(
                    db=db,
                    agent_name="NotificationAgent",
                    event_name="cliente_criado",
                    knowledge_type="notification",
                    severity="high",
                    title="Contato prioritário",
                    message=(
                        "Agendar um contato comercial "
                        f"prioritário com {cliente}."
                    ),
                    account_id=account_id,
                )

                print(
                    "Contato prioritário criado."
                )

            else:
                print(
                    "Nenhuma notificação necessária."
                )

        except Exception as error:
            db.rollback()

            print(
                "Erro ao registrar notificação: "
                f"{error}"
            )

        finally:
            db.close()

        print(
            "NotificationAgent finalizado."
        )
        print(
            "========================================"
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
    NotificationAgent.on_cliente_criado,
    name="NotificationAgent",
    priority=40,
)