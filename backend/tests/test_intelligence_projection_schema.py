"""INTELLIGENCE-PROPOSAL -- projected schema, instructions, vocabulary."""

import copy
import dataclasses
import hashlib
import itertools
import json

import pytest
from jsonschema import Draft202012Validator

from app.intelligence.planning.canonical import canonical_bytes
from app.intelligence.planning.capabilities import PINNED_REGISTRY_DIGEST
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.capabilities import schema_is_closed
from app.intelligence.planning.schema import build_plan_schema
from app.intelligence.planning.validator import validate_plan
from app.intelligence.proposal import rendering
from app.intelligence.proposal.contracts import INSTRUCTIONS_TEXT

from test_intelligence_plan_validator import build_real_policy_reference
from test_intelligence_plan_validator import dumps
from test_intelligence_plan_validator import good_plan
from test_intelligence_plan_validator import make_authority
from test_intelligence_plan_validator import read_only_plan
from test_intelligence_plan_validator import step_of
from test_intelligence_projection_contracts import OBJ
from test_intelligence_projection_contracts import PROPOSABLE
from test_intelligence_projection_contracts import closure
from test_intelligence_projection_contracts import codes
from test_intelligence_projection_contracts import ok_projection
from test_intelligence_projection_contracts import run
from test_intelligence_projection_contracts import subset_registry


pytestmark = pytest.mark.security

GOLDEN_FULL_SCHEMA_SHA256 = (
    "652d12821936de78d404bc1c57187e210bc06748875dc7fd70987876fc40ae51"
)


def all_subsets():
    for size in range(1, 6):
        yield from itertools.combinations(PROPOSABLE, size)


def project_subset(subset, **overrides):
    registry = subset_registry(subset)
    return run(registry=registry, pinned_registry_digest=registry.digest, **overrides)


def variants(schema):
    return {
        item["properties"]["capability_id"]["const"]: item
        for item in schema["properties"]["steps"]["items"]["anyOf"]
    }


# ----------------------------------------------------------------------
# the 31 subsets of proposable capabilities
# ----------------------------------------------------------------------


def test_every_subset_projects_a_closed_valid_schema_identical_to_the_registrys():
    full = variants(build_plan_schema(REGISTRY_V1))
    seen = 0

    for subset in all_subsets():
        result = project_subset(subset)
        expected = closure(subset)

        if not expected:
            assert codes(result) == ["projection_empty"]
            continue

        projection = result.projection
        schema = projection.output_schema
        offered = variants(schema)

        assert set(offered) == expected == set(projection.capability_ids)
        assert all(offered[name] == full[name] for name in offered)
        Draft202012Validator.check_schema(schema)
        assert schema_is_closed(schema)
        assert projection.schema_bytes <= 12288
        assert projection.registry_digest == subset_registry(subset).digest
        assert projection.schema_bytes == len(canonical_bytes(schema))
        for hidden in ("account.mark_paid", "work.escalate_to_human"):
            assert hidden not in projection.output_schema_json
        seen += 1

    assert seen == 30  # one subset (mark_overdue alone) is empty


def test_everything_except_the_variants_is_the_registrys_own_schema():
    base = copy.deepcopy(build_plan_schema(REGISTRY_V1))

    for subset in all_subsets():
        result = project_subset(subset)

        if not result.ok:
            continue

        schema = result.projection.output_schema
        for document in (schema, base):
            document["properties"]["steps"]["items"]["anyOf"] = []
        for name in ("objective_ref", "plan_version"):
            schema["properties"].pop(name)
        reference = copy.deepcopy(base)
        for name in ("objective_ref", "plan_version"):
            reference["properties"].pop(name)

        assert schema == reference


def test_the_full_projection_is_the_registry_schema_with_two_pins():
    projection = ok_projection()
    schema = projection.output_schema
    full = build_plan_schema(REGISTRY_V1)

    assert schema["properties"]["objective_ref"] == {"const": OBJ}
    assert schema["properties"]["plan_version"] == {"const": 1}
    schema["properties"]["objective_ref"] = full["properties"]["objective_ref"]
    schema["properties"]["plan_version"] = full["properties"]["plan_version"]
    assert schema == full
    assert projection.registry_digest == PINNED_REGISTRY_DIGEST


