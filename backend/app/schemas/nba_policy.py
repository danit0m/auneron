from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.action_space_evaluator import (
    ActionSpaceEvaluationResponse,
)
from app.schemas.outcome import FinancialEpisode
from app.schemas.outcome import Money


DecisionType = Literal["single_action", "action_bundle", "no_action"]


class RecurrenceFacts(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    average_late_days: float | None
    resolved_occurrences: int | None


class ObservedFacts(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    days_overdue: int
    amount: Money
    recurrence: RecurrenceFacts | None
    active_escalation: bool


class CalibrationSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: str
    critical_overdue_reference_days: int
    early_high_exposure_reference_day: int
    absolute_high_value_reference: Money
    operational_cost_floor_reference: Money


class Decision(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    decision_type: DecisionType
    selected_actions: list[str]


class NbaRecommendationSnapshotPayload(BaseModel):
    """
    DW-6.4A -- forma canonica usada exclusivamente para serializar
    NbaDecisionEvidence (objeto de dominio) em snapshot_payload/
    snapshot_digest. Nunca inclui recommendation_snapshot_id -- essa
    identidade so existe depois que o snapshot ja foi persistido, e
    incluir aqui criaria autorreferencia. Nao confundir com
    NbaDecisionEvidenceResponse (forma HTTP, com o ID).
    """

    model_config = ConfigDict(from_attributes=True)

    episode: FinancialEpisode
    action_space: ActionSpaceEvaluationResponse
    observed_facts: ObservedFacts
    policy_version: str
    calibration: CalibrationSnapshot
    applied_rules: list[str]
    decision: Decision
    requires_human_review: bool
    human_review_reasons: list[str]


class NbaDecisionEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    episode: FinancialEpisode
    action_space: ActionSpaceEvaluationResponse
    observed_facts: ObservedFacts
    policy_version: str
    calibration: CalibrationSnapshot
    applied_rules: list[str]
    decision: Decision
    requires_human_review: bool
    human_review_reasons: list[str]
    recommendation_snapshot_id: int
