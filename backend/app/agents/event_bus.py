from collections import defaultdict
from collections.abc import Callable
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[
            str,
            list[Callable[[dict[str, Any]], None]],
        ] = defaultdict(list)

    def subscribe(
        self,
        event_name: str,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        self._subscribers[event_name].append(callback)

    def publish(
        self,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        callbacks = self._subscribers.get(event_name, [])

        for callback in callbacks:
            try:
                callback(payload)
            except Exception as error:
                print(
                    f"[EventBus] Erro no evento "
                    f"'{event_name}': {error}"
                )


event_bus = EventBus()