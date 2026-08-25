from datetime import datetime
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest


ApprovalStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "expired",
    "cancelled",
]

ApprovalRiskLevel = Literal[
    "low",
    "medium",
    "high",
    "critical",
]

ApprovalDecisionValue = Literal[
    "approved",
    "rejected",
]


class ApprovalAPISchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class ApprovalCreateRequest(
    ApprovalAPISchema
):
    input_payload: Any


class ApprovalDecisionRequest(
    ApprovalAPISchema
):
    decision: ApprovalDecisionValue
    decision_note: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )


class ApprovalRequestResponse(
    ApprovalAPISchema
):
    request_id: int
    action_type: Literal[
        "skill_execution"
    ]
    skill_version_id: int
    requester_actor_type: Literal[
        "user",
        "agent",
        "system",
        "integration",
    ]
    requester_user_id: int | None
    risk_level: ApprovalRiskLevel
    status: ApprovalStatus
    target_account_id: int | None
    target_user_id: int | None
    expires_at: datetime
    resolved_at: datetime | None
    created_at: datetime

    @classmethod
    def from_request(
        cls,
        request: ApprovalRequest,
    ) -> "ApprovalRequestResponse":
        return cls(
            request_id=request.id,
            action_type=request.action_type,
            skill_version_id=(
                request.skill_version_id
            ),
            requester_actor_type=(
                request.requester_actor_type
            ),
            requester_user_id=(
                request.requester_user_id
            ),
            risk_level=request.risk_level,
            status=request.status,
            target_account_id=(
                request.target_account_id
            ),
            target_user_id=(
                request.target_user_id
            ),
            expires_at=request.expires_at,
            resolved_at=request.resolved_at,
            created_at=request.created_at,
        )


class ApprovalDecisionResponse(
    ApprovalAPISchema
):
    decision_id: int
    approval_request_id: int
    decision: ApprovalDecisionValue
    decided_by_user_id: int | None
    decided_by_role: str
    decision_note: str | None
    created_at: datetime

    @classmethod
    def from_decision(
        cls,
        decision: ApprovalDecision,
    ) -> "ApprovalDecisionResponse":
        return cls(
            decision_id=decision.id,
            approval_request_id=(
                decision.approval_request_id
            ),
            decision=decision.decision,
            decided_by_user_id=(
                decision.decided_by_user_id
            ),
            decided_by_role=(
                decision.decided_by_role
            ),
            decision_note=(
                decision.decision_note
            ),
            created_at=decision.created_at,
        )


class ApprovalCreationResponse(
    ApprovalAPISchema
):
    request: ApprovalRequestResponse
    created: bool
    duplicate: bool


class ApprovalDetailsResponse(
    ApprovalAPISchema
):
    request: ApprovalRequestResponse
    decision: ApprovalDecisionResponse | None


class ApprovalListResponse(
    ApprovalAPISchema
):
    items: list[ApprovalRequestResponse]
    next_cursor: int | None


class ApprovalDecisionResultResponse(
    ApprovalAPISchema
):
    request: ApprovalRequestResponse
    decision: ApprovalDecisionResponse
