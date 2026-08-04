from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

AgentHandler = Callable[
    [dict[str, Any]],
    None,
]


@dataclass(frozen=True)
class RegisteredAgent:
    name: str
    handler: AgentHandler
    priority: int


class AgentRegistry:
    """
    Registro central dos agentes.

    Cada agente é associado a um evento e possui
    uma prioridade de execução.
    """

    def __init__(self) -> None:
        self._agents: dict[
            str,
            list[RegisteredAgent],
        ] = defaultdict(list)

    def register(
        self,
        event_name: str,
        handler: AgentHandler,
        *,
        name: str | None = None,
        priority: int = 100,
    ) -> None:
        agent_name = (
            name
            or handler.__qualname__.split(".")[0]
        )

        agents = self._agents[event_name]

        duplicate = any(
            agent.name == agent_name
            and agent.handler == handler
            for agent in agents
        )

        if duplicate:
            return

        agents.append(
            RegisteredAgent(
                name=agent_name,
                handler=handler,
                priority=priority,
            )
        )

        agents.sort(
            key=lambda agent: (
                agent.priority,
                agent.name,
            )
        )

    def unregister(
        self,
        event_name: str,
        agent_name: str,
    ) -> bool:
        agents = self._agents.get(
            event_name,
            [],
        )

        initial_size = len(agents)

        self._agents[event_name] = [
            agent
            for agent in agents
            if agent.name != agent_name
        ]

        return (
            len(self._agents[event_name])
            < initial_size
        )

    def get_agents(
        self,
        event_name: str,
    ) -> list[RegisteredAgent]:
        return list(
            self._agents.get(
                event_name,
                [],
            )
        )

    def get_selected_agents(
        self,
        event_name: str,
        selected_names: tuple[str, ...],
    ) -> list[RegisteredAgent]:
        selected_set = set(
            selected_names
        )

        return [
            agent
            for agent in self.get_agents(
                event_name,
            )
            if agent.name in selected_set
        ]

    def count_agents(
        self,
        event_name: str,
    ) -> int:
        return len(
            self._agents.get(
                event_name,
                [],
            )
        )

    def list_events(self) -> list[str]:
        return sorted(
            self._agents.keys()
        )

    def list_registry(self) -> dict:
        return {
            event_name: [
                {
                    "name": agent.name,
                    "priority": agent.priority,
                }
                for agent in agents
            ]
            for event_name, agents
            in self._agents.items()
        }

    def clear(self) -> None:
        self._agents.clear()


registry = AgentRegistry()