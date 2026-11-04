"""AI-PLAN-03 / A3-01: gateway result cannot grant execution, the bridge is
bound to a verified projection and its intent, and D1-D3 snapshots fail
closed with typed codes. Database-free and provider-free."""
import dataclasses
import json

import pytest

from app.intelligence.gateway.contracts import DataClassification, ModelCallResult
from app.intelligence.planning.authority import SyntheticAuthorityContext
from app.intelligence.planning.capabilities import build_v1_registry
from app.intelligence.planning.codes import RejectionCode
from app.intelligence.planning.policy import PolicyReference
from app.intelligence.planning.validator import validate_plan
from app.intelligence.proposal.builder import project
import intelligence_pipeline_admission_bridge as bridge
from intelligence_pipeline_admission_bridge import admit_model_result
from test_intelligence_plan_validator import good_plan
from test_intelligence_projection_contracts import base_request


pytestmark = pytest.mark.contract


def result(parsed, ok=True):
    return ModelCallResult(
        ok=ok, parsed=parsed, error_code=None if ok else "provider_failure",
        call_ids=(), final_state="settled", attempts=1,
        actual_micro=0, actual_tokens=0,
    )


def admit(parsed, *, request=None, projection=None, ok=True):
    request = base_request() if request is None else request
    if projection is None:
        projection = project(request).projection
    return admit_model_result(
        result(parsed, ok=ok), projection=projection, request=request,
    )


def codes(admission):
    return {v.code for v in admission.violations}


# --- non-authority (existing contract) -----------------------------------

@pytest.mark.parametrize("payload", [None, [], "{}", float("nan"), {"x": float("inf")}])
def test_unusable_model_output_is_not_admitted(payload):
    assert admit(payload) is None


def test_failed_gateway_result_never_reaches_admission():
    assert admit(good_plan(), ok=False) is None


def test_valid_shape_still_cannot_grant_execution():
    admission = admit(good_plan())
    assert admission is not None
    assert admission.is_grant is False
    assert admission.eligibility_verified is False
    assert admission.execution_blocked_reason == "no_executor_in_this_phase"
    with pytest.raises(TypeError):
        bool(admission)


# --- projection / intent binding ----------------------------------------

def test_bound_plan_is_admitted_positive_control():
    admission = admit(good_plan())
    assert admission is not None
    assert not admission.violations
    assert admission.is_grant is False


@pytest.mark.parametrize("field,value", [
    ("objective_ref", "obj_zzzzzzzz9"),
    ("plan_version", 7),
    ("plan_version", True),
])
def test_foreign_objective_or_version_is_refused(field, value):
    plan = good_plan()
    plan[field] = value
    assert admit(plan) is None


def test_out_of_scope_account_ref_is_refused():
    plan = good_plan()
    plan["steps"][0]["params"]["account_ref"] = "acct_ffffff"
    assert admit(plan) is None


def test_out_of_scope_capability_is_refused():
    plan = good_plan()
    plan["steps"][0]["capability_id"] = "not.projected"
    assert admit(plan) is None


def test_missing_or_foreign_projection_is_refused():
    assert admit(good_plan(), projection=object()) is None
    other = project(base_request(plan_version=2)).projection
    assert other is not None
    assert admit(good_plan(), projection=other) is None


def test_tampered_projection_is_refused():
    projection = project(base_request()).projection
    forged = dataclasses.replace(projection, objective_ref="obj_zzzzzzzz9")
    plan = good_plan()
    plan["objective_ref"] = "obj_zzzzzzzz9"
    assert admit(plan, projection=forged) is None


def test_projection_from_other_request_cannot_be_rebound():
    stale = project(base_request()).projection
    changed = base_request(plan_version=2)
    assert admit(good_plan(), request=changed, projection=stale) is None


# --- full projected-schema binding and defect surfacing -----------------

def test_valid_mutating_plan_is_admitted_positive_control():
    plan = good_plan()
    assert "account.mark_overdue" in {s["capability_id"] for s in plan["steps"]}
    admission = admit(plan)
    assert admission is not None
    assert not admission.violations
    decision = getattr(admission.decision, "value", admission.decision)
    assert decision == "admitted_pending_step_approvals"
    assert admission.is_grant is False


