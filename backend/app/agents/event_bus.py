from collections.abc import Callable
from typing import Any

from app.orchestrator import AIOrchestrator

LegacyHandler = Callable[
    [dict[str, Any]],
    None,
]


class EventBus:
    """
    Porta de entrada dos eventos do Auneron AI.

    Na Sprint 6, a execução dos agentes passou
    a ser coordenada pelo AIOrchestrator.

    O método subscribe permanece temporariamente
    para compatibilidade com partes antigas do projeto,
    mas os novos agentes devem usar o AgentRegistry.
    """

    def __init__(self) -> None:
        self._legacy_subscribers: dict[
            str,
            list[LegacyHandler],
        ] = {}

    def subscribe(
        self,
        event_name: str,
        callback: LegacyHandler,
    ) -> None:
        """
        Mantido temporariamente por compatibilidade.

        Os agentes da arquitetura atual devem utilizar:

        registry.register(event_name, handler)
        """

        callbacks = (
            self._legacy_subscribers.setdefault(
                event_name,
                [],
            )
        )

        if callback not in callbacks:
            callbacks.append(callback)

    def publish(
        self,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        """
        Publica o evento para o AIOrchestrator.

        Os callbacks antigos não são executados aqui,
        evitando que agentes registrados nos dois
        mecanismos sejam processados em duplicidade.
        """

        AIOrchestrator.execute(
            event_name=event_name,
            payload=payload,
        )


event_bus = EventBus()