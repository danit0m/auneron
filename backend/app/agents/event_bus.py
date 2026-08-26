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

    O EventBus preserva a geração de decisões do Orchestrator em modo
    observe-only. A execução de handlers legados permanece em quarentena e
    não é uma fronteira de autoridade.

    O método subscribe permanece temporariamente para compatibilidade com
    partes antigas do projeto. Os callbacks legados não são executados por
    publish().
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

        Os agentes da arquitetura legada continuam registrados no
        AgentRegistry somente como referência de migração e metadado de
        decisão. EventBus.publish não executa esses callbacks.
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
        Publica o evento em modo observe-only.

        A decisão e a seleção de agentes podem ser observadas, mas nenhum
        ExecutionPipeline ou handler legado é executado a partir desta porta.
        """

        AIOrchestrator.observe(
            event_name=event_name,
            payload=payload,
        )


event_bus = EventBus()