def _extra_top_level_key(plan):
    plan["zz_unprojected"] = "x"


def _foreign_capability_version(plan):
    plan["steps"][0]["capability_version"] = 99


@pytest.mark.parametrize("mutate", [_extra_top_level_key, _foreign_capability_version])
def test_projected_schema_violation_is_refused_before_validate_plan(mutate):
    plan = good_plan()
    mutate(plan)
    request = base_request()
    # Control: validate_plan alone still yields an admission object, so a
    # None from the bridge proves refusal at the projected-schema step.
    assert _validate(plan=plan) is not None
    assert admit(plan, request=request) is None


@pytest.mark.parametrize("target", ["verify_projection", "Draft202012Validator"])
def test_internal_error_surfaces_as_defect_not_unusable_output(monkeypatch, target):
    def boom(*args, **kwargs):
        raise RuntimeError("injected internal defect")

    monkeypatch.setattr(bridge, target, boom)
    with pytest.raises(RuntimeError, match="injected internal defect"):
        admit(good_plan())


# --- D1-D3 regressions (validator snapshots) ----------------------------

def _validate(*, registry=None, policy=None, authority=None, plan=None):
    request = base_request()
    return validate_plan(
        json.dumps(good_plan() if plan is None else plan),
        registry=request.registry if registry is None else registry,
        policy=request.policy if policy is None else policy,
        authority=request.authority if authority is None else authority,
        data_classification=DataClassification.SYNTHETIC,
    )


def _authority(**overrides):
    base = base_request().authority
    options = dict(
        initiator_ref=base.initiator_ref, role=base.role,
        permissions=base.permissions, accounts=base.accounts,
    )
    options.update(overrides)
    return SyntheticAuthorityContext(**options)


def test_d_baseline_positive_control():
    admission = _validate(authority=_authority())
    assert not admission.violations
    assert admission.internal_defect is False


@pytest.mark.parametrize("overrides", [
    {"initiator_ref": "user:\x01"},
    {"initiator_ref": "user:é"},
    {"permissions": frozenset({"approval:read", 1})},
])
def test_d1_non_canonical_authority_is_rejected(overrides):
    admission = _validate(authority=_authority(**overrides))
    assert RejectionCode.AUTHORITY_CONTEXT_INVALID in codes(admission)
    assert admission.is_grant is False


def test_d2_in_place_registry_mutation_is_rejected():
    registry = build_v1_registry()
    schema = registry.get("memory.recall", 1).input_schema
    schema["properties"]["limit"]["maximum"] = 1000
    admission = _validate(registry=registry)
    assert RejectionCode.REGISTRY_INTEGRITY_FAILURE in codes(admission)


def test_d2_limits_reassignment_is_rejected():
    from app.intelligence.planning.limits import PlanLimits
    registry = build_v1_registry()
    registry.limits = PlanLimits(max_summary_chars=1)
    admission = _validate(registry=registry)
    assert RejectionCode.REGISTRY_INTEGRITY_FAILURE in codes(admission)


def test_d2_fresh_registry_positive_control():
    admission = _validate(registry=build_v1_registry())
    assert not admission.violations


def test_d3_malformed_server_objects_are_rejected_typed():
    request = base_request()
    no_registry = validate_plan(
        json.dumps(good_plan()), registry=None, policy=request.policy,
        authority=request.authority,
        data_classification=DataClassification.SYNTHETIC,
    )
    assert RejectionCode.REGISTRY_INTEGRITY_FAILURE in codes(no_registry)
    bad_policy = _validate(policy=PolicyReference(None, None, None, None))
    assert RejectionCode.POLICY_REFERENCE_INVALID in codes(bad_policy)
    empty_authority = _validate(
        authority=object.__new__(SyntheticAuthorityContext),
    )
    assert RejectionCode.AUTHORITY_CONTEXT_INVALID in codes(empty_authority)
    for admission in (no_registry, bad_policy, empty_authority):
        assert admission.is_grant is False
