from dataclasses import dataclass
from typing import Any

from app.orchestrator.registry import (
    RegisteredAgent,
)
from app.orchestrator.safety import (
    LegacyAutonomyExecutionBlockedError,
)


@dataclass(frozen=True)
class AgentExecutionResult:
    agent_name: str
    priority: int
    success: bool
    duration_seconds: float
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    event_name: str
    strategy_name: str
    selected_agents: int
    completed_agents: int
    failed_agents: int
    duration_seconds: float
    executions: tuple[
        AgentExecutionResult,
        ...,
    ]


class ExecutionPipeline:
    """
    Compatibility boundary for the quarantined legacy execution pipeline.

    Decisions may still expose candidate agent metadata, but legacy handlers
    cannot execute through this class. Governed execution belongs to the
    Work/Skill/Approval runtime and is intentionally not bridged here.
    """

    @staticmethod
    def execute(
        *,
        event_name: str,
        strategy_name: str,
        agents: list[RegisteredAgent],
        payload: dict[str, Any],
    ) -> PipelineResult:
        """Fail closed before any agent iteration, metrics or telemetry."""

        raise LegacyAutonomyExecutionBlockedError(
            "Legacy ExecutionPipeline execution is quarantined."
        )

    @staticmethod
    def _execute_agent(
        *,
        event_name: str,
        strategy_name: str,
        agent: RegisteredAgent,
        payload: dict[str, Any],
    ) -> AgentExecutionResult:
        """Fail closed before any legacy agent handler can run."""

        raise LegacyAutonomyExecutionBlockedError(
            "Legacy agent handler execution is quarantined."
        )
