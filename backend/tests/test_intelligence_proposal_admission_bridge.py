"""AI-PLAN-03: gateway result cannot grant execution."""
import pytest

from app.intelligence.gateway.contracts import ModelCallResult
from app.intelligence.planning.capabilities import REGISTRY_V1
from intelligence_pipeline_admission_bridge import admit_model_result
from tests.test_intelligence_plan_validator import good_plan


pytestmark = pytest.mark.contract


def result(parsed, ok=True):
    return ModelCallResult(
        ok=ok, parsed=parsed, error_code=None if ok else "provider_failure",
        call_ids=(), final_state="settled", attempts=1,
        actual_micro=0, actual_tokens=0,
    )


@pytest.mark.parametrize("payload", [None, [], "{}", float("nan"), {"x": float("inf")}])
def test_unusable_model_output_is_not_admitted(payload):
    assert admit_model_result(
        result(payload), registry=REGISTRY_V1, policy=None, authority=None,
    ) is None


def test_failed_gateway_result_never_reaches_admission():
    assert admit_model_result(
        result(good_plan(), ok=False),
        registry=REGISTRY_V1, policy=None, authority=None,
    ) is None


def test_valid_shape_still_cannot_grant_execution():
    admission = admit_model_result(
        result(good_plan()), registry=REGISTRY_V1, policy=None, authority=None,
    )
    assert admission is not None
    assert admission.is_grant is False
    assert admission.eligibility_verified is False
    assert admission.execution_blocked_reason == "no_executor_in_this_phase"
    with pytest.raises(TypeError):
        bool(admission)
