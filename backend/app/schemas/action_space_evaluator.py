from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.outcome import FinancialEpisode


CapabilityKind = Literal["skill_action", "work_action"]
InitiationActor = Literal["agent", "user", "rbac_actor"]
AuthorityModel = Literal["approval", "rbac"]


class ActionEvaluation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action_key: str
    capability_kind: CapabilityKind
    eligibility_policy: str
    structurally_available: bool
    system_recommendable: bool
    requires_external_fact: str | None
    reason: str | None
    initiation_actor: InitiationActor
    authority_model: AuthorityModel
    required_authority: str
    execution_corridor: str


class ActionSpaceEvaluationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    episode: FinancialEpisode
    actions: list[ActionEvaluation]
    recommendable_actions: list[str]
    no_action: bool
