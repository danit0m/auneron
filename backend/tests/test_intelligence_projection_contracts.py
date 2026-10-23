"""INTELLIGENCE-PROPOSAL -- proj.v1 contracts and shared helpers.

Pure and offline: no database, no network, no provider, no gateway.
The helpers at the top are imported by the sibling projection tests.
"""

import copy
import dataclasses
import hashlib
import json
import os

import pytest

from app.intelligence.planning.canonical import canonical_bytes
from app.intelligence.planning.capabilities import PINNED_REGISTRY_DIGEST
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.capabilities import build_v1_registry
from app.intelligence.proposal import contracts as c
from app.intelligence.proposal.builder import project
from app.intelligence.proposal.builder import verify_projection

from test_intelligence_plan_validator import ACCT
from test_intelligence_plan_validator import ALL_PERMISSIONS
from test_intelligence_plan_validator import build_real_policy_reference
from test_intelligence_plan_validator import make_authority


pytestmark = pytest.mark.contract

OBJ = "obj_k3m9x2ab"
REF = ACCT
REF2 = "acct_b2c4d6"
PROPOSABLE = (
    "account.get_context",
    "account.mark_overdue",
    "action_space.evaluate",
    "memory.recall",
    "nba.evaluate",
)
SCALE = float(os.environ.get("PROJ_FUZZ_SCALE", "1"))


def scaled(count):
    """Iteration count; mutation runs lower it with PROJ_FUZZ_SCALE."""
    return max(5, int(count * SCALE))


# ----------------------------------------------------------------------
# shared helpers
# ----------------------------------------------------------------------


def base_request(**overrides):
    options = {
        "registry": REGISTRY_V1,
        "pinned_registry_digest": PINNED_REGISTRY_DIGEST,
        "policy": build_real_policy_reference(),
        "authority": make_authority(),
        "profile": c.ProfileView(name="lab-fast", max_output_tokens_cap=512),
        "objective_ref": OBJ,
        "plan_version": 1,
        "account_refs": (REF,),
        "limits": c.ProjectionLimits(),
    }
    options.update(overrides)
    return c.ProjectionRequest(**options)


def run(**overrides):
    return project(base_request(**overrides))


def ok_projection(**overrides):
    result = run(**overrides)
    assert result.ok, result.codes
    return result.projection


def subset_registry(ids):
    """Registry holding only the given proposable ids (plus everything
    that is not a candidate, which must never surface)."""
    keep = []

    for definition in REGISTRY_V1.definitions:
        is_candidate = (
            definition.registered
            and definition.enabled
            and definition.llm_proposable
        )

        if not is_candidate or definition.capability_id in ids:
            keep.append(definition)

    return CapabilityRegistry(tuple(keep))


def closure(ids):
    """Independent reference for the prerequisite closure."""
    offered = set(ids)

    while True:
        drop = {
            definition.capability_id
            for definition in REGISTRY_V1.definitions
            if definition.capability_id in offered
            and any(
                not (set(rule.any_of) & offered)
                for rule in definition.prerequisites
            )
        }

        if not drop:
            return offered

        offered -= drop


def codes(result):
    return sorted(code.value for code in result.codes)


# ----------------------------------------------------------------------
# frozen constants
# ----------------------------------------------------------------------


def test_the_instruction_text_and_its_sha256_are_frozen():
    raw = c.INSTRUCTIONS_TEXT.encode("ascii")

    assert len(raw) == 279
    assert hashlib.sha256(raw).hexdigest() == c.INSTRUCTIONS_SHA256
    assert c.INSTRUCTIONS_SHA256 == (
        "ff48f1c23aecca5b67548c2845df0b7dbf2165af9c271b9212c1d8448fdf87bb"
    )
    assert all(0x20 <= byte <= 0x7E for byte in raw)
    assert c.INSTRUCTIONS_TEXT == (
        "Return exactly one JSON object that conforms to the provided "
        "JSON Schema. Use only the capabilities present in the schema "
        "and do not add fields that the schema does not define. Do not "
        "include digests, server-generated identifiers, credentials or "
        "any text outside the JSON object."
    )
    assert c.PROJECTION_CONTRACT == "proj.v1"
    assert c.INSTRUCTIONS_TEMPLATE_ID == "plan-instructions.v1"
    assert len(raw) <= 1024  # the limit that used to be a parameter


def test_limits_defaults_and_ceilings_are_frozen():
    limits = c.ProjectionLimits()

    assert (limits.max_schema_bytes, limits.max_account_refs) == (12288, 8)
    assert {f.name for f in dataclasses.fields(c.ProjectionLimits)} == {
        "max_schema_bytes",
        "max_account_refs",
    }  # no max_instruction_bytes, no max_capabilities
    assert c.MAX_PLAN_VERSION == 2**53 - 1


