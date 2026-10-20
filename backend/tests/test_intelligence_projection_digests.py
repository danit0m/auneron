"""INTELLIGENCE-PROPOSAL -- digest model and verify_projection."""

import dataclasses
import itertools
import json
import random

import pytest

from app.intelligence.planning import canonical
from app.intelligence.planning.authority import AccountAccess
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.proposal import contracts as c
from app.intelligence.proposal import digests as d
from app.intelligence.proposal.builder import project
from app.intelligence.proposal.builder import verify_projection

from test_intelligence_plan_validator import ALL_PERMISSIONS
from test_intelligence_plan_validator import build_real_policy_reference
from test_intelligence_plan_validator import make_authority
from test_intelligence_projection_contracts import OBJ
from test_intelligence_projection_contracts import PROPOSABLE
from test_intelligence_projection_contracts import REF
from test_intelligence_projection_contracts import REF2
from test_intelligence_projection_contracts import base_request
from test_intelligence_projection_contracts import ok_projection
from test_intelligence_projection_contracts import scaled
from test_intelligence_projection_contracts import subset_registry


pytestmark = pytest.mark.security


def digest_of(**overrides):
    return project(base_request(**overrides)).projection.projection_digest


def tamper(projection, **changes):
    clone = dataclasses.replace(projection)

    for name, value in changes.items():
        object.__setattr__(clone, name, value)

    return clone


_CURRENT = object()


def verdict(projection, request=_CURRENT):
    found = verify_projection(
        projection, base_request() if request is _CURRENT else request
    )

    return [item.code.value for item in found], [item.stage for item in found]


# ----------------------------------------------------------------------
# domains
# ----------------------------------------------------------------------


def test_digest_domains_are_distinct_newline_terminated_and_prefix_free():
    existing = [
        value for name, value in vars(canonical).items() if name.startswith("DOMAIN_")
    ]
    added = [d.DOMAIN_POLICY, d.DOMAIN_SCHEMA, d.DOMAIN_INSTRUCTIONS, d.DOMAIN_PROJECTION]
    everything = existing + added

    assert len(existing) == 5 and len(everything) == 9
    assert len(set(everything)) == 9
    assert all(item.endswith(b"\n") for item in everything)
    assert not any(a != b and b.startswith(a) for a in everything for b in everything)
    assert all(item.startswith(b"auneron.proj.v1.") for item in added)


def test_the_same_material_under_every_domain_gives_different_digests():
    material = {"a": 1, "b": ["x"]}
    domains = [
        value for name, value in vars(canonical).items() if name.startswith("DOMAIN_")
    ] + [d.DOMAIN_POLICY, d.DOMAIN_SCHEMA, d.DOMAIN_INSTRUCTIONS, d.DOMAIN_PROJECTION]

    assert len({canonical.digest(domain, material) for domain in domains}) == len(domains)


def test_registry_and_context_digests_come_from_planning_unchanged():
    projection = ok_projection()

    assert projection.registry_digest == REGISTRY_V1.digest
    assert projection.context_digest == make_authority().context_digest()
    assert projection.policy_digest == d.policy_digest(build_real_policy_reference())
    assert projection.schema_digest == d.schema_digest(projection.output_schema)
    assert projection.instructions_digest == d.instructions_digest(
        c.INSTRUCTIONS_TEMPLATE_ID, c.INSTRUCTIONS_TEXT
    )


# ----------------------------------------------------------------------
# policy digest
# ----------------------------------------------------------------------


