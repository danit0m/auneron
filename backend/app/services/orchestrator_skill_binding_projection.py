from dataclasses import dataclass

from app.orchestrator.decision import OrchestrationDecision
from app.repositories.skill_repository import SkillRepository


@dataclass(frozen=True)
class AdvisorySkillBinding:
    """
    Safe, non-executable metadata projected from one enabled agent binding.
    """

    agent_name: str
    binding_id: int
    skill_version_id: int
    skill_id: int
    binding_priority: int
    execution_mode: str
    runtime_kind: str


@dataclass(frozen=True)
class AdvisoryAgentSkillSet:
    """
    Ordered advisory Skill bindings for one selected legacy agent.
    """

    agent_name: str
    bindings: tuple[AdvisorySkillBinding, ...]


@dataclass(frozen=True)
class AdvisorySkillBindingPlan:
    """
    Read-only projection of an OrchestrationDecision.

    This plan is advisory metadata only. It grants no authority and is not
    executable intent.
    """

    decision_name: str
    agents: tuple[AdvisoryAgentSkillSet, ...]


class OrchestratorSkillBindingProjectionService:
    """
    SELECT-only bridge foundation from advisory agent names to Skill metadata.

    The service intentionally has no Work, Approval, runtime, Memory, EventBus
    or handler dependency. It may only read existing enabled bindings and
    published/active Skill metadata through SkillRepository.
    """

    def __init__(
        self,
        repository: SkillRepository,
    ) -> None:
        self.repository = repository

    def resolve(
        self,
        decision: OrchestrationDecision,
    ) -> AdvisorySkillBindingPlan:
        if not isinstance(decision, OrchestrationDecision):
            raise TypeError(
                "decision must be an OrchestrationDecision."
            )

        selected_agents = decision.selected_agents

        if len(set(selected_agents)) != len(selected_agents):
            raise ValueError(
                "selected_agents contains duplicate agent names."
            )

        projected_agents: list[AdvisoryAgentSkillSet] = []

        for agent_name in selected_agents:
            if (
                not isinstance(agent_name, str)
                or not agent_name.strip()
            ):
                raise ValueError(
                    "selected_agents contains an invalid agent name."
                )

            projected_bindings: list[AdvisorySkillBinding] = []

            for binding in self.repository.list_bindings_for_agent(
                agent_name,
                enabled_only=True,
            ):
                version = self.repository.get_version(
                    binding.skill_version_id
                )

                if (
                    version is None
                    or version.status != "published"
                ):
                    continue

                skill = self.repository.get_skill(
                    version.skill_id
                )

                if (
                    skill is None
                    or skill.status != "active"
                ):
                    continue

                projected_bindings.append(
                    AdvisorySkillBinding(
                        agent_name=agent_name,
                        binding_id=binding.id,
                        skill_version_id=version.id,
                        skill_id=skill.id,
                        binding_priority=binding.priority,
                        execution_mode=version.execution_mode,
                        runtime_kind=version.runtime_kind,
                    )
                )

            projected_agents.append(
                AdvisoryAgentSkillSet(
                    agent_name=agent_name,
                    bindings=tuple(
                        projected_bindings
                    ),
                )
            )

        return AdvisorySkillBindingPlan(
            decision_name=decision.decision_name,
            agents=tuple(projected_agents),
        )
