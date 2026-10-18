"""INTELLIGENCE-PLANNING -- immutable, versioned Capability Registry."""

import copy
import dataclasses

import pytest

from app.intelligence.planning import capabilities as caps
from app.intelligence.planning.capabilities import PINNED_REGISTRY_DIGEST
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.capabilities import ApprovalKind
from app.intelligence.planning.capabilities import ApprovalPolicy
from app.intelligence.planning.capabilities import CapabilityDefinition
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.capabilities import EffectClass
from app.intelligence.planning.capabilities import build_v1_registry
from app.intelligence.planning.capabilities import schema_is_closed
from app.intelligence.planning.limits import PLAN_LIMITS

from test_intelligence_plan_validator import good_plan
from test_intelligence_plan_validator import policy  # noqa: F401
from test_intelligence_plan_validator import validate


pytestmark = pytest.mark.contract


def definition(**overrides):
    base = {
        "capability_id": "demo.read",
        "version": 1,
        "registered": True,
        "enabled": True,
        "llm_proposable": True,
        "executable": False,
        "effect_class": EffectClass.READ,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {},
        },
        "output_schema": {"type": "object"},
        "required_permissions": ("approval:read",),
        "approval_policy": ApprovalPolicy(ApprovalKind.NONE),
    }
    base.update(overrides)
    return CapabilityDefinition(**base)


def test_the_v1_registry_has_exactly_the_authorized_capabilities():
    by_id = {d.capability_id: d for d in REGISTRY_V1.definitions}

    assert set(by_id) == {
        "account.get_context",
        "account.mark_overdue",
        "account.mark_paid",
        "action_space.evaluate",
        "memory.recall",
        "nba.evaluate",
        "work.escalate_to_human",
    }
    assert REGISTRY_V1.version == "capreg.v1"
    assert all(d.version == 1 and d.registered for d in by_id.values())


def test_no_capability_is_executable():
    assert caps.EXECUTION_ENABLED_IN_THIS_PHASE is False
    assert not any(d.executable for d in REGISTRY_V1.definitions)

    with pytest.raises(ValueError):
        definition(executable=True)


def test_mandatory_policy_flags():
    by_id = {d.capability_id: d for d in REGISTRY_V1.definitions}

    paid = by_id["account.mark_paid"]
    assert (paid.enabled, paid.llm_proposable) == (False, False)

    escalate = by_id["work.escalate_to_human"]
    assert escalate.enabled is True and escalate.llm_proposable is False
    assert escalate.reference_key == "escalate_to_human"

    overdue = by_id["account.mark_overdue"]
    assert overdue.llm_proposable and overdue.enabled
    assert overdue.approval_policy.kind is ApprovalKind.PER_STEP_APPROVAL
    assert overdue.approval_policy.approver_permission == "approval:decide"
    assert overdue.separation_requirements == ("initiator_ne_decider",)
    assert len(overdue.prerequisites) == 1

    for read in ("account.get_context", "nba.evaluate", "action_space.evaluate", "memory.recall"):
        assert by_id[read].effect_class is EffectClass.READ
        assert by_id[read].approval_policy.kind is ApprovalKind.NONE
        assert by_id[read].reference_key is None


def test_eligibility_prerequisite_is_the_authorized_one():
    (rule,) = REGISTRY_V1.get("account.mark_overdue", 1).prerequisites

    assert set(rule.any_of) == {"action_space.evaluate", "nba.evaluate"}
    assert set(rule.bind) == {"account_ref", "due_date"}
    assert rule.required_precondition == ("episode.eligible_mark_overdue", "eq", True)


def test_every_input_schema_is_closed_and_draft_valid():
    for d in REGISTRY_V1.definitions:
        assert schema_is_closed(d.input_schema)
        assert d.validator.is_valid(_minimal_valid_params(d.capability_id))

    assert not schema_is_closed({"type": "object"})
    assert not schema_is_closed(
        {"type": "object", "additionalProperties": False, "properties": {"x": {"type": "object"}}}
    )


def _minimal_valid_params(capability_id):
    if capability_id == "memory.recall":
        return {"account_ref": "acct_7f3a9c", "query": "q", "limit": 3}
    if capability_id == "account.get_context":
        return {"account_ref": "acct_7f3a9c"}
    return {"account_ref": "acct_7f3a9c", "due_date": "2026-09-30"}


