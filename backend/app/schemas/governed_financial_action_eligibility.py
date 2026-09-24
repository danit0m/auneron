from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.approval import ApprovalRequestResponse
from app.schemas.outcome import FinancialEpisode
from app.schemas.work import WorkResponse


MarkOverdueReason = Literal[
    "due_date_mismatch",
    "already_paid",
    "status_not_open",
    "not_overdue",
]

MarkPaidReason = Literal["already_paid"]


class MarkOverdueEligibilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    eligibility_policy: str
    episode: FinancialEpisode
    structurally_available: bool
    system_recommendable: bool
    requires_external_fact: None
    reason: MarkOverdueReason | None


class MarkPaidEligibilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    eligibility_policy: str
    episode: FinancialEpisode
    structurally_available: bool
    system_recommendable: bool
    requires_external_fact: Literal["payment_observed"] | None
    reason: MarkPaidReason | None
    due_date_matches_current_vencimento: bool


class HumanAccountMarkOverdueMaterializationRequest(BaseModel):
    """
    DW-6.4B -- corpo opcional. Ausencia total do corpo continua
    suportando o comportamento historico (materializacao sem
    referencia a nenhuma recomendacao). Quando fornecido,
    recommendation_snapshot_id e apenas uma REFERENCIA declarada pelo
    operador -- nunca aceita/consumida sem passar pela verificacao de
    integridade do DW-6.4A (get_verified()).
    """

    model_config = ConfigDict(from_attributes=True)

    recommendation_snapshot_id: int | None = None


class HumanAccountMarkOverdueMaterializationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_item: WorkResponse
    approval_request: ApprovalRequestResponse
    created: bool
    duplicate: bool


class HumanAccountMarkOverdueExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_item_id: int
    approval_request_id: int
    approval_consumption_id: int
    invocation_id: int
    invocation_status: str
    duplicate: bool
    output: dict[str, Any]
