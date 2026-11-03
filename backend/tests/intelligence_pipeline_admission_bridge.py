"""Pure, non-executing boundary between gateway output and plan admission.

No provider calls, I/O, authorization or execution occurs here.
"""
import json

from jsonschema import Draft202012Validator

from app.intelligence.gateway.contracts import DataClassification, ModelCallResult
from app.intelligence.planning.admission import PlanAdmission
from app.intelligence.proposal.builder import verify_projection
from app.intelligence.proposal.contracts import Projection, ProjectionRequest
from app.intelligence.planning.validator import validate_plan


def _within_projection(plan: dict, projection: Projection) -> bool:
    """Exact intent binding plus a redundant scope cross-check.

    The verified projection.output_schema is the authoritative shape and
    scope check (see admit_model_result); this only adds exact-type
    objective/version binding and a second look at top-level scope refs.
    """
    if (
        plan.get("objective_ref") != projection.objective_ref
        or plan.get("plan_version") != projection.plan_version
        or type(plan.get("plan_version")) is not int
    ):
        return False
    capabilities = frozenset(projection.capability_ids)
    accounts = frozenset(projection.account_refs)
    for step in plan.get("steps", ()):
        if step.get("capability_id") not in capabilities:
            return False
        params = step.get("params", {})
        if "account_ref" in params and params["account_ref"] not in accounts:
            return False
    return True


def admit_model_result(
    result: ModelCallResult,
    *,
    projection: Projection,
    request: ProjectionRequest,
) -> PlanAdmission | None:
    """Return structural admission, or None for an unusable or unbound response.

    Closed order:
    1. not an ok ModelCallResult with an exact-dict ``parsed`` -> None;
    2. verify_projection(projection, request) != () -> None (malformed,
       tampered or stale projection);
    3. canonical JSON round trip into a plain copy fails -> None;
    4. the plain copy violates the full verified projection.output_schema
       (Draft 2020-12: capability ids and versions, params, refs, limits,
       closed objects) -> None;
    5. objective_ref / plan_version / scope binding fails -> None;
    6. validate_plan with the request's registry, policy and authority.

    verify_projection is documented as total and the steps after it operate
    on a schema-valid plain copy, so no blanket exception handler exists
    here: an unexpected internal error is a defect and propagates.

    An admission is never a grant. Caller MUST NOT interpret a non-None
    return value as permission to execute. The gateway is responsible for
    budget and provider enforcement before calling this boundary.
    """
    if type(result) is not ModelCallResult or result.ok is not True:
        return None
    if type(result.parsed) is not dict:
        return None
    if verify_projection(projection, request) != ():
        return None
    try:
        raw = json.dumps(
            result.parsed, ensure_ascii=True, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        )
        plain = json.loads(raw)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    validator = Draft202012Validator(projection.output_schema)
    if next(validator.iter_errors(plain), None) is not None:
        return None
    if not _within_projection(plain, projection):
        return None
    return validate_plan(
        raw,
        registry=request.registry,
        policy=request.policy,
        authority=request.authority,
        data_classification=DataClassification.SYNTHETIC,
    )