def test_registry_construction_invariants():
    with pytest.raises(ValueError):
        definition(capability_id="Bad Id")
    with pytest.raises(ValueError):
        definition(version=0)
    with pytest.raises(ValueError):
        definition(version=True)
    with pytest.raises(ValueError):
        definition(registered=False)  # enabled/proposable but unregistered
    with pytest.raises(ValueError):
        definition(enabled=False)  # proposable but disabled
    with pytest.raises(ValueError):
        definition(reference_key="x")  # reads have no action reference
    with pytest.raises(ValueError):
        definition(approval_policy=ApprovalPolicy(ApprovalKind.RBAC_ONLY))
    with pytest.raises(ValueError):
        definition(effect_class=EffectClass.MUTATE)  # mutate needs approval
    with pytest.raises(ValueError):
        definition(input_schema={"type": "object"})  # open schema
    with pytest.raises(ValueError):
        definition(required_permissions=("b", "a"))  # unsorted
    with pytest.raises(ValueError):
        definition(required_permissions=("a", "a"))  # not unique
    with pytest.raises(ValueError):
        ApprovalPolicy(ApprovalKind.PER_STEP_APPROVAL)
    with pytest.raises(ValueError):
        ApprovalPolicy(ApprovalKind.NONE, "approval:decide")

    one = definition()
    with pytest.raises(ValueError):
        CapabilityRegistry((one, definition()))  # duplicate id+version


def test_definitions_are_frozen():
    target = REGISTRY_V1.get("account.mark_overdue", 1)

    with pytest.raises(dataclasses.FrozenInstanceError):
        target.enabled = False
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.executable = True
    assert isinstance(REGISTRY_V1.definitions, tuple)
    assert not hasattr(REGISTRY_V1, "register")
    assert not hasattr(REGISTRY_V1, "add")


def test_lookup_is_exact_by_id_and_version():
    assert REGISTRY_V1.get("account.mark_overdue", 1) is not None
    assert REGISTRY_V1.get("account.mark_overdue", 2) is None
    assert REGISTRY_V1.get("ACCOUNT.MARK_OVERDUE", 1) is None
    assert REGISTRY_V1.versions_of("nba.evaluate") == (1,)
    assert REGISTRY_V1.versions_of("nope") == ()
    assert list(REGISTRY_V1.ids()) == sorted(REGISTRY_V1.ids())


def test_the_digest_is_stable_pinned_and_independent_of_build_order():
    rebuilt = build_v1_registry()
    reversed_registry = CapabilityRegistry(
        tuple(reversed(REGISTRY_V1.definitions))
    )

    assert rebuilt.digest == REGISTRY_V1.digest == PINNED_REGISTRY_DIGEST
    assert reversed_registry.digest == REGISTRY_V1.digest
    assert len(PINNED_REGISTRY_DIGEST) == 64


def _registry_with(mutator):
    definitions = []

    for d in REGISTRY_V1.definitions:
        replacement = mutator(d)
        definitions.append(replacement if replacement is not None else d)

    return CapabilityRegistry(tuple(definitions))


def test_any_registry_change_moves_the_digest():
    changes = {
        "enable_mark_paid": lambda d: dataclasses.replace(d, enabled=True)
        if d.capability_id == "account.mark_paid"
        else None,
        "proposable_escalate": lambda d: dataclasses.replace(d, llm_proposable=True)
        if d.capability_id == "work.escalate_to_human"
        else None,
        "drop_prerequisite": lambda d: dataclasses.replace(d, prerequisites=())
        if d.capability_id == "account.mark_overdue"
        else None,
        "drop_separation": lambda d: dataclasses.replace(d, separation_requirements=())
        if d.capability_id == "account.mark_overdue"
        else None,
        "permission": lambda d: dataclasses.replace(
            d, required_permissions=("approval:read",)
        )
        if d.capability_id == "nba.evaluate"
        else None,
        "schema": lambda d: dataclasses.replace(
            d, input_schema=_looser(d.input_schema)
        )
        if d.capability_id == "memory.recall"
        else None,
    }

    for name, mutator in changes.items():
        assert _registry_with(mutator).digest != PINNED_REGISTRY_DIGEST, name


def _looser(schema):
    schema = copy.deepcopy(dict(schema))
    schema["properties"]["limit"]["maximum"] = 1000
    return schema


def test_a_tampered_registry_rejects_every_plan_and_names_no_value(policy):  # noqa: F811
    tampered = _registry_with(
        lambda d: dataclasses.replace(d, enabled=True)
        if d.capability_id == "account.mark_paid"
        else None
    )

    admission = validate(good_plan(), policy, registry=tampered)

    assert [v.code for v in admission.violations] == ["registry_integrity_failure"]
    assert admission.execution_digest is None


def test_limits_are_part_of_the_registry_identity():
    other = CapabilityRegistry(
        REGISTRY_V1.definitions,
        limits=dataclasses.replace(PLAN_LIMITS, max_steps=9),
    )

    assert other.digest != REGISTRY_V1.digest
    assert PLAN_LIMITS.max_steps == 8 and PLAN_LIMITS.max_mutating_steps == 1
