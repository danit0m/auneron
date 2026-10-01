from typing import Annotated
from typing import Literal
from typing import Union
from datetime import datetime

from pydantic import Field

from app.models.escalation_observation import EscalationObservation
from app.schemas.work import WorkSchema


AssessmentCode = Literal[
    "contact_made",
    "payment_promised",
    "payment_refused",
    "unreachable",
    "partial_agreement",
]


class ObservedFactObservationResponse(WorkSchema):
    observation_type: Literal["observed_fact"]
    id: int
    escalation_work_item_id: int
    created_at: datetime
    linked_account_event_id: int
    observed_at: datetime


class HumanAssessmentRequest(WorkSchema):
    assessment_code: AssessmentCode


class HumanAssessmentObservationResponse(WorkSchema):
    observation_type: Literal["human_assessment"]
    id: int
    escalation_work_item_id: int
    created_at: datetime
    assessment_code: AssessmentCode
    declared_by_user_id: int
    declared_at: datetime


EscalationObservationResponse = Annotated[
    Union[
        ObservedFactObservationResponse,
        HumanAssessmentObservationResponse,
    ],
    Field(discriminator="observation_type"),
]


def escalation_observation_response(
    observation: EscalationObservation,
) -> (
    ObservedFactObservationResponse
    | HumanAssessmentObservationResponse
):
    if observation.observation_type == "observed_fact":
        return ObservedFactObservationResponse(
            observation_type="observed_fact",
            id=observation.id,
            escalation_work_item_id=(
                observation.escalation_work_item_id
            ),
            created_at=observation.created_at,
            linked_account_event_id=(
                observation.linked_account_event_id
            ),
            observed_at=observation.observed_at,
        )

    return HumanAssessmentObservationResponse(
        observation_type="human_assessment",
        id=observation.id,
        escalation_work_item_id=(
            observation.escalation_work_item_id
        ),
        created_at=observation.created_at,
        assessment_code=observation.assessment_code,
        declared_by_user_id=observation.declared_by_user_id,
        declared_at=observation.declared_at,
    )


class EscalationObservationListResponse(WorkSchema):
    items: list[EscalationObservationResponse]
    next_cursor: int | None = None