def test_rejection_codes_and_stages_are_stable():
    assert sorted(code.value for code in c.ProjectionRejectionCode) == sorted(
        [
            "request_invalid",
            "registry_integrity_failure",
            "authority_context_invalid",
            "policy_reference_missing",
            "policy_reference_invalid",
            "scope_inaccessible",
            "projection_empty",
            "schema_too_large",
            "schema_invalid",
            "projection_text_invalid",
            "internal_term_leak",
            "projection_internal_error",
            "projection_malformed",
            "projection_tampered",
            "projection_stale",
        ]
    )
    assert set(c.STAGE_OF) == set(c.ProjectionRejectionCode)
    assert c.make_rejection(c.ProjectionRejectionCode.REQUEST_INVALID).stage == 0
    assert (
        c.make_rejection(c.ProjectionRejectionCode.PROJECTION_STALE).stage
        == int(c.Stage.VERIFICATION)
    )
    assert sorted(item.value for item in c.ExclusionReason) == sorted(
        [
            "permission_missing",
            "scope_not_readable",
            "scope_not_manageable",
            "policy_mismatch",
            "prerequisite_unavailable",
        ]
    )


def test_the_projection_field_set_is_frozen():
    assert [f.name for f in dataclasses.fields(c.Projection)] == [
        "contract",
        "template_id",
        "registry_digest",
        "context_digest",
        "policy_digest",
        "profile_name",
        "profile_max_output_tokens_cap",
        "max_schema_bytes",
        "max_account_refs",
        "plan_limits",
        "objective_ref",
        "plan_version",
        "account_refs",
        "capability_ids",
        "exclusions",
        "output_schema_json",
        "schema_bytes",
        "schema_digest",
        "instructions",
        "instructions_digest",
        "projection_digest",
        "synthetic_authority",
        "is_grant",
        "replay_protected",
    ]


