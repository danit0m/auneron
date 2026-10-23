"""AI-PLAN-05: deterministic, offline planning pipeline."""
from app.intelligence.gateway.contracts import ModelCallResult
from intelligence_pipeline_simulation import simulate_proposal
from test_intelligence_plan_validator import good_plan
from test_intelligence_projection_contracts import base_request


def fixture(parsed, ok=True):
    return ModelCallResult(
        ok=ok, parsed=parsed, error_code=None if ok else "provider_failure",
        call_ids=(), final_state="settled", attempts=1,
        actual_micro=0, actual_tokens=0,
    )


def run(result, **overrides):
    return simulate_proposal(
        base_request(**overrides), call_key="synthetic-fixture-05",
        input_text="Synthetic planning exercise.", max_output_tokens=128,
        fixture=result,
    )


def test_fixture_plan_cannot_authorize_execution():
    outcome = run(fixture(good_plan()))
    assert outcome.prepared.model_request is not None
    assert outcome.admission is not None
    assert outcome.admission.is_grant is False
    assert outcome.admission.execution_blocked_reason == "no_executor_in_this_phase"


def test_failed_fixture_is_not_admitted():
    outcome = run(fixture(good_plan(), ok=False))
    assert outcome.prepared.model_request is not None
    assert outcome.admission is None


def test_invalid_fixture_is_not_admitted():
    outcome = run(fixture(["not", "a", "plan"]))
    assert outcome.admission is None


def test_unexpected_server_fields_never_grant_execution():
    hostile = good_plan()
    hostile["approved"] = True
    hostile["execution_authorized"] = True
    outcome = run(fixture(hostile))
    assert outcome.admission is not None
    assert outcome.admission.is_grant is False
    assert outcome.admission.eligibility_verified is False


def test_model_result_is_deterministic_and_does_not_mutate_fixture():
    import copy

    plan = good_plan()
    original = copy.deepcopy(plan)
    first = run(fixture(plan))
    second = run(fixture(plan))
    assert plan == original
    assert first.admission == second.admission
    assert first.prepared.model_request == second.prepared.model_request
    assert first.admission is not None
    assert first.admission.is_grant is False


def test_rejected_plan_remains_blocked_across_retries():
    hostile = good_plan()
    hostile["steps"] = "not-an-array"
    for _ in range(3):
        outcome = run(fixture(hostile))
        assert outcome.admission is not None
        assert outcome.admission.is_grant is False
        assert outcome.admission.eligibility_verified is False


def test_projection_failure_prevents_admission():
    outcome = run(fixture(good_plan()), account_refs=("acct_missing",))
    assert outcome.prepared.model_request is None
    assert outcome.admission is None