def test_the_policy_digest_is_order_independent_and_field_sensitive():
    policy = build_real_policy_reference()
    base = d.policy_digest(policy)
    reordered = dataclasses.replace(
        policy,
        actions=dict(reversed(list(policy.actions.items()))),
        autonomy_by_effect=dict(reversed(list(policy.autonomy_by_effect.items()))),
        known_permissions=frozenset(sorted(policy.known_permissions, reverse=True)),
        read_route_permissions=dict(reversed(list(policy.read_route_permissions.items()))),
    )

    assert d.policy_digest(reordered) == base == d.policy_digest(build_real_policy_reference())

    changes = []
    key = "account.mark_overdue"
    for field in ("capability_kind", "initiation_actor", "authority_model", "required_authority", "execution_corridor"):
        actions = dict(policy.actions)
        actions[key] = dataclasses.replace(actions[key], **{field: "changed"})
        changes.append(dataclasses.replace(policy, actions=actions))
    for field in ("disposition", "risk_level"):
        autonomy = dict(policy.autonomy_by_effect)
        autonomy["MUTATE"] = dataclasses.replace(autonomy["MUTATE"], **{field: "changed"})
        changes.append(dataclasses.replace(policy, autonomy_by_effect=autonomy))
    autonomy = dict(policy.autonomy_by_effect)
    autonomy["READ"] = dataclasses.replace(autonomy["READ"], approver_permission="approval:decide")
    changes.append(dataclasses.replace(policy, autonomy_by_effect=autonomy))
    changes.append(dataclasses.replace(policy, known_permissions=policy.known_permissions | {"extra:perm"}))
    changes.append(dataclasses.replace(policy, known_permissions=policy.known_permissions - {"clients.view"}))
    routes = dict(policy.read_route_permissions)
    routes["nba.evaluate"] = frozenset({"approval:read", "extra:perm"})
    changes.append(dataclasses.replace(policy, read_route_permissions=routes))
    changes.append(dataclasses.replace(policy, source="other"))
    changes.append(dataclasses.replace(policy, actions={}))

    digests = {d.policy_digest(item) for item in changes}

    assert base not in digests
    assert len(digests) == len(changes)


def test_the_policy_material_is_strict():
    good = build_real_policy_reference()
    broken = [
        dataclasses.replace(good, actions=["x"]),
        dataclasses.replace(good, actions={1: good.actions["account.mark_overdue"]}),
        dataclasses.replace(good, known_permissions={1, 2}),
        dataclasses.replace(good, known_permissions=("a",)),
        dataclasses.replace(good, read_route_permissions={"a": 5}),
        dataclasses.replace(good, source=3),
        dataclasses.replace(good, autonomy_by_effect=3),
    ]

    for policy in broken:
        with pytest.raises((ValueError, AttributeError, TypeError)):
            d.policy_digest(policy)

    assert "approver_permission" not in d.policy_material(good)["autonomy"]["READ"]


# ----------------------------------------------------------------------
# projection digest
# ----------------------------------------------------------------------


def material_kwargs(projection):
    return {
        "contract": projection.contract,
        "template_id": projection.template_id,
        "registry_digest": projection.registry_digest,
        "context_digest": projection.context_digest,
        "policy_digest": projection.policy_digest,
        "profile_name": projection.profile_name,
        "profile_max_output_tokens_cap": projection.profile_max_output_tokens_cap,
        "max_schema_bytes": projection.max_schema_bytes,
        "max_account_refs": projection.max_account_refs,
        "plan_limits": projection.plan_limits,
        "objective_ref": projection.objective_ref,
        "plan_version": projection.plan_version,
        "account_refs": projection.account_refs,
        "capability_ids": projection.capability_ids,
        "exclusions": projection.exclusions,
        "schema_digest": projection.schema_digest,
        "instructions_digest": projection.instructions_digest,
    }


def test_every_input_of_the_material_changes_the_projection_digest():
    projection = ok_projection()
    base_args = material_kwargs(projection)
    base = d.projection_digest(d.projection_material(**base_args))
    other = "f" * 64
    alterations = {
        "contract": "proj.v2",
        "template_id": "plan-instructions.v2",
        "registry_digest": other,
        "context_digest": other,
        "policy_digest": other,
        "profile_name": "lab-strong",
        "profile_max_output_tokens_cap": 513,
        "max_schema_bytes": 12289,
        "max_account_refs": 9,
        "plan_limits": projection.plan_limits[:-1],
        "objective_ref": "obj_zzzzzzzz",
        "plan_version": 2,
        "account_refs": (REF, REF2),
        "capability_ids": PROPOSABLE[:-1],
        "exclusions": (c.Exclusion("memory.recall", c.ExclusionReason.PERMISSION_MISSING),),
        "schema_digest": other,
        "instructions_digest": other,
    }

    assert set(alterations) == set(base_args)
    seen = {base}
    for name, value in alterations.items():
        altered = dict(base_args, **{name: value})
        result = d.projection_digest(d.projection_material(**altered))

        assert result != base, name
        seen.add(result)
    assert len(seen) == len(alterations) + 1