def test_value_objects_are_frozen_and_results_are_not_booleans():
    projection = ok_projection()
    result = run()

    for target, field in (
        (projection, "capability_ids"),
        (c.ProjectionLimits(), "max_schema_bytes"),
        (base_request(), "objective_ref"),
        (result, "projection"),
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(target, field, None)

    with pytest.raises(TypeError):
        bool(result)
    with pytest.raises(TypeError):
        bool(run(objective_ref="x"))


def test_a_projection_cannot_be_built_with_authority_flags():
    projection = ok_projection()

    for field, value in (
        ("is_grant", True),
        ("replay_protected", True),
        ("synthetic_authority", False),
    ):
        with pytest.raises(ValueError):
            dataclasses.replace(projection, **{field: value})

    with pytest.raises(ValueError):
        c.ProjectionResult(None, ())  # neither projection nor rejections
    with pytest.raises(ValueError):
        c.ProjectionResult(projection, (c.make_rejection(c.ProjectionRejectionCode.REQUEST_INVALID),))


# ----------------------------------------------------------------------
# a valid request
# ----------------------------------------------------------------------


def test_a_valid_request_yields_a_complete_projection():
    projection = ok_projection(account_refs=(REF,))

    assert projection.contract == "proj.v1"
    assert projection.template_id == "plan-instructions.v1"
    assert projection.instructions == c.INSTRUCTIONS_TEXT
    assert projection.registry_digest == PINNED_REGISTRY_DIGEST
    assert projection.capability_ids == PROPOSABLE
    assert projection.exclusions == ()
    assert (projection.synthetic_authority, projection.is_grant, projection.replay_protected) == (True, False, False)
    assert projection.objective_ref == OBJ and projection.plan_version == 1
    assert projection.profile_name == "lab-fast"
    assert projection.profile_max_output_tokens_cap == 512
    assert projection.plan_limits == tuple(sorted(REGISTRY_V1.limits.descriptor().items()))
    assert projection.account_refs == (REF,)
    assert projection.schema_bytes == len(projection.output_schema_json)
    assert projection.schema_bytes <= 12288
    assert projection.context_digest == make_authority().context_digest()
    assert all(
        len(value) == 64
        for value in (
            projection.registry_digest,
            projection.context_digest,
            projection.policy_digest,
            projection.schema_digest,
            projection.instructions_digest,
            projection.projection_digest,
        )
    )


def test_account_refs_are_sorted_and_order_independent():
    authority = make_authority(
        accounts={
            REF: dataclasses.replace(make_authority().accounts[REF]),
            REF2: dataclasses.replace(make_authority().accounts[REF]),
        }
    )
    first = ok_projection(authority=authority, account_refs=(REF, REF2))
    second = ok_projection(authority=authority, account_refs=(REF2, REF))

    assert first.account_refs == tuple(sorted((REF, REF2)))
    assert first == second


def test_invalid_requests_are_a_single_typed_rejection():
    good = base_request()
    bad_requests = [
        None,
        "request",
        {"registry": 1},
        object(),
        dataclasses.replace(good, profile=None),
        dataclasses.replace(good, profile=c.ProfileView("Lab", 512)),
        dataclasses.replace(good, profile=c.ProfileView("lab_fast", 512)),
        dataclasses.replace(good, profile=c.ProfileView("1lab", 512)),
        dataclasses.replace(good, profile=c.ProfileView("a" * 65, 512)),
        dataclasses.replace(good, profile=c.ProfileView("lab-fast\n", 512)),
        dataclasses.replace(good, profile=c.ProfileView("lab-fast", 0)),
        dataclasses.replace(good, profile=c.ProfileView("lab-fast", True)),
        dataclasses.replace(good, profile=c.ProfileView("lab-fast", 10_000_001)),
        dataclasses.replace(good, profile=c.ProfileView("lab-fast", "512")),
        dataclasses.replace(good, profile=c.ProfileView(7, 512)),
        dataclasses.replace(good, objective_ref="obj_short"),
        dataclasses.replace(good, objective_ref="OBJ_K3M9X2AB"),
        dataclasses.replace(good, objective_ref=OBJ + "\n"),
        dataclasses.replace(good, objective_ref="obj_k3m9x2á"),
        dataclasses.replace(good, objective_ref=None),
        dataclasses.replace(good, plan_version=0),
        dataclasses.replace(good, plan_version=True),
        dataclasses.replace(good, plan_version=2**53),
        dataclasses.replace(good, plan_version=1.0),
        dataclasses.replace(good, plan_version="1"),
        dataclasses.replace(good, account_refs=[REF]),
        dataclasses.replace(good, account_refs=()),
        dataclasses.replace(good, account_refs=(REF, REF)),
        dataclasses.replace(good, account_refs=("acct_",)),
        dataclasses.replace(good, account_refs=(REF + "\n",)),
        dataclasses.replace(good, account_refs=("acct_éabc1",)),
        dataclasses.replace(good, account_refs=(REF, 5)),
        dataclasses.replace(good, account_refs=tuple(f"acct_aaaa{i:02d}" for i in range(9))),
        dataclasses.replace(good, limits=None),
        dataclasses.replace(good, limits=c.ProjectionLimits(0, 8)),
        dataclasses.replace(good, limits=c.ProjectionLimits(True, 8)),
        dataclasses.replace(good, limits=c.ProjectionLimits(1_048_577, 8)),
        dataclasses.replace(good, limits=c.ProjectionLimits(12288, 0)),
        dataclasses.replace(good, limits=c.ProjectionLimits(12288, 65)),
        dataclasses.replace(good, pinned_registry_digest="abc"),
        dataclasses.replace(good, pinned_registry_digest=PINNED_REGISTRY_DIGEST.upper()),
        dataclasses.replace(good, pinned_registry_digest=7),
    ]

    for request in bad_requests:
        result = project(request)

        assert not result.ok
        assert result.codes == (c.ProjectionRejectionCode.REQUEST_INVALID,)
        assert result.rejections[0].stage == 0


def test_the_output_schema_is_canonical_text_with_a_fresh_copy_per_access():
    projection = ok_projection()
    first = projection.output_schema

    first["properties"]["steps"]["maxItems"] = 999
    first["extra"] = True
    second = projection.output_schema

    assert second["properties"]["steps"]["maxItems"] == 8 and "extra" not in second
    assert first is not second
    assert canonical_bytes(second).decode("ascii") == projection.output_schema_json
    assert isinstance(projection.output_schema_json, str)
    assert json.loads(projection.output_schema_json) == second


def test_the_audit_view_is_content_free():
    projection = ok_projection(
        authority=make_authority(initiator_ref="user:CANARY-9f2c1d")
    )
    view = projection.audit_view()
    rendered = json.dumps(view)

    assert set(view) == {
        "contract",
        "registry_digest",
        "context_digest",
        "policy_digest",
        "projection_digest",
        "capability_count",
        "exclusion_reasons",
        "schema_bytes",
        "synthetic_authority",
        "is_grant",
        "replay_protected",
    }
    for forbidden in ("CANARY", OBJ, REF, projection.output_schema_json[:60]):
        assert forbidden not in rendered


def test_the_projection_is_deterministic_over_many_repetitions():
    request = base_request()
    first = project(request).projection

    for _ in range(scaled(1000)):
        assert project(request).projection == first

    assert project(request).projection.projection_digest == first.projection_digest


def test_changing_the_inputs_after_project_does_not_change_the_projection():
    registry = build_v1_registry()
    policy = build_real_policy_reference()
    authority = make_authority()
    request = base_request(
        registry=registry, policy=policy, authority=authority,
        pinned_registry_digest=registry.digest,
    )
    projection = project(request).projection
    frozen_copy = copy.deepcopy(projection)

    registry.get("account.get_context", 1).input_schema["properties"]["account_ref"]["pattern"] = ".*"
    policy.actions["account.mark_overdue"] = dataclasses.replace(
        policy.actions["account.mark_overdue"], execution_corridor="x"
    )
    authority.accounts["acct_zzzzzz"] = dataclasses.replace(authority.accounts[REF])

    assert projection == frozen_copy
    assert projection.output_schema == frozen_copy.output_schema
    # and the changes ARE noticed when the same request is verified again
    assert [r.code.value for r in verify_projection(projection, request)] == [
        "projection_stale"
    ]


# ----------------------------------------------------------------------
# exact boundaries of every field
# ----------------------------------------------------------------------


def test_field_boundaries_are_exact():
    from test_intelligence_plan_validator import make_authority as authority_for
    from app.intelligence.planning.authority import AccountAccess

    for size in (8, 32):
        assert run(objective_ref="obj_" + "a" * size).ok
    for size in (7, 33):
        assert codes(run(objective_ref="obj_" + "a" * size)) == ["request_invalid"]

    for size in (6, 12):
        ref = "acct_" + "b" * size
        authority = authority_for(accounts={ref: AccountAccess(True, True)})
        assert run(authority=authority, account_refs=(ref,)).ok
    for size in (5, 13):
        ref = "acct_" + "b" * size
        authority = authority_for(accounts={ref: AccountAccess(True, True)})
        assert codes(run(authority=authority, account_refs=(ref,))) == ["request_invalid"]

    for name in ("a", "a" * 64, "a-1"):
        assert run(profile=c.ProfileView(name, 512)).ok
    for name in ("", "a" * 65, "-a", "a_b"):
        assert codes(run(profile=c.ProfileView(name, 512))) == ["request_invalid"]

    for version in (1, 2**53 - 1):
        assert run(plan_version=version).ok
    for version in (0, -1, 2**53):
        assert codes(run(plan_version=version)) == ["request_invalid"]

    for cap in (1, 10_000_000):
        assert run(profile=c.ProfileView("lab-fast", cap)).ok
    for cap in (0, 10_000_001):
        assert codes(run(profile=c.ProfileView("lab-fast", cap))) == ["request_invalid"]

    assert run(limits=c.ProjectionLimits(1_048_576, 8)).ok
    assert codes(run(limits=c.ProjectionLimits(1_048_577, 8))) == ["request_invalid"]
    assert codes(run(limits=c.ProjectionLimits(1, 8))) == ["schema_too_large"]  # valid request
    assert run(limits=c.ProjectionLimits(12288, 64)).ok
    assert codes(run(limits=c.ProjectionLimits(12288, 65))) == ["request_invalid"]

    refs = ("acct_aaaaaa", "acct_bbbbbb")
    both = authority_for(accounts={ref: AccountAccess(True, True) for ref in refs})
    assert run(authority=both, account_refs=refs, limits=c.ProjectionLimits(12288, 2)).ok
    assert codes(run(authority=both, account_refs=refs, limits=c.ProjectionLimits(12288, 1))) == ["request_invalid"]


def test_every_rejection_code_has_its_exact_stage():
    expected = {
        "request_invalid": 0,
        "registry_integrity_failure": 1,
        "authority_context_invalid": 2,
        "policy_reference_missing": 3,
        "policy_reference_invalid": 3,
        "scope_inaccessible": 4,
        "projection_empty": 5,
        "schema_too_large": 6,
        "schema_invalid": 6,
        "projection_text_invalid": 6,
        "internal_term_leak": 6,
        "projection_internal_error": 7,
        "projection_malformed": 8,
        "projection_tampered": 8,
        "projection_stale": 8,
    }

    assert {code.value: int(stage) for code, stage in c.STAGE_OF.items()} == expected
    with pytest.raises(TypeError):
        c.STAGE_OF[c.ProjectionRejectionCode.REQUEST_INVALID] = c.Stage.SCOPE  # read-only


def test_a_rejection_is_one_stage_per_run_and_sorted():
    result = run(plan_version=0)

    assert [r.stage for r in result.rejections] == [0]
    assert result.rejections == tuple(sorted(result.rejections))
