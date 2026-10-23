"""INTELLIGENCE-PROPOSAL -- capability selection: filters and closure."""

import dataclasses
import itertools
import random

import pytest

from app.intelligence.planning.authority import AccountAccess
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.proposal import contracts as c
from app.intelligence.proposal.selection import candidates

from test_intelligence_plan_validator import ALL_PERMISSIONS
from test_intelligence_plan_validator import build_real_policy_reference
from test_intelligence_plan_validator import make_authority
from test_intelligence_projection_contracts import PROPOSABLE
from test_intelligence_projection_contracts import REF
from test_intelligence_projection_contracts import REF2
from test_intelligence_projection_contracts import closure
from test_intelligence_projection_contracts import codes
from test_intelligence_projection_contracts import ok_projection
from test_intelligence_projection_contracts import run
from test_intelligence_projection_contracts import scaled
from test_intelligence_projection_contracts import subset_registry


pytestmark = pytest.mark.security

READ_ONLY_SCOPE = {REF: AccountAccess(readable=True, manageable=False)}
MANAGE_ONLY_SCOPE = {REF: AccountAccess(readable=False, manageable=True)}


def excluded(projection):
    return {(e.capability_id, e.reason.value) for e in projection.exclusions}


def test_only_enabled_proposable_non_executable_capabilities_are_candidates():
    assert tuple(d.capability_id for d in candidates(REGISTRY_V1)) == PROPOSABLE

    for hidden in ("account.mark_paid", "work.escalate_to_human"):
        definition = REGISTRY_V1.get(hidden, 1)
        assert not (definition.enabled and definition.llm_proposable)


def test_forbidden_capabilities_appear_nowhere_in_a_projection():
    projection = ok_projection()
    haystack = " ".join(
        [
            projection.output_schema_json,
            projection.instructions,
            " ".join(projection.capability_ids),
            " ".join(e.capability_id for e in projection.exclusions),
        ]
    )

    for hidden in ("account.mark_paid", "work.escalate_to_human", "escalate_to_human", "mark_paid"):
        assert hidden not in haystack


def test_full_authority_offers_every_proposable_capability():
    projection = ok_projection()

    assert projection.capability_ids == PROPOSABLE
    assert projection.exclusions == ()


def test_each_missing_permission_has_its_exact_effect():
    expected = {
        "memory:read": {("memory.recall", "permission_missing")},
        "clients.manage": {("account.mark_overdue", "permission_missing")},
        "skill:execute_mutating": {("account.mark_overdue", "permission_missing")},
        "work:create": set(),
        "clients.view": {
            ("account.get_context", "permission_missing"),
            ("nba.evaluate", "permission_missing"),
            ("action_space.evaluate", "permission_missing"),
            ("memory.recall", "permission_missing"),
            ("account.mark_overdue", "prerequisite_unavailable"),
        },
        "approval:read": {
            ("account.get_context", "permission_missing"),
            ("nba.evaluate", "permission_missing"),
            ("action_space.evaluate", "permission_missing"),
            ("account.mark_overdue", "prerequisite_unavailable"),
        },
    }

    for permission, reasons in expected.items():
        authority = make_authority(permissions=ALL_PERMISSIONS - {permission})
        result = run(authority=authority)

        if permission == "clients.view":
            assert codes(result) == ["projection_empty"], permission
            continue

        assert result.ok, permission
        assert excluded(result.projection) == reasons, permission


def test_precedence_of_filters_is_permission_then_scope_then_policy():
    # lacks the permission AND the scope: the permission reason wins
    authority = make_authority(
        permissions=ALL_PERMISSIONS - {"clients.manage"},
        accounts=READ_ONLY_SCOPE,
    )
    projection = ok_projection(authority=authority)

    assert ("account.mark_overdue", "permission_missing") in excluded(projection)
    assert ("account.mark_overdue", "scope_not_manageable") not in excluded(projection)

    # has permission, lacks scope AND policy agreement: the scope reason wins
    policy = build_real_policy_reference()
    policy.actions["account.mark_overdue"] = dataclasses.replace(
        policy.actions["account.mark_overdue"], execution_corridor="other"
    )
    projection = ok_projection(
        authority=make_authority(accounts=READ_ONLY_SCOPE), policy=policy
    )

    assert ("account.mark_overdue", "scope_not_manageable") in excluded(projection)


def test_scope_rules_for_read_and_write():
    projection = ok_projection(authority=make_authority(accounts=READ_ONLY_SCOPE))

    assert projection.capability_ids == tuple(i for i in PROPOSABLE if i != "account.mark_overdue")
    assert excluded(projection) == {("account.mark_overdue", "scope_not_manageable")}

    # manage-only: no read capability is usable, so the mutating one has no
    # evidence capability and the whole projection is empty
    result = run(authority=make_authority(accounts=MANAGE_ONLY_SCOPE))

    assert codes(result) == ["projection_empty"]


