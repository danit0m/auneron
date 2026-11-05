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


def _produced_by(fixture: ModelCallResult, profile_name: str) -> bool:
    """True only if every recorded attempt used the projected profile.

    Fallback attempts are not attributable to this projection (the
    pipeline knows no fallback chain), so they fail closed.
    """
    if type(fixture) is not ModelCallResult:
        return False
    used = fixture.profiles_used
    return (
        type(used) is tuple and len(used) > 0
        and all(type(name) is str and name == profile_name for name in used)
    )


def simulate_proposal(
    request: ProjectionRequest,
    *,
    call_key: str,
    input_text: str,
    max_output_tokens: int,
    fixture: ModelCallResult,
    user_budget_id: int | None = None,
    objective_budget_id: int | None = None,
    deployment_budget_id: int | None = None,
) -> SimulatedPipelineResult:
    """Evaluate a supplied synthetic fixture; no gateway dispatch or execution."""
    prepared = prepare_proposal(
        request, call_key=call_key, input_text=input_text,
        max_output_tokens=max_output_tokens,
        user_budget_id=user_budget_id,
        objective_budget_id=objective_budget_id,
        deployment_budget_id=deployment_budget_id,
    )
    if prepared.model_request is None:
        return SimulatedPipelineResult(prepared, None)
    if not _produced_by(fixture, prepared.model_request.profile):
        return SimulatedPipelineResult(prepared, None)
    admission = admit_model_result(
        fixture, projection=prepared.projection_result.projection,
        request=request,
    )
    return SimulatedPipelineResult(prepared, admission)