def test_equivalent_orderings_do_not_change_the_projection_digest():
    projection = ok_projection()
    args = material_kwargs(projection)
    base = d.projection_digest(d.projection_material(**args))
    pair = (c.Exclusion("memory.recall", c.ExclusionReason.PERMISSION_MISSING),
            c.Exclusion("account.get_context", c.ExclusionReason.POLICY_MISMATCH))
    ordered = dict(args, account_refs=(REF, REF2), exclusions=tuple(sorted(pair)))
    shuffled = dict(
        ordered,
        account_refs=tuple(reversed(ordered["account_refs"])),
        capability_ids=tuple(reversed(args["capability_ids"])),
        exclusions=tuple(reversed(ordered["exclusions"])),
    )

    assert d.projection_digest(d.projection_material(**shuffled)) == d.projection_digest(
        d.projection_material(**dict(ordered, capability_ids=args["capability_ids"]))
    )
    assert base != d.projection_digest(d.projection_material(**ordered))


def test_the_request_inputs_that_matter_change_the_projection_digest():
    base = digest_of()
    two_accounts = make_authority(
        accounts={REF: AccountAccess(True, True), REF2: AccountAccess(True, True)}
    )
    policy = build_real_policy_reference()
    changed_policy = dataclasses.replace(policy, source="other")
    subset = subset_registry(PROPOSABLE[:3])
    variants = {
        "profile name": dict(profile=c.ProfileView("lab-strong", 512)),
        "profile cap": dict(profile=c.ProfileView("lab-fast", 1024)),
        "max_schema_bytes": dict(limits=c.ProjectionLimits(12289, 8)),
        "max_account_refs": dict(limits=c.ProjectionLimits(12288, 9)),
        "objective": dict(objective_ref="obj_zzzzzzzz"),
        "plan_version": dict(plan_version=2),
        "account refs": dict(authority=two_accounts, account_refs=(REF, REF2)),
        "initiator": dict(authority=make_authority(initiator_ref="user:43")),
        "role": dict(authority=make_authority(role="viewer")),
        "elevation": dict(authority=make_authority(session_elevated=True)),
        "account flags": dict(authority=make_authority(accounts={REF: AccountAccess(True, False)})),
        "permission": dict(authority=make_authority(permissions=ALL_PERMISSIONS | {"extra:perm"})),
        "policy": dict(policy=changed_policy),
        "registry": dict(registry=subset, pinned_registry_digest=subset.digest),
    }
    seen = {base}

    for label, overrides in variants.items():
        value = digest_of(**overrides)

        assert value != base, label
        seen.add(value)
    assert len(seen) == len(variants) + 1


def test_equivalent_requests_have_the_same_projection_digest():
    base = digest_of()
    authority = make_authority(
        permissions=frozenset(sorted(ALL_PERMISSIONS, reverse=True)),
        accounts=dict(reversed(list({REF: AccountAccess(True, True)}.items()))),
    )
    policy = build_real_policy_reference()
    policy = dataclasses.replace(policy, actions=dict(reversed(list(policy.actions.items()))))

    assert digest_of(authority=authority, policy=policy) == base


# ----------------------------------------------------------------------
# logical-context collisions
# ----------------------------------------------------------------------


def test_distinct_logical_contexts_never_share_a_context_digest():
    perms = sorted(ALL_PERMISSIONS)
    contexts = [
        make_authority(),
        make_authority(initiator_ref="user:4"),
        make_authority(initiator_ref="user:42 "),
        make_authority(initiator_ref="user:42:"),
        make_authority(role="viewer"),
        make_authority(role="analyst"),
        make_authority(session_elevated=True),
        make_authority(permissions=frozenset(perms[:-1])),
        make_authority(permissions=frozenset(perms[1:])),
        make_authority(permissions=frozenset({"a,b"})),
        make_authority(permissions=frozenset({"a", "b"})),
        make_authority(permissions=frozenset({"a b"})),
        make_authority(accounts={}),
        make_authority(accounts={REF: AccountAccess(True, False)}),
        make_authority(accounts={REF: AccountAccess(False, True)}),
        make_authority(accounts={REF: AccountAccess(True, True), REF2: AccountAccess(True, True)}),
        make_authority(accounts={REF2: AccountAccess(True, True)}),
    ]
    digests = [item.context_digest() for item in contexts]

    assert len(set(digests)) == len(contexts)

    rng = random.Random(31)
    seen = {}
    for _ in range(scaled(400)):
        subset = frozenset(rng.sample(perms, rng.randrange(0, len(perms) + 1)))
        flags = (rng.random() < 0.5, rng.random() < 0.5)
        role = rng.choice(["viewer", "manager", "analyst"])
        elevated = rng.random() < 0.5
        key = (subset, flags, role, elevated)
        value = make_authority(
            permissions=subset,
            role=role,
            session_elevated=elevated,
            accounts={REF: AccountAccess(*flags)},
        ).context_digest()

        assert seen.setdefault(value, key) == key


