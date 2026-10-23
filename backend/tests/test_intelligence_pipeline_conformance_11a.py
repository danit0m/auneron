"""AI-PLAN-11A: offline deterministic conformance and non-authority tests."""
import copy

import pytest

from app.intelligence.gateway.contracts import DataClassification, ModelCallResult
from intelligence_pipeline_admission_bridge import admit_model_result
from intelligence_pipeline_simulation import simulate_proposal
from test_intelligence_plan_validator import good_plan
from test_intelligence_projection_contracts import base_request


def _fixture(parsed, *, ok=True):
    return ModelCallResult(
        ok=ok, parsed=parsed, error_code=None if ok else "provider_failure",
        call_ids=(), final_state="settled", attempts=1,
        actual_micro=0, actual_tokens=0,
    )


def _simulate(parsed, *, call_key="synthetic-11a"):
    return simulate_proposal(
        base_request(), call_key=call_key,
        input_text="Synthetic-only plan.", max_output_tokens=128,
        fixture=_fixture(parsed),
    )


def test_replay_preserves_projection_and_admission_without_grant():
    plan = good_plan()
    original = copy.deepcopy(plan)
    first = _simulate(plan)
    second = _simulate(plan)
    assert plan == original
    assert first == second
    assert first.prepared.model_request is not None
    assert first.prepared.model_request.data_classification is DataClassification.SYNTHETIC
    assert first.admission is not None
    assert first.admission.is_grant is False
    assert first.admission.eligibility_verified is False
    assert first.admission.execution_blocked_reason == "no_executor_in_this_phase"
    with pytest.raises(TypeError):
        bool(first.admission)


@pytest.mark.parametrize("payload", [
    None, [], "{}", {"x": float("nan")}, {"x": float("inf")},
    {"x": object()}, {"x": {1, 2}},
])
def test_unusable_fixture_is_fail_closed(payload):
    request = base_request()
    result = admit_model_result(
        _fixture(payload), registry=request.registry,
        policy=request.policy, authority=request.authority,
    )
    assert result is None


def test_rejected_projection_never_admits_even_valid_fixture():
    outcome = simulate_proposal(
        base_request(account_refs=("missing_account",)),
        call_key="synthetic-11a", input_text="Synthetic-only plan.",
        max_output_tokens=128, fixture=_fixture(good_plan()),
    )
    assert outcome.prepared.model_request is None
    assert outcome.admission is None


def test_untrusted_claims_cannot_become_authority():
    hostile = good_plan()
    hostile["approved"] = True
    hostile["execution_authorized"] = True
    hostile["eligibility_verified"] = True
    outcome = _simulate(hostile)
    assert outcome.admission is not None
    assert outcome.admission.is_grant is False
    assert outcome.admission.eligibility_verified is False


def test_call_key_changes_request_not_plan_authority():
    plan = good_plan()
    first = _simulate(plan, call_key="synthetic-11a-a")
    second = _simulate(plan, call_key="synthetic-11a-b")
    assert first.prepared.model_request.call_key != second.prepared.model_request.call_key
    assert first.admission == second.admission
    assert first.admission is not None
    assert first.admission.is_grant is False