def test_mixed_references_offer_writes_only_when_all_are_manageable():
    mixed = make_authority(
        accounts={
            REF: AccountAccess(readable=True, manageable=True),
            REF2: AccountAccess(readable=True, manageable=False),
        }
    )
    projection = ok_projection(authority=mixed, account_refs=(REF, REF2))

    assert "account.mark_overdue" not in projection.capability_ids
    assert excluded(projection) == {("account.mark_overdue", "scope_not_manageable")}

    both = make_authority(
        accounts={
            REF: AccountAccess(True, True),
            REF2: AccountAccess(True, True),
        }
    )
    assert ok_projection(authority=both, account_refs=(REF, REF2)).capability_ids == PROPOSABLE


def test_unknown_and_forbidden_references_are_indistinguishable():
    forbidden = make_authority(accounts={REF: AccountAccess(False, False)})
    unknown = make_authority(accounts={})
    first = run(authority=forbidden)
    second = run(authority=unknown)

    assert codes(first) == codes(second) == ["scope_inaccessible"]
    assert first == second
    # one inaccessible reference rejects the whole request: no silent narrowing
    partial = make_authority(accounts={REF: AccountAccess(True, True)})
    assert codes(run(authority=partial, account_refs=(REF, REF2))) == ["scope_inaccessible"]


def test_policy_disagreement_excludes_only_the_affected_capability():
    policy = build_real_policy_reference()
    policy.actions["account.mark_overdue"] = dataclasses.replace(
        policy.actions["account.mark_overdue"], execution_corridor="other"
    )
    projection = ok_projection(policy=policy)

    assert excluded(projection) == {("account.mark_overdue", "policy_mismatch")}
    assert projection.capability_ids == tuple(i for i in PROPOSABLE if i != "account.mark_overdue")

    policy = build_real_policy_reference()
    policy = dataclasses.replace(policy, read_route_permissions={})
    result = run(policy=policy)

    assert codes(result) == ["projection_empty"]  # reads unreconciled, writes lose evidence

    policy = dataclasses.replace(
        build_real_policy_reference(),
        autonomy_by_effect={},
    )
    assert codes(run(policy=policy)) == ["projection_empty"]


def test_a_missing_or_malformed_policy_is_rejected_not_guessed():
    for value in (None, {}, "policy", 7, object()):
        assert codes(run(policy=value)) == ["policy_reference_missing"]

    good = build_real_policy_reference()
    broken = [
        dataclasses.replace(good, actions=["list"]),
        dataclasses.replace(good, actions={1: "x"}),
        dataclasses.replace(good, known_permissions=frozenset({1, "x"})),
        dataclasses.replace(good, known_permissions=["list"]),
        dataclasses.replace(good, read_route_permissions={"a": 5}),
        dataclasses.replace(good, autonomy_by_effect=None),
        dataclasses.replace(good, source=None),
    ]
    entry = good.actions["account.mark_overdue"]
    object.__setattr__(entry, "execution_corridor", None)
    broken.append(good)

    for policy in broken:
        assert codes(run(policy=policy)) == ["policy_reference_invalid"]


def test_the_prerequisite_closure_only_ever_removes_the_dependent_capability():
    changed = emptied = 0

    for size in range(1, 6):
        for subset in itertools.combinations(PROPOSABLE, size):
            expected = closure(subset)
            registry = subset_registry(subset)
            result = run(registry=registry, pinned_registry_digest=registry.digest)

            if not expected:
                assert codes(result) == ["projection_empty"]
                assert set(subset) == {"account.mark_overdue"}
                emptied += 1
                continue

            projection = result.projection

            assert set(projection.capability_ids) == expected, subset
            if expected != set(subset):
                changed += 1
                assert set(subset) - expected == {"account.mark_overdue"}
                assert ("account.mark_overdue", "prerequisite_unavailable") in excluded(projection)

    assert (changed, emptied) == (3, 1)  # 4 of the 31 subsets are altered


def test_either_evidence_capability_keeps_the_mutating_one():
    for evidence in ("nba.evaluate", "action_space.evaluate"):
        registry = subset_registry({evidence, "account.mark_overdue"})
        projection = ok_projection(registry=registry, pinned_registry_digest=registry.digest)

        assert set(projection.capability_ids) == {evidence, "account.mark_overdue"}