# ----------------------------------------------------------------------
# verify_projection
# ----------------------------------------------------------------------


def test_an_intact_current_projection_verifies_clean():
    projection = ok_projection()

    assert verify_projection(projection, base_request()) == ()
    assert projection == ok_projection()  # verifying changed nothing


def test_malformed_projections_are_reported_without_raising():
    good = ok_projection()

    class Imitation(c.Projection):
        pass

    clone = tamper(good)
    object.__setattr__(clone, "surplus", "x")
    missing = tamper(good)
    object.__delattr__(missing, "instructions")
    malformed = [
        None,
        {},
        "projection",
        object(),
        Imitation(**{f.name: getattr(good, f.name) for f in dataclasses.fields(good)}),
        tamper(good, capability_ids=list(good.capability_ids)),
        tamper(good, capability_ids=("a", 3)),
        tamper(good, registry_digest=good.registry_digest.upper()),
        tamper(good, registry_digest="short"),
        tamper(good, context_digest=7),
        tamper(good, plan_version=True),
        tamper(good, plan_version=1.0),
        tamper(good, schema_bytes="6222"),
        tamper(good, is_grant=True),
        tamper(good, replay_protected=True),
        tamper(good, synthetic_authority=False),
        tamper(good, exclusions=[c.Exclusion("a.b", c.ExclusionReason.POLICY_MISMATCH)]),
        tamper(good, exclusions=("x",)),
        tamper(good, plan_limits=(("max_steps", "8"),)),
        tamper(good, plan_limits=[("max_steps", 8)]),
        tamper(good, instructions="café"),
        tamper(good, output_schema_json=5),
        clone,
        missing,
    ]

    for item in malformed:
        assert verdict(item) == (["projection_malformed"], [8]), repr(type(item))


def test_tampered_projections_are_reported_as_tampered():
    good = ok_projection()
    schema = good.output_schema
    schema["properties"]["steps"]["maxItems"] = 9
    other_schema = canonical.canonical_bytes(schema).decode("ascii")
    zero = "0" * 64
    tampered = {
        "schema text": tamper(good, output_schema_json=other_schema),
        "schema bytes": tamper(good, schema_bytes=good.schema_bytes + 1),
        "capabilities": tamper(good, capability_ids=good.capability_ids[:-1]),
        "profile": tamper(good, profile_name="lab-strong"),
        "objective": tamper(good, objective_ref="obj_zzzzzzzz"),
        "plan version": tamper(good, plan_version=2),
        "accounts": tamper(good, account_refs=(REF, REF2)),
        "exclusions": tamper(good, exclusions=(c.Exclusion("memory.recall", c.ExclusionReason.POLICY_MISMATCH),)),
        "contract": tamper(good, contract="proj.v2"),
        "template": tamper(good, template_id="plan-instructions.v2"),
        "instructions": tamper(good, instructions=good.instructions + " "),
        "instructions digest": tamper(good, instructions_digest=zero),
        "schema digest": tamper(good, schema_digest=zero),
        "policy digest": tamper(good, policy_digest=zero),
        "context digest": tamper(good, context_digest=zero),
        "registry digest": tamper(good, registry_digest=zero),
        "projection digest": tamper(good, projection_digest=zero),
        "limits": tamper(good, max_schema_bytes=good.max_schema_bytes + 1),
        "plan limits": tamper(good, plan_limits=good.plan_limits[:-1]),
        "invalid json": tamper(good, output_schema_json="{not json"),
    }

    for label, item in tampered.items():
        assert verdict(item) == (["projection_tampered"], [8]), label


