"""Pure, non-executing boundary between gateway output and plan admission.

No provider calls, I/O, authorization or execution occurs here.
"""
import json

from app.intelligence.gateway.contracts import DataClassification, ModelCallResult
from app.intelligence.planning.admission import PlanAdmission
from app.intelligence.planning.authority import SyntheticAuthorityContext
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.policy import PolicyReference
from app.intelligence.planning.validator import validate_plan


def admit_model_result(
    result: ModelCallResult,
    *,
    registry: CapabilityRegistry,
    policy: PolicyReference | None,
    authority: SyntheticAuthorityContext | None,
) -> PlanAdmission | None:
    """Return structural admission, or None for an unusable gateway response.

    An admission is never a grant. Caller MUST NOT interpret a non-None
    return value as permission to execute. The gateway is responsible for
    budget, schema and provider enforcement before calling this boundary.
    """
    if type(result) is not ModelCallResult or result.ok is not True:
        return None
    if type(result.parsed) is not dict:
        return None
    try:
        raw = json.dumps(
            result.parsed, ensure_ascii=True, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    return validate_plan(
        raw,
        registry=registry,
        policy=policy,
        authority=authority,
        data_classification=DataClassification.SYNTHETIC,
    )
