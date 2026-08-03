from collections import defaultdict
from collections.abc import Callable
from typing import Any

AgentHandler = Callable[[dict[str, Any]], None]


class AgentRegistry:
    """
    Registro central dos agentes do Auneron AI.

    Relaciona eventos aos agentes responsáveis
    por processá-los.
    """

    def __init__(self) -> None:
        self._handlers: dict[
            str,
            list[AgentHandler],
        ] = defaultdict(list)

    def register(
        self,
        event_name: str,
        handler: AgentHandler,
    ) -> None:
        """
        Registra um agente para determinado evento.

        Evita que o mesmo handler seja cadastrado
        mais de uma vez durante o reload do Uvicorn.
        """

        handlers = self._handlers[event_name]

        if handler not in handlers:
            handlers.append(handler)

    def unregister(
        self,
        event_name: str,
        handler: AgentHandler,
    ) -> None:
        """
        Remove um agente de determinado evento.
        """

        handlers = self._handlers.get(
            event_name,
            [],
        )

        if handler in handlers:
            handlers.remove(handler)

    def get_handlers(
        self,
        event_name: str,
    ) -> list[AgentHandler]:
        """
        Retorna uma cópia da lista de agentes
        registrados para o evento.
        """

        return list(
            self._handlers.get(
                event_name,
                [],
            )
        )

    def count_handlers(
        self,
        event_name: str,
    ) -> int:
        return len(
            self._handlers.get(
                event_name,
                [],
            )
        )

    def list_events(self) -> list[str]:
        return sorted(self._handlers.keys())

    def clear(self) -> None:
        """
        Limpa todo o registro.

        Útil futuramente para testes automatizados.
        """

        self._handlers.clear()


registry = AgentRegistry()