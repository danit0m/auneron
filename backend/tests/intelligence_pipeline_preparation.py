"""Offline-only construction of a projected planning model request.

Does not call a provider, write to a database, or execute a plan.
"""
from dataclasses import dataclass

from app.intelligence.gateway.contracts import DataClassification, ModelCallRequest
from app.intelligence.proposal.builder import project
from app.intelligence.proposal.contracts import ProjectionRequest, ProjectionResult


@dataclass(frozen=True)
class PreparedProposal:
    projection_result: ProjectionResult
    model_request: ModelCallRequest | None


def prepare_proposal(
    request: ProjectionRequest,
    *,
    call_key: str,
    input_text: str,
    max_output_tokens: int,
) -> PreparedProposal:
    """Construct a model request from an approved projection, never dispatch it."""
    outcome = project(request)
    projection = outcome.projection
    if projection is None:
        return PreparedProposal(outcome, None)
    if (
        type(call_key) is not str or not 1 <= len(call_key) <= 120
        or type(input_text) is not str or not input_text
        or type(max_output_tokens) is not int
        or not 1 <= max_output_tokens <= projection.profile_max_output_tokens_cap
        or len(input_text.encode("utf-8")) > 32768
    ):
        return PreparedProposal(outcome, None)
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
            prompt_template_id=projection.template_id,
            output_schema_id=projection.schema_digest,
        ),
    )
