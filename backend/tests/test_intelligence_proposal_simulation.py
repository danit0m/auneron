"""AI-PLAN-05: deterministic, offline planning pipeline."""
import pytest

from app.intelligence.gateway.contracts import ModelCallResult
from app.intelligence.proposal.contracts import ProfileView
from intelligence_pipeline_simulation import simulate_proposal
from test_intelligence_plan_validator import good_plan
from test_intelligence_projection_contracts import base_request


def fixture(parsed, ok=True, profiles_used=("lab-fast",)):
    return ModelCallResult(
        ok=ok, parsed=parsed, error_code=None if ok else "provider_failure",
        call_ids=(), final_state="settled", attempts=1,
        actual_micro=0, actual_tokens=0, profiles_used=profiles_used,
    )


def run(result, **overrides):
    return simulate_proposal(
        base_request(**overrides), call_key="synthetic-fixture-05",
        input_text="Synthetic planning exercise.", max_output_tokens=128,
        fixture=result, deployment_budget_id=1,
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
    # The verified closed schema refuses fabricated authority before admission.
    assert outcome.admission is None


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
        # Invalid step structure remains blocked on every attempt.
        assert outcome.admission is None


def test_projection_failure_prevents_admission():
    outcome = run(fixture(good_plan()), account_refs=("acct_missing",))
    assert outcome.prepared.model_request is None
    assert outcome.admission is None


# AI-PLAN-12-P1A: a fixture is admitted only if every recorded attempt
# used the projected profile ("lab-fast" in base_request).


def test_fixture_from_projected_profile_is_admitted():
    outcome = run(fixture(good_plan(), profiles_used=("lab-fast",)))
    assert outcome.prepared.model_request.profile == "lab-fast"
    assert outcome.admission is not None
    assert outcome.admission.is_grant is False


def test_fixture_with_retries_on_projected_profile_is_admitted():
    outcome = run(fixture(good_plan(), profiles_used=("lab-fast", "lab-fast")))
    assert outcome.admission is not None


@pytest.mark.parametrize("profiles_used", [
    ("lab-strong",),                # unrelated profile
    ("lab-fast", "lab-strong"),     # fallback after a failed primary
    ("lab-strong", "lab-fast"),     # impossible order
    ("lab-fast", "lab-fast "),      # lookalike
    ("Lab-fast",),
    (),                             # missing
])
def test_fixture_not_produced_by_projected_profile_is_not_admitted(profiles_used):
    outcome = run(fixture(good_plan(), profiles_used=profiles_used))
    assert outcome.prepared.model_request is not None
    assert outcome.admission is None


@pytest.mark.parametrize("profiles_used", [
    None, "lab-fast", ["lab-fast"], ("lab-fast", None), (b"lab-fast",),
])
def test_malformed_profiles_used_is_not_admitted(profiles_used):
    outcome = run(fixture(good_plan(), profiles_used=profiles_used))
    assert outcome.admission is None


def test_profile_match_follows_the_projected_request():
    strong = dict(profile=ProfileView("lab-strong", 512))
    matching = run(fixture(good_plan(), profiles_used=("lab-strong",)), **strong)
    stale = run(fixture(good_plan(), profiles_used=("lab-fast",)), **strong)
    assert matching.prepared.model_request.profile == "lab-strong"
    assert matching.admission is not None
    assert stale.admission is None


def test_non_result_fixture_is_not_admitted():
    outcome = run({"ok": True, "parsed": good_plan(), "profiles_used": ("lab-fast",)})
    assert outcome.admission is None


def test_budget_ids_are_passed_through_to_the_model_request():
    outcome = simulate_proposal(
        base_request(), call_key="synthetic-fixture-12", input_text="Synthetic.",
        max_output_tokens=128, fixture=fixture(good_plan()), user_budget_id=3,
    )
    assert outcome.prepared.model_request.budget_ids == (3,)
    assert outcome.admission is not None


def test_simulation_without_budget_id_builds_no_request():
    outcome = simulate_proposal(
        base_request(), call_key="synthetic-fixture-12", input_text="Synthetic.",
        max_output_tokens=128, fixture=fixture(good_plan()),
    )
    assert outcome.prepared.model_request is None
    assert outcome.prepared.rejection_reason == "missing_budget_id"
    assert outcome.admission is None
