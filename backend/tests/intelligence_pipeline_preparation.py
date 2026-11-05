"""Offline-only construction of a projected planning model request.

Does not call a provider, write to a database, or execute a plan.
"""
from dataclasses import dataclass

from app.intelligence.gateway.contracts import DataClassification, ModelCallRequest
from app.intelligence.gateway.pricing import input_token_upper_bound
from app.intelligence.proposal.builder import project
from app.intelligence.proposal.contracts import (
    Projection,
    ProjectionRequest,
    ProjectionResult,
)

# Mirror the gateway's limits (gateway.py); gateway.py is not imported
# because it pulls in the database layer.
MAX_CALL_KEY_LENGTH = 120
MAX_REQUEST_BYTES = 32_768


class PreparationRejection:
    """Stable reason codes for a preparation that built no model request.

    Fixed literals only: a code never carries the call key, input text,
    budget identifiers or any other request data.
    """
    PROJECTION_REJECTED = "projection_rejected"
    INVALID_CALL_KEY = "invalid_call_key"
    INVALID_INPUT_TEXT = "invalid_input_text"
    INVALID_MAX_OUTPUT_TOKENS = "invalid_max_output_tokens"
    REQUEST_TOO_LARGE = "request_too_large"
    MISSING_BUDGET_ID = "missing_budget_id"


PREPARATION_REJECTION_REASONS = frozenset({
    PreparationRejection.PROJECTION_REJECTED,
    PreparationRejection.INVALID_CALL_KEY,
    PreparationRejection.INVALID_INPUT_TEXT,
    PreparationRejection.INVALID_MAX_OUTPUT_TOKENS,
    PreparationRejection.REQUEST_TOO_LARGE,
    PreparationRejection.MISSING_BUDGET_ID,
})


@dataclass(frozen=True)
class PreparedProposal:
    """`rejection_reason` is None exactly when `model_request` is not None."""
    projection_result: ProjectionResult
    model_request: ModelCallRequest | None
    rejection_reason: str | None = None


def _rejection_reason(
    projection: Projection,
    call_key: object,
    input_text: object,
    max_output_tokens: object,
    budget_ids: tuple,
) -> str | None:
    """First failing check, in a fixed order."""
    if (
        type(call_key) is not str or not call_key.strip()
        or len(call_key) > MAX_CALL_KEY_LENGTH
    ):
        return PreparationRejection.INVALID_CALL_KEY
    if type(input_text) is not str or not input_text:
        return PreparationRejection.INVALID_INPUT_TEXT
    if (
        type(max_output_tokens) is not int
        or not 1 <= max_output_tokens <= projection.profile_max_output_tokens_cap
    ):
        return PreparationRejection.INVALID_MAX_OUTPUT_TOKENS
    if (
        input_token_upper_bound(projection.instructions, input_text)
        > MAX_REQUEST_BYTES
    ):
        return PreparationRejection.REQUEST_TOO_LARGE
    if not budget_ids:
        return PreparationRejection.MISSING_BUDGET_ID
    return None


def prepare_proposal(
    request: ProjectionRequest,
    *,
    call_key: str,
    input_text: str,
    max_output_tokens: int,
    user_budget_id: int | None = None,
    objective_budget_id: int | None = None,
    deployment_budget_id: int | None = None,
) -> PreparedProposal:
    """Construct a model request from an approved projection, never dispatch it.

    Budget identifiers are taken as given, never invented here: at least
    one of the three must be supplied, matching the gateway's own
    `not request.budget_ids` refusal, or no model request is built.
    """
    outcome = project(request)
    projection = outcome.projection
    if projection is None:
        return PreparedProposal(
            outcome, None, PreparationRejection.PROJECTION_REJECTED
        )
    budget_ids = tuple(
        budget_id
        for budget_id in (
            user_budget_id, objective_budget_id, deployment_budget_id,
        )
        if budget_id is not None
    )
    reason = _rejection_reason(
        projection, call_key, input_text, max_output_tokens, budget_ids
    )
    if reason is not None:
        return PreparedProposal(outcome, None, reason)
    return PreparedProposal(
        outcome,
        ModelCallRequest(
            call_key=call_key,
            purpose="plan_proposal",
            profile=projection.profile_name,
            instructions=projection.instructions,
            input_text=input_text,
            output_schema=projection.output_schema,
            max_output_tokens=max_output_tokens,
            data_classification=DataClassification.SYNTHETIC,
            user_budget_id=user_budget_id,
            objective_budget_id=objective_budget_id,
            deployment_budget_id=deployment_budget_id,
            prompt_template_id=projection.template_id,
            output_schema_id=projection.schema_digest,
        ),
    )