def test_the_selection_is_monotonic_in_the_permissions():
    rng = random.Random(20261019)
    pool = sorted(ALL_PERMISSIONS)
    checked = 0

    for _ in range(scaled(120)):
        bigger = set(rng.sample(pool, rng.randrange(0, len(pool) + 1)))
        smaller = set(rng.sample(sorted(bigger), rng.randrange(0, len(bigger) + 1))) if bigger else set()
        rich = run(authority=make_authority(permissions=frozenset(bigger)))
        poor = run(authority=make_authority(permissions=frozenset(smaller)))
        rich_ids = set(rich.projection.capability_ids) if rich.ok else set()
        poor_ids = set(poor.projection.capability_ids) if poor.ok else set()

        assert poor_ids <= rich_ids
        assert rich_ids <= set(PROPOSABLE)
        checked += 1

    assert checked >= 5


def test_non_candidates_are_invisible_even_as_exclusions():
    definitions = []

    for definition in REGISTRY_V1.definitions:
        if definition.capability_id == "memory.recall":
            definition = dataclasses.replace(definition, llm_proposable=False)
        if definition.capability_id == "account.get_context":
            definition = dataclasses.replace(definition, enabled=False, llm_proposable=False)
        definitions.append(definition)

    registry = CapabilityRegistry(tuple(definitions))
    projection = ok_projection(registry=registry, pinned_registry_digest=registry.digest)

    assert "memory.recall" not in projection.capability_ids
    assert "account.get_context" not in projection.capability_ids
    assert not [e for e in projection.exclusions if e.capability_id in {"memory.recall", "account.get_context"}]
    assert "memory.recall" not in projection.output_schema_json


def test_exclusions_are_sorted_unique_and_the_order_of_definitions_is_irrelevant():
    result = run(authority=make_authority(permissions=ALL_PERMISSIONS - {"memory:read", "clients.manage"}))
    exclusions = result.projection.exclusions

    assert list(exclusions) == sorted(exclusions)
    assert len(set(exclusions)) == len(exclusions)
    assert codes(run(authority=make_authority(permissions=frozenset({"approval:read"})))) == ["projection_empty"]

    reversed_registry = CapabilityRegistry(tuple(reversed(REGISTRY_V1.definitions)))
    assert reversed_registry.digest == REGISTRY_V1.digest
    assert run(registry=reversed_registry).projection == run().projection


def test_the_selection_is_deterministic_for_random_scopes_and_permissions():
    rng = random.Random(77)
    pool = sorted(ALL_PERMISSIONS)

    for _ in range(scaled(60)):
        permissions = frozenset(rng.sample(pool, rng.randrange(0, len(pool) + 1)))
        readable, manageable = rng.random() < 0.8, rng.random() < 0.6
        scope = {REF: AccountAccess(readable, manageable)}
        authority = make_authority(permissions=permissions, accounts=scope)

        assert run(authority=authority) == run(authority=authority)


def test_candidates_require_every_flag_on_the_definition_itself():
    from types import SimpleNamespace

    def fake(**flags):
        base = {
            "registered": True, "enabled": True, "llm_proposable": True,
            "executable": False, "capability_id": "demo.read", "version": 1,
        }
        base.update(flags)
        return SimpleNamespace(**base)

    registry = SimpleNamespace(
        definitions=(
            fake(capability_id="a.ok"),
            fake(capability_id="b.unregistered", registered=False),
            fake(capability_id="c.disabled", enabled=False),
            fake(capability_id="d.not_proposable", llm_proposable=False),
            fake(capability_id="e.executable", executable=True),
        )
    )

    assert [d.capability_id for d in candidates(registry)] == ["a.ok"]


def test_exclusions_listed_after_the_closure_are_still_sorted():
    permissions = ALL_PERMISSIONS - {"approval:read"}
    projection = ok_projection(authority=make_authority(permissions=permissions))
    pairs = [(e.capability_id, e.reason.value) for e in projection.exclusions]

    assert pairs == [
        ("account.get_context", "permission_missing"),
        ("account.mark_overdue", "prerequisite_unavailable"),
        ("action_space.evaluate", "permission_missing"),
        ("nba.evaluate", "permission_missing"),
    ]
    assert list(projection.exclusions) == sorted(projection.exclusions)


def test_reads_need_every_reference_to_be_readable():
    mixed = make_authority(
        accounts={
            REF: AccountAccess(readable=True, manageable=True),
            REF2: AccountAccess(readable=False, manageable=True),
        }
    )
    result = run(authority=mixed, account_refs=(REF, REF2))

    # no read capability is usable, so the mutating one has no evidence either
    assert codes(result) == ["projection_empty"]

    one_way = make_authority(
        accounts={
            REF: AccountAccess(readable=True, manageable=False),
            REF2: AccountAccess(readable=True, manageable=False),
        }
    )
    projection = ok_projection(authority=one_way, account_refs=(REF, REF2))
    assert "account.mark_overdue" not in projection.capability_ids
