from dataclasses import dataclass

from app.core.authority_provenance import AuthorityProvenance
from app.orchestrator.decision import OrchestrationDecision
from app.services.orchestrator_skill_binding_projection import (
    AdvisorySkillBindingPlan,
)


@dataclass(frozen=True)
class AuthenticatedAdvisoryEnvelope:
    """
    Immutable non-executable composition of advisory decision context.

    The envelope binds one OrchestrationDecision, its safe advisory Skill
    binding plan and authenticated authority provenance. It grants no
    authority, is not an authorization decision and is not executable intent.
    """

    decision: OrchestrationDecision
    plan: AdvisorySkillBindingPlan
    authority: AuthorityProvenance

    def __post_init__(self) -> None:
        if not isinstance(
            self.decision,
            OrchestrationDecision,
        ):
            raise TypeError(
                "decision must be an OrchestrationDecision."
            )

        if not isinstance(
            self.plan,
            AdvisorySkillBindingPlan,
        ):
            raise TypeError(
                "plan must be an AdvisorySkillBindingPlan."
            )

        if not isinstance(
            self.authority,
            AuthorityProvenance,
        ):
            raise TypeError(
                "authority must be an AuthorityProvenance."
            )

        if (
            self.plan.decision_name
            != self.decision.decision_name
        ):
            raise ValueError(
                "plan.decision_name must match "
                "decision.decision_name."
            )

        selected_agents = (
            self.decision.selected_agents
        )
        planned_agents = tuple(
            agent.agent_name
            for agent in self.plan.agents
        )

        if planned_agents != selected_agents:
            raise ValueError(
                "plan agent order and membership must "
                "exactly match decision.selected_agents."
            )