def test_the_pins_bind_the_plan_to_the_objective_and_version():
    validator = Draft202012Validator(ok_projection().output_schema)
    plan = good_plan()

    assert validator.is_valid(plan)
    assert not validator.is_valid({**plan, "objective_ref": "obj_zzzzzzzz"})
    assert not validator.is_valid({**plan, "plan_version": 2})
    # the validator knows nothing about the pin: the pipeline must re-check
    assert validate_plan(
        dumps({**plan, "objective_ref": "obj_zzzzzzzz"}),
        registry=REGISTRY_V1,
        policy=build_real_policy_reference(),
        authority=make_authority(),
    ).admitted


# ----------------------------------------------------------------------
# the projection guides; the full validator decides
# ----------------------------------------------------------------------


def test_a_projected_schema_is_guidance_not_authority():
    policy, authority = build_real_policy_reference(), make_authority()

    def admitted(plan):
        return validate_plan(
            dumps(plan), registry=REGISTRY_V1, policy=policy, authority=authority
        ).admitted

    one = Draft202012Validator(
        project_subset(("account.get_context",)).projection.output_schema
    )
    full = Draft202012Validator(ok_projection().output_schema)

    # admitted by the full validator, invalid under the narrow projection
    assert admitted(good_plan()) and not one.is_valid(good_plan())

    # valid under the projection, refused by the full validator
    plan = good_plan()
    step_of(plan, "mark")["preconditions"] = []
    assert full.is_valid(plan) and not admitted(plan)


def test_a_sub_registry_can_never_stand_in_for_the_registry():
    sub = subset_registry(PROPOSABLE[:2])
    result = validate_plan(
        dumps(read_only_plan()),
        registry=sub,
        policy=build_real_policy_reference(),
        authority=make_authority(),
    )

    assert not result.admitted
    assert {v.code for v in result.violations} == {"registry_integrity_failure"}

    projection = project_subset(PROPOSABLE[:2]).projection
    assert all(
        not isinstance(getattr(projection, f.name), CapabilityRegistry)
        for f in dataclasses.fields(projection)
    )


def test_the_regression_goldens_catch_a_change_in_the_planning_contract():
    schema = build_plan_schema(REGISTRY_V1)

    assert hashlib.sha256(canonical_bytes(schema)).hexdigest() == GOLDEN_FULL_SCHEMA_SHA256
    assert sorted(schema) == ["$defs", "$schema", "additionalProperties", "properties", "required", "type"]
    assert sorted(schema["properties"]) == [
        "contract", "objective_ref", "plan_version", "registry_version", "steps", "summary",
    ]
    for item in variants(schema).values():
        assert sorted(item) == ["additionalProperties", "properties", "required", "type"]
        assert sorted(item["properties"]) == [
            "capability_id", "capability_version", "depends_on", "expected_outcomes",
            "on_failure", "params", "preconditions", "rationale", "step_key",
        ]
    assert REGISTRY_V1.digest == PINNED_REGISTRY_DIGEST
    assert REGISTRY_V1.limits.descriptor() == {
        "max_dependencies": 3, "max_depth": 4, "max_expected_outcomes": 4, "max_fan_out": 3,
        "max_integer": 9007199254740991, "max_json_bytes": 32768, "max_json_depth": 10,
        "max_mutating_steps": 1, "max_preconditions": 4, "max_rationale_chars": 400,
        "max_steps": 8, "max_summary_chars": 280,
    }


# ----------------------------------------------------------------------
# size limits
# ----------------------------------------------------------------------


def test_the_schema_size_limit_has_an_exact_boundary():
    size = ok_projection().schema_bytes

    assert size == 6222
    assert run(limits=_limits(size)).ok
    assert codes(run(limits=_limits(size - 1))) == ["schema_too_large"]
    assert codes(run(limits=_limits(1))) == ["schema_too_large"]


def _limits(max_schema_bytes):
    from app.intelligence.proposal.contracts import ProjectionLimits

    return ProjectionLimits(max_schema_bytes=max_schema_bytes, max_account_refs=8)


# ----------------------------------------------------------------------
# vocabulary: internal VALUES are forbidden, generic language is not
# ----------------------------------------------------------------------


