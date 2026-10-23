"""Offline test harness for proposal preparation and admission.

Explicitly accepts a deterministic response fixture; never calls a provider.
"""
from dataclasses import dataclass

from app.intelligence.gateway.contracts import ModelCallResult
from app.intelligence.planning.admission import PlanAdmission
from intelligence_pipeline_admission_bridge import admit_model_result
from app.intelligence.proposal.contracts import ProjectionRequest
from intelligence_pipeline_preparation import PreparedProposal, prepare_proposal


@dataclass(frozen=True)
class SimulatedPipelineResult:
    prepared: PreparedProposal
    admission: PlanAdmission | None


def simulate_proposal(
    request: ProjectionRequest,
    *,
    call_key: str,
    input_text: str,
    max_output_tokens: int,
    fixture: ModelCallResult,
) -> SimulatedPipelineResult:
    """Evaluate a supplied synthetic fixture; no gateway dispatch or execution."""
    prepared = prepare_proposal(
        request, call_key=call_key, input_text=input_text,
        max_output_tokens=max_output_tokens,
    )
    if prepared.model_request is None:
        return SimulatedPipelineResult(prepared, None)
    admission = admit_model_result(
        fixture, registry=request.registry,
        policy=request.policy, authority=request.authority,
    )
    return SimulatedPipelineResult(prepared, admission)