def test_a_consistent_projection_that_the_request_no_longer_yields_is_stale():
    good = ok_projection()
    policy = build_real_policy_reference()
    policy.actions["account.mark_overdue"] = dataclasses.replace(
        policy.actions["account.mark_overdue"], execution_corridor="other"
    )
    weaker = make_authority(permissions=ALL_PERMISSIONS - {"memory:read"})
    subset = subset_registry(PROPOSABLE[:3])
    stale_requests = {
        "permission lost": base_request(authority=weaker),
        "policy changed": base_request(policy=policy),
        "scope changed": base_request(authority=make_authority(accounts={REF: AccountAccess(True, False)})),
        "registry changed": base_request(registry=subset, pinned_registry_digest=subset.digest),
        "profile changed": base_request(profile=c.ProfileView("lab-fast", 1024)),
        "objective changed": base_request(objective_ref="obj_zzzzzzzz"),
        "request now invalid": base_request(plan_version=0),
        "not a request": None,
        "wrong pin": base_request(pinned_registry_digest="0" * 64),
        "authority gone": base_request(authority=None),
    }

    for label, request in stale_requests.items():
        assert verdict(good, request) == (["projection_stale"], [8]), label

    # tampering is reported before staleness, malformation before both
    assert verdict(tamper(good, plan_version=2), stale_requests["permission lost"])[0] == ["projection_tampered"]
    assert verdict(tamper(good, plan_version=True), stale_requests["permission lost"])[0] == ["projection_malformed"]


def test_mutating_the_original_inputs_is_detected_as_stale():
    authority = make_authority()
    request = base_request(authority=authority)
    projection = project(request).projection

    assert verify_projection(projection, request) == ()
    authority.accounts["acct_zzzzzz"] = AccountAccess(True, True)  # in-place change

    assert [item.code.value for item in verify_projection(projection, request)] == ["projection_stale"]


def test_a_changed_exclusion_reason_changes_the_digest():
    args = material_kwargs(ok_projection())
    one = dict(args, exclusions=(c.Exclusion("memory.recall", c.ExclusionReason.PERMISSION_MISSING),))
    other = dict(args, exclusions=(c.Exclusion("memory.recall", c.ExclusionReason.POLICY_MISMATCH),))
    another = dict(args, exclusions=(c.Exclusion("nba.evaluate", c.ExclusionReason.PERMISSION_MISSING),))
    digests = {d.projection_digest(d.projection_material(**item)) for item in (one, other, another)}

    assert len(digests) == 3


def test_the_instruction_and_schema_digests_are_domain_bound_and_template_bound():
    schema = ok_projection().output_schema

    assert d.schema_digest(schema) == canonical.digest(d.DOMAIN_SCHEMA, schema)
    assert d.schema_digest(schema) != canonical.digest(d.DOMAIN_PROJECTION, schema)
    assert d.schema_digest(schema) != canonical.digest(d.DOMAIN_POLICY, schema)
    assert d.instructions_digest("plan-instructions.v1", c.INSTRUCTIONS_TEXT) != d.instructions_digest(
        "plan-instructions.v2", c.INSTRUCTIONS_TEXT
    )
    assert d.instructions_digest("plan-instructions.v1", c.INSTRUCTIONS_TEXT) != d.instructions_digest(
        "plan-instructions.v1", c.INSTRUCTIONS_TEXT + "."
    )


def forged(projection, **changes):
    """A projection whose digests were all recomputed to match its edits."""
    clone = tamper(projection, **changes)
    schema = json.loads(clone.output_schema_json)
    object.__setattr__(clone, "schema_digest", d.schema_digest(schema))
    object.__setattr__(
        clone,
        "instructions_digest",
        d.instructions_digest(clone.template_id, clone.instructions),
    )
    args = {
        key: getattr(clone, key)
        for key in (
            "contract", "template_id", "registry_digest", "context_digest",
            "policy_digest", "profile_name", "profile_max_output_tokens_cap",
            "max_schema_bytes", "max_account_refs", "plan_limits", "objective_ref",
            "plan_version", "account_refs", "capability_ids", "exclusions",
            "schema_digest", "instructions_digest",
        )
    }
    object.__setattr__(
        clone, "projection_digest", d.projection_digest(d.projection_material(**args))
    )
    return clone


def test_self_consistent_forgeries_are_still_tampered():
    good = ok_projection()
    schema = good.output_schema
    spaced = json.dumps(schema, sort_keys=True, separators=(", ", ": "))

    assert verdict(forged(good)) == ([], [])  # the forger itself is faithful
    cases = {
        "contract": forged(good, contract="proj.v9"),
        "template": forged(good, template_id="plan-instructions.v9"),
        "instructions": forged(good, instructions=good.instructions + " Extra."),
        "non-canonical schema text": forged(good, output_schema_json=spaced),
    }

    for label, item in cases.items():
        assert verdict(item) == (["projection_tampered"], [8]), label