def forbidden_for(selected):
    policy = build_real_policy_reference()

    return rendering.forbidden_identifiers(
        REGISTRY_V1.definitions, selected, policy.known_permissions
    )


def test_the_frozen_instructions_pass_their_own_filter_for_every_subset():
    for subset in all_subsets():
        forbidden = forbidden_for(subset)
        schema = build_plan_schema(subset_registry(subset))

        assert not rendering.has_leak(schema, INSTRUCTIONS_TEXT, forbidden), subset

    compound = [t for t in INSTRUCTIONS_TEXT.replace(",", " ").replace(".", " ").split() if any(m in t for m in "_.:/")]
    assert compound == []
    for generic in ("digests", "credentials", "server-generated", "identifiers"):
        assert generic in INSTRUCTIONS_TEXT


def test_the_scanner_detects_every_class_of_internal_value():
    forbidden = forbidden_for(PROPOSABLE)
    clean = build_plan_schema(REGISTRY_V1)
    injected = {
        "excluded id": "see work.escalate_to_human",
        "disabled id": "account.mark_paid",
        "reference key": "escalate_to_human",
        "corridor": "advisory_approval_work_skill",
        "permission": "clients.manage",
        "approver permission": "approval:decide",
        "separation token": "initiator_ne_decider",
        "approval kind": "PER_STEP_APPROVAL",
        "digest field": "registry_digest",
        "opaque hex": "a" * 40,
        "opaque run": "Zm9v" * 12,
        "case variant": "WORK.ESCALATE_TO_HUMAN",
    }

    assert not rendering.has_leak(clean, INSTRUCTIONS_TEXT, forbidden)
    for label, text in injected.items():
        assert rendering.has_leak(clean, INSTRUCTIONS_TEXT + " " + text, forbidden), label
        polluted = copy.deepcopy(clean)
        polluted["properties"]["summary"]["x-note"] = text
        assert rendering.has_leak(polluted, INSTRUCTIONS_TEXT, forbidden), label

    keyed = copy.deepcopy(clean)
    keyed["properties"]["executable"] = {"type": "boolean"}
    assert rendering.has_leak(keyed, INSTRUCTIONS_TEXT, forbidden)
    # generic prose is not an internal value
    assert not rendering.has_leak(clean, INSTRUCTIONS_TEXT + " executable approval permission", forbidden)


def test_a_projected_capability_id_is_public_even_if_it_is_also_an_internal_key():
    # the reference key of account.mark_overdue is the same text as its id
    assert REGISTRY_V1.get("account.mark_overdue", 1).reference_key == "account.mark_overdue"
    assert "account.mark_overdue" not in forbidden_for(PROPOSABLE)
    assert "account.mark_overdue" in forbidden_for(tuple(i for i in PROPOSABLE if i != "account.mark_overdue"))


def custom_registry(mutator):
    definitions = []

    for definition in REGISTRY_V1.definitions:
        if definition.capability_id == "memory.recall":
            schema = copy.deepcopy(dict(definition.input_schema))
            mutator(schema)
            definition = dataclasses.replace(definition, input_schema=schema)
        definitions.append(definition)

    return CapabilityRegistry(tuple(definitions))


def test_internal_values_hidden_in_a_registry_are_caught_at_projection():
    cases = {
        "excluded id": lambda s: s.update(title="work.escalate_to_human"),
        "permission": lambda s: s.update(title="approval:decide"),
        "corridor": lambda s: s["properties"]["query"].update(title="direct_approval_skill"),
        "hex": lambda s: s.update(title="f" * 40),
        "case": lambda s: s.update(title="ACCOUNT.MARK_PAID"),
    }

    for label, mutator in cases.items():
        registry = custom_registry(mutator)
        result = run(registry=registry, pinned_registry_digest=registry.digest)

        assert codes(result) == ["internal_term_leak"], label


def test_non_ascii_text_is_rejected_whatever_it_spells():
    # NFC non-ASCII text can reach a registry; NFD and control characters
    # cannot even be digested by it (see the last assertion).
    spellings = (
        "caf\u00e9",
        "\uff57ork",
        "a\u200bb",
        "\u202etxt",
        "\u00a0",
        "\u2028",
    )

    for text in spellings:
        registry = custom_registry(lambda s, t=text: s.update(title=t))
        result = run(registry=registry, pinned_registry_digest=registry.digest)

        assert codes(result) == ["projection_text_invalid"], repr(text)

    for text in ("e\u0301", "tab\t", "line\n"):
        with pytest.raises(Exception):
            custom_registry(lambda s, t=text: s.update(title=t))


def test_defensive_schema_failures_are_typed(monkeypatch):
    monkeypatch.setattr(rendering, "build_plan_schema", lambda *a, **k: {"type": 5})
    assert codes(run()) == ["schema_invalid"]

    monkeypatch.setattr(
        rendering, "build_plan_schema", lambda *a, **k: {"type": "object", "properties": {"objective_ref": {}, "plan_version": {}}}
    )
    assert codes(run()) == ["schema_invalid"]  # an open object schema is refused

    broken = copy.deepcopy(build_plan_schema(REGISTRY_V1))
    broken["properties"]["summary"]["maxLength"] = float("nan")
    monkeypatch.setattr(rendering, "build_plan_schema", lambda *a, **k: broken)
    assert codes(run()) == ["schema_invalid"]


def test_the_projected_schema_is_ascii_and_has_no_descriptive_text():
    projection = ok_projection()
    keys, values = rendering.walk_strings(projection.output_schema)

    assert all(rendering.is_printable_ascii(text) for text in keys + values)
    assert "description" not in keys
    assert json.dumps(projection.output_schema, sort_keys=True, separators=(",", ":")) == projection.output_schema_json


def test_a_schema_that_only_the_metaschema_can_refuse_is_still_refused(monkeypatch):
    broken = copy.deepcopy(build_plan_schema(REGISTRY_V1))
    broken["properties"]["summary"]["maxLength"] = -1  # closed, pinned, ASCII, but invalid
    monkeypatch.setattr(rendering, "build_plan_schema", lambda *a, **k: broken)

    assert codes(run()) == ["schema_invalid"]


def registry_with(*, hidden_read=None, schema_edit=None, on="account.get_context"):
    """Custom registry: optionally make one read capability non-proposable
    and edit the input schema of another."""
    definitions = []

    for definition in REGISTRY_V1.definitions:
        if definition.capability_id == hidden_read:
            definition = dataclasses.replace(definition, llm_proposable=False)
        if schema_edit is not None and definition.capability_id == on:
            schema = copy.deepcopy(dict(definition.input_schema))
            schema_edit(schema)
            definition = dataclasses.replace(definition, input_schema=schema)
        definitions.append(definition)

    return CapabilityRegistry(tuple(definitions))


def leaks(registry, policy=None):
    from test_intelligence_projection_contracts import base_request
    from app.intelligence.proposal.builder import project

    request = base_request(
        registry=registry,
        pinned_registry_digest=registry.digest,
        policy=policy or build_real_policy_reference(),
    )

    return codes(project(request)) == ["internal_term_leak"]


def test_the_id_of_any_non_projected_capability_is_forbidden_text():
    # memory.recall is a read capability with no action reference: only its
    # id can reveal it
    registry = registry_with(
        hidden_read="memory.recall",
        schema_edit=lambda s: s.update(title="memory.recall"),
    )

    assert leaks(registry)
    # while the same text is public when that capability IS projected
    public = registry_with(schema_edit=lambda s: s.update(title="memory.recall"))
    from test_intelligence_projection_contracts import base_request
    from app.intelligence.proposal.builder import project

    assert project(base_request(registry=public, pinned_registry_digest=public.digest)).ok


def test_permission_strings_are_forbidden_from_both_sources():
    policy = build_real_policy_reference()

    # only in the policy vocabulary
    extra = dataclasses.replace(policy, known_permissions=policy.known_permissions | {"extra:perm"})
    assert leaks(registry_with(schema_edit=lambda s: s.update(title="extra:perm")), extra)

    # only in a definition: the policy no longer lists it
    fewer = dataclasses.replace(policy, known_permissions=policy.known_permissions - {"clients.manage"})
    assert leaks(registry_with(schema_edit=lambda s: s.update(title="clients.manage")), fewer)


def test_property_names_are_scanned_as_text_too():
    registry = registry_with(
        on="memory.recall",
        schema_edit=lambda s: s["properties"].update({"clients.manage": {"type": "string", "maxLength": 3}}),
    )

    assert leaks(registry)
