"""INTELLIGENCE-PROPOSAL -- adversarial tests of the projection API."""

import copy
import dataclasses
import json
import logging
import random
import socket

import pytest

from app.intelligence.planning.authority import AccountAccess
from app.intelligence.planning.authority import SyntheticAuthorityContext
from app.intelligence.planning.capabilities import PINNED_REGISTRY_DIGEST
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.capabilities import build_v1_registry
from app.intelligence.proposal import contracts as c
from app.intelligence.proposal import rendering
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
from test_intelligence_projection_contracts import codes
from test_intelligence_projection_contracts import ok_projection
from test_intelligence_projection_contracts import run
from test_intelligence_projection_contracts import scaled


pytestmark = pytest.mark.security

HIDDEN = ("account.mark_paid", "work.escalate_to_human")
REJECTIONS = {
    code
    for code in c.ProjectionRejectionCode
    if code
    not in {
        c.ProjectionRejectionCode.PROJECTION_INTERNAL_ERROR,
        c.ProjectionRejectionCode.PROJECTION_MALFORMED,
        c.ProjectionRejectionCode.PROJECTION_TAMPERED,
        c.ProjectionRejectionCode.PROJECTION_STALE,
    }
}


class Evil:
    def __getattr__(self, name):
        raise RuntimeError("hostile object")


class Boom(dict):
    def get(self, *args, **kwargs):
        raise RuntimeError("hostile mapping")

    def items(self):
        raise RuntimeError("hostile mapping")

    def __getitem__(self, key):
        raise RuntimeError("hostile mapping")

    def __iter__(self):
        raise RuntimeError("hostile mapping")


def hostile_values():
    return [
        None, 0, 1, -1, True, False, 1.5, float("nan"), float("inf"), "", " ", "x",
        "acct_abc123", "obj_k3m9x2ab", "é", "é", "‮", "a\x00b", "\ud800",
        "x" * 70000, "obj_k3m9x2ab\n", b"bytes", [], [1], (), (1,), (REF,), {}, {"a": 1},
        set(), {1}, frozenset(), frozenset({1}), object(), Evil(), Boom(), len,
    ]


def assert_invariants(request, result):
    assert isinstance(result, c.ProjectionResult)
    assert c.ProjectionRejectionCode.PROJECTION_INTERNAL_ERROR not in result.codes

    if not result.ok:
        assert set(result.codes) <= REJECTIONS
        assert list(result.rejections) == sorted(set(result.rejections))
        return

    projection = result.projection

    assert set(projection.capability_ids) <= set(PROPOSABLE)
    assert not any(hidden in projection.output_schema_json for hidden in HIDDEN)
    assert not any(hidden in projection.instructions for hidden in HIDDEN)
    assert (projection.synthetic_authority, projection.is_grant, projection.replay_protected) == (True, False, False)
    assert projection.schema_bytes <= projection.max_schema_bytes
    assert projection.registry_digest == PINNED_REGISTRY_DIGEST or projection.registry_digest == request.pinned_registry_digest


# ----------------------------------------------------------------------
# fuzzing
# ----------------------------------------------------------------------


def corrupt(rng, pool):
    """One hostile request built from a valid one."""
    request = base_request(registry=build_v1_registry())
    strategy = rng.randrange(8)

    if strategy == 0:
        field = rng.choice([f.name for f in dataclasses.fields(c.ProjectionRequest)])
        return dataclasses.replace(request, **{field: rng.choice(pool)})
    if strategy == 1:
        return rng.choice(pool)
    if strategy == 2:
        object.__setattr__(
            request.authority,
            rng.choice(["initiator_ref", "role", "permissions", "accounts", "session_elevated", "synthetic"]),
            rng.choice(pool),
        )
    elif strategy == 3:
        object.__setattr__(
            request.policy,
            rng.choice(["actions", "autonomy_by_effect", "known_permissions", "read_route_permissions", "source"]),
            rng.choice(pool),
        )
    elif strategy == 4:
        setattr(request.registry, rng.choice(["_definitions", "version", "limits", "_digest"]), rng.choice(pool))
    elif strategy == 5:
        definition = rng.choice(request.registry.definitions)
        object.__setattr__(definition, rng.choice(["input_schema", "output_schema", "required_permissions", "enabled", "llm_proposable"]), rng.choice(pool))
    elif strategy == 6:
        object.__setattr__(request.profile, rng.choice(["name", "max_output_tokens_cap"]), rng.choice(pool))
    else:
        object.__setattr__(request.limits, rng.choice(["max_schema_bytes", "max_account_refs"]), rng.choice(pool))

    return request


def test_the_public_api_never_raises_on_hostile_requests():
    rng = random.Random(20261019)
    pool = hostile_values()
    attempts = scaled(3000)
    accepted = 0

    for index in range(attempts):
        request = corrupt(rng, pool)
        result = project(request)

        assert_invariants(request, result)
        accepted += result.ok
        if result.ok and index % 25 == 0:
            assert verify_projection(result.projection, request) == ()

    assert attempts >= 5
    if SCALE_IS_FULL:
        assert attempts >= 3000
        assert 0 < accepted < attempts  # both outcomes are exercised


SCALE_IS_FULL = scaled(3000) == 3000


def test_verify_projection_never_raises_on_hostile_projections():
    rng = random.Random(7)
    pool = hostile_values()
    request = base_request()
    good = project(request).projection
    names = [f.name for f in dataclasses.fields(c.Projection)]

    for _ in range(scaled(600)):
        clone = dataclasses.replace(good)

        for _ in range(rng.randrange(1, 3)):
            object.__setattr__(clone, rng.choice(names), rng.choice(pool))

        if clone == good:
            continue

        found = verify_projection(clone, request)

        assert len(found) == 1
        assert found[0].code in {
            c.ProjectionRejectionCode.PROJECTION_MALFORMED,
            c.ProjectionRejectionCode.PROJECTION_TAMPERED,
            c.ProjectionRejectionCode.PROJECTION_STALE,
        }
        assert found[0].stage == int(c.Stage.VERIFICATION)

    for value in pool:
        assert len(verify_projection(value, value)) == 1
        assert len(verify_projection(good, value)) == 1


# ----------------------------------------------------------------------
# authority
# ----------------------------------------------------------------------


def test_hostile_authority_objects_are_a_typed_rejection():
    def with_field(**changes):
        authority = make_authority()
        for name, value in changes.items():
            object.__setattr__(authority, name, value)
        return authority

    cases = {
        "none": None,
        "dict": {"permissions": []},
        "string": "admin",
        "list accounts": with_field(accounts=["acct"]),
        "hostile accounts": with_field(accounts=Boom({REF: AccountAccess(True, True)})),
        "foreign access value": with_field(accounts={REF: "yes"}),
        "int access flags": with_field(accounts={REF: AccountAccess(1, 1)}),
        "int readable only": with_field(accounts={REF: AccountAccess(1, True)}),
        "int manageable only": with_field(accounts={REF: AccountAccess(True, 1)}),
        "mutable permissions": with_field(permissions={"approval:read"}),
        "mixed permissions": with_field(permissions=frozenset({1, "x"})),
        "elevation not bool": with_field(session_elevated="yes"),
        "unknown role": with_field(role="superuser"),
        "not synthetic": with_field(synthetic=False),
        "nfd initiator": make_authority(initiator_ref="user:é"),
        "control initiator": make_authority(initiator_ref="user:\x07"),
        "surrogate initiator": make_authority(initiator_ref="user:\ud800"),
        "nfd account key": make_authority(accounts={"acct_éabc1": AccountAccess(True, True), REF: AccountAccess(True, True)}),
    }

    for label, authority in cases.items():
        assert codes(run(authority=authority)) == ["authority_context_invalid"], label


def test_an_authority_subclass_cannot_lie_about_itself():
    class Liar(SyntheticAuthorityContext):
        def context_digest(self):
            return "f" * 64

        def access_to(self, account_ref):
            return AccountAccess(True, True)

        def holds(self, permission):
            return True

    liar = Liar(
        initiator_ref="user:42",
        role="manager",
        permissions=ALL_PERMISSIONS,
        accounts={REF: AccountAccess(True, False)},
    )
    honest = SyntheticAuthorityContext(
        initiator_ref="user:42",
        role="manager",
        permissions=ALL_PERMISSIONS,
        accounts={REF: AccountAccess(True, False)},
    )
    result = run(authority=liar)

    assert result == run(authority=honest)  # decided on its data, not its claims
    assert "account.mark_overdue" not in result.projection.capability_ids
    assert result.projection.context_digest == honest.context_digest()


def test_a_synthetic_context_is_refused_when_the_environment_is_production(monkeypatch):
    request = base_request()  # built while it was still allowed

    monkeypatch.setenv("APP_ENV", "production")

    assert codes(project(request)) == ["authority_context_invalid"]
    with pytest.raises(Exception):
        make_authority()  # and it cannot even be built any more


# ----------------------------------------------------------------------
# registry
# ----------------------------------------------------------------------


def test_registry_tampering_and_substitution_are_rejected():
    tampered = build_v1_registry()
    tampered.get("account.get_context", 1).input_schema["properties"]["account_ref"]["pattern"] = ".*"
    reordered = build_v1_registry()
    object.__setattr__(reordered.get("nba.evaluate", 1), "enabled", False)

    class Raising(CapabilityRegistry):
        @property
        def definitions(self):
            raise RuntimeError("hostile registry")

    sub = CapabilityRegistry(tuple(d for d in REGISTRY_V1.definitions if d.capability_id != "memory.recall"))
    cases = {
        # the cached digest of the tampered registry still equals the pin
        "in-place schema change": dict(registry=tampered, pinned_registry_digest=tampered.digest),
        "flag flipped in place": dict(registry=reordered, pinned_registry_digest=PINNED_REGISTRY_DIGEST),
        "none": dict(registry=None),
        "object": dict(registry=object()),
        "hostile subclass": dict(registry=Raising(REGISTRY_V1.definitions)),
        "other registry, old pin": dict(registry=sub),
        "wrong pin": dict(pinned_registry_digest="0" * 64),
    }

    assert tampered.digest == PINNED_REGISTRY_DIGEST  # the cache cannot see it
    for label, overrides in cases.items():
        assert codes(run(**overrides)) == ["registry_integrity_failure"], label


def test_a_registry_that_passes_is_used_only_through_its_copy():
    registry = build_v1_registry()
    projection = ok_projection(registry=registry, pinned_registry_digest=registry.digest)
    before = copy.deepcopy(registry.descriptor())

    assert registry.descriptor() == before  # not modified by the projection
    assert projection.registry_digest == registry.digest == PINNED_REGISTRY_DIGEST


# ----------------------------------------------------------------------
# internal metadata, size and complexity
# ----------------------------------------------------------------------


def custom_property_registry(name):
    definitions = []

    for definition in REGISTRY_V1.definitions:
        if definition.capability_id == "memory.recall":
            schema = copy.deepcopy(dict(definition.input_schema))
            schema["properties"][name] = {"type": "string", "maxLength": 5}
            definition = dataclasses.replace(definition, input_schema=schema)
        definitions.append(definition)

    return CapabilityRegistry(tuple(definitions))


def test_every_internal_field_name_is_refused_as_a_schema_key():
    assert len(rendering.INTERNAL_KEYS) == 22

    for name in sorted(rendering.INTERNAL_KEYS):
        registry = custom_property_registry(name)
        result = run(registry=registry, pinned_registry_digest=registry.digest)

        assert codes(result) == ["internal_term_leak"], name


def test_schema_size_and_account_list_bombs_are_bounded():
    definitions = []

    for definition in REGISTRY_V1.definitions:
        if definition.capability_id == "memory.recall":
            schema = copy.deepcopy(dict(definition.input_schema))
            for index in range(400):
                schema["properties"][f"field{index}"] = {"type": "integer", "minimum": 0, "maximum": 99}
            definition = dataclasses.replace(definition, input_schema=schema)
        definitions.append(definition)

    registry = CapabilityRegistry(tuple(definitions))

    assert codes(run(registry=registry, pinned_registry_digest=registry.digest)) == ["schema_too_large"]
    assert codes(run(account_refs=tuple(f"acct_bbbb{i:03d}" for i in range(1000)))) == ["request_invalid"]
    assert codes(run(account_refs=(REF,) * 8)) == ["request_invalid"]


def test_hostile_text_in_request_fields_is_rejected_and_never_echoed():
    canary = "CANARYx9f2"
    hostile = [
        "obj_" + canary.lower() + "‮",
        "obj_" + canary.lower() + "\n",
        "obj_" + canary.lower() + "​",
        "obj_ｋ3m9x2ab",
        "obj_" + canary.lower() + "\ud800",
        ("obj_" + canary.lower()) * 5000,
        "obj_k3m9x2ab\x00" + canary,
    ]

    for text in hostile:
        result = run(objective_ref=text)

        assert codes(result) == ["request_invalid"]
        assert canary not in repr(result) and canary.lower() not in repr(result).replace("obj_", "")

    for text in ("acct_１２３４５６", "acct_abc12́", "acct_abc\x00de", "acct_abcdef\n"):
        assert codes(run(account_refs=(text,))) == ["request_invalid"]

    for text in ("lab‮fast", "lab-fast\n", "LAB", "lab fast", "lab​-fast", "x" * 10000):
        assert codes(run(profile=c.ProfileView(text, 512))) == ["request_invalid"]


# ----------------------------------------------------------------------
# no authority leaks out
# ----------------------------------------------------------------------


def test_forbidden_capabilities_are_never_projected_for_any_authority():
    rng = random.Random(555)
    pool = sorted(ALL_PERMISSIONS)

    for _ in range(scaled(250)):
        permissions = frozenset(rng.sample(pool, rng.randrange(0, len(pool) + 1)))
        flags = AccountAccess(rng.random() < 0.8, rng.random() < 0.6)
        refs = (REF, REF2) if rng.random() < 0.3 else (REF,)
        accounts = {ref: flags for ref in refs}
        result = run(authority=make_authority(permissions=permissions, accounts=accounts), account_refs=refs)

        if not result.ok:
            assert set(result.codes) <= REJECTIONS
            continue

        projection = result.projection
        assert set(projection.capability_ids) <= set(PROPOSABLE)
        for hidden in HIDDEN:
            assert hidden not in projection.output_schema_json
            assert hidden not in {e.capability_id for e in projection.exclusions}
        for item in projection.capability_ids:
            definition = REGISTRY_V1.get(item, 1)
            assert set(definition.required_permissions) <= permissions


def test_an_identifying_canary_never_reaches_any_output():
    canary = "user:CANARY-3c9a77"
    result = run(authority=make_authority(initiator_ref=canary))
    projection = result.projection
    texts = [
        repr(result),
        repr(projection),
        str(projection.audit_view()),
        projection.output_schema_json,
        projection.instructions,
    ]

    assert not any("CANARY" in text for text in texts)
    assert projection.context_digest == make_authority(initiator_ref=canary).context_digest()


def test_the_inputs_are_not_modified_by_projecting():
    registry = build_v1_registry()
    policy = build_real_policy_reference()
    authority = make_authority()
    request = base_request(
        registry=registry, policy=policy, authority=authority,
        pinned_registry_digest=registry.digest,
    )
    before = (
        copy.deepcopy(registry.descriptor()),
        copy.deepcopy(policy),
        copy.deepcopy(authority),
        copy.deepcopy(request.account_refs),
    )

    for _ in range(3):
        assert project(request).ok

    assert registry.descriptor() == before[0]
    assert policy == before[1]
    assert authority == before[2]
    assert request.account_refs == before[3]


def test_projecting_performs_no_io_logging_or_persistence(monkeypatch, caplog):
    def refuse(*args, **kwargs):
        raise AssertionError("I/O attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    import app.database.database as database

    monkeypatch.setattr(database, "SessionLocal", refuse)
    random_state = random.getstate()

    with caplog.at_level(logging.DEBUG):
        result = run()
        assert verify_projection(result.projection, base_request()) == ()

    assert result.ok and caplog.records == []
    assert random.getstate() == random_state


def test_nothing_in_the_package_can_execute_or_approve():
    import app.intelligence.proposal.builder as builder
    import app.intelligence.proposal.selection as selection

    words = ("execute", "dispatch", "approve", "submit", "enqueue", "schedule", "invoke", "authorize")
    names = set(dir(c)) | set(dir(rendering)) | set(dir(builder)) | set(dir(selection))
    names |= {f.name for f in dataclasses.fields(c.Projection)}
    names |= set(dir(c.Projection)) | set(dir(c.ProjectionResult))
    offending = [n for n in names if any(w in n.lower() for w in words) and not n.startswith("__")]

    assert offending == []


def test_unexpected_internal_failures_are_typed_not_raised(monkeypatch):
    import app.intelligence.proposal.builder as builder

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(builder, "select_capabilities", boom)
    result = run()

    assert codes(result) == ["projection_internal_error"]
    assert result.rejections[0].stage == int(c.Stage.INTERNAL)


def test_a_policy_that_cannot_be_evaluated_is_reported_as_invalid(monkeypatch):
    import app.intelligence.proposal.selection as selection

    def unusable(*args, **kwargs):
        raise AttributeError("malformed policy")

    monkeypatch.setattr(selection, "reconcile_capability", unusable)

    assert codes(run()) == ["policy_reference_invalid"]


def test_look_alike_objects_are_not_accepted_in_place_of_the_real_types():
    from types import SimpleNamespace

    # a dataclass with the very same fields is not a SyntheticAuthorityContext
    @dataclasses.dataclass(frozen=True)
    class LookAlike:
        initiator_ref: str
        role: str
        permissions: frozenset
        accounts: dict
        session_elevated: bool = False
        synthetic: bool = True

    authority = LookAlike("user:42", "manager", ALL_PERMISSIONS, {REF: AccountAccess(True, True)})
    assert codes(run(authority=authority)) == ["authority_context_invalid"]

    # a registry-shaped object is not a CapabilityRegistry
    duck_registry = SimpleNamespace(
        definitions=REGISTRY_V1.definitions, version=REGISTRY_V1.version, limits=REGISTRY_V1.limits
    )
    assert codes(run(registry=duck_registry)) == ["registry_integrity_failure"]

    # nor is a dict-like wrapper a Mapping inside the policy
    class DictLike:
        def __init__(self, inner):
            self._inner = inner

        def items(self):
            return self._inner.items()

        def get(self, key, default=None):
            return self._inner.get(key, default)

    policy = build_real_policy_reference()
    for field in ("actions", "autonomy_by_effect", "read_route_permissions"):
        wrapped = dataclasses.replace(policy, **{field: DictLike(getattr(policy, field))})
        assert codes(run(policy=wrapped)) == ["policy_reference_invalid"], field


# ----------------------------------------------------------------------
# registry snapshot isolation (INT-IMPL-2A-R3)
#
# After the pin check passes, NOTHING that happens to the original
# registry (a hook running inside project(), another thread, a hostile
# dict/list subclass) may change what the projection says. The snapshot
# is rebuilt from plain JSON, so these tests are deterministic.
# ----------------------------------------------------------------------

PRISTINE_PATTERN = "^acct_[a-z0-9]{6,12}$"


def fresh_registry():
    """Equal to the registry of record, sharing nothing with it (not even
    the limits object)."""
    base = build_v1_registry()

    return CapabilityRegistry(
        base.definitions, limits=dataclasses.replace(base.limits)
    )


def context_pattern(projection):
    for item in projection.output_schema["properties"]["steps"]["items"]["anyOf"]:
        if item["properties"]["capability_id"]["const"] == "account.get_context":
            return item["properties"]["params"]["properties"]["account_ref"]["pattern"]


def poison(registry):
    """What a hostile hook does to the ORIGINAL after the pin was checked."""
    definition = registry.get("account.get_context", 1)
    definition.input_schema["properties"]["account_ref"]["pattern"] = ".*"
    object.__setattr__(registry.limits, "max_steps", 99)


def poisoned(registry):
    definition = registry.get("account.get_context", 1)

    return (
        definition.input_schema["properties"]["account_ref"]["pattern"] == ".*"
        and registry.limits.max_steps == 99
    )


class PolicyHook(dict):
    def __init__(self, data, hook):
        super().__init__(data)
        self.hook = hook

    def __deepcopy__(self, memo):
        self.hook()
        return dict(copy.deepcopy(dict(self), memo))


class AccountsHook(dict):
    def __init__(self, data, hook):
        super().__init__(data)
        self.hook = hook

    def values(self):
        self.hook()
        return super().values()


def untouched(result, expected, pattern=PRISTINE_PATTERN):
    return (
        result.ok
        and result.projection == expected
        and result.projection.registry_digest == PINNED_REGISTRY_DIGEST
        and context_pattern(result.projection) == pattern
        and result.projection.output_schema["properties"]["steps"]["maxItems"] == 8
    )


def policy_with_hook(hook):
    policy = build_real_policy_reference()

    return dataclasses.replace(policy, actions=PolicyHook(policy.actions, hook))


def test_a_registry_changed_while_the_policy_is_copied_cannot_reach_the_projection():
    expected = project(base_request()).projection
    registry = fresh_registry()
    policy = policy_with_hook(lambda: poison(registry))

    result = project(base_request(registry=registry, policy=policy))

    assert poisoned(registry)  # the hook really ran, on the original
    assert untouched(result, expected)


def test_a_registry_changed_while_the_authority_is_copied_cannot_reach_the_projection():
    expected = project(base_request()).projection
    registry = fresh_registry()
    authority = make_authority()
    object.__setattr__(
        authority,
        "accounts",
        AccountsHook(authority.accounts, lambda: poison(registry)),
    )

    result = project(base_request(registry=registry, authority=authority))

    assert poisoned(registry)
    assert untouched(result, expected)


def test_interleaved_calls_and_a_registry_changed_in_between_stay_independent():
    expected = project(base_request()).projection
    registry, other = fresh_registry(), fresh_registry()
    seen = {}

    def hook():
        seen["nested"] = project(base_request(registry=other))
        poison(registry)

    result = project(base_request(registry=registry, policy=policy_with_hook(hook)))

    assert poisoned(registry) and not poisoned(other)
    assert untouched(result, expected)
    assert seen["nested"].ok and seen["nested"].projection == expected


def test_a_registry_changed_before_the_snapshot_is_rejected_not_projected():
    registry = fresh_registry()
    request = base_request(registry=registry)  # the pin is the pristine digest

    poison(registry)

    assert codes(project(request)) == ["registry_integrity_failure"]


def hostile_dict_class(alter):
    state = {"evil": False}

    class Shifty(dict):
        def view(self):
            data = {key: dict.__getitem__(self, key) for key in dict.keys(self)}

            if state["evil"]:
                data = copy.deepcopy(data)
                alter(data)

            return data

        def keys(self):
            return self.view().keys()

        def items(self):
            return self.view().items()

        def values(self):
            return self.view().values()

        def __iter__(self):
            return iter(self.view())

        def __getitem__(self, key):
            return self.view()[key]

        def get(self, key, default=None):
            return self.view().get(key, default)

    return Shifty, state


def hostile_list_class():
    state = {"evil": False}

    class ShiftyList(list):
        def __iter__(self):
            return iter([] if state["evil"] else list.__iter__(self))

        def __len__(self):
            return 0 if state["evil"] else list.__len__(self)

    return ShiftyList, state


def registry_with_schema_edit(edit):
    definitions = []

    for definition in build_v1_registry().definitions:
        if definition.capability_id == "account.get_context":
            schema = copy.deepcopy(dict(definition.input_schema))
            definition = dataclasses.replace(definition, input_schema=edit(schema))

        definitions.append(definition)

    return CapabilityRegistry(tuple(definitions))


def alter_pattern(data):
    data["properties"]["account_ref"]["pattern"] = ".*"


def test_a_hostile_dict_subclass_inside_the_registry_cannot_alter_the_projection():
    expected = project(base_request()).projection
    Shifty, state = hostile_dict_class(alter_pattern)
    registry = registry_with_schema_edit(lambda schema: Shifty(schema))

    assert registry.digest == PINNED_REGISTRY_DIGEST  # pristine while the flag is down

    result = project(
        base_request(
            registry=registry,
            policy=policy_with_hook(lambda: state.__setitem__("evil", True)),
        )
    )

    assert state["evil"] is True  # the flip happened after the pin check
    assert untouched(result, expected)

    # flipped BEFORE the snapshot: the copy sees altered content, the pin refuses it
    again = project(base_request(registry=registry))

    assert codes(again) == ["registry_integrity_failure"]


def test_a_hostile_list_subclass_inside_the_registry_cannot_alter_the_projection():
    expected = project(base_request()).projection
    ShiftyList, state = hostile_list_class()

    def edit(schema):
        schema["required"] = ShiftyList(schema["required"])

        return schema

    registry = registry_with_schema_edit(edit)

    assert registry.digest == PINNED_REGISTRY_DIGEST

    result = project(
        base_request(
            registry=registry,
            policy=policy_with_hook(lambda: state.__setitem__("evil", True)),
        )
    )

    assert state["evil"] is True
    assert untouched(result, expected)


def test_no_schema_is_ever_projected_under_a_valid_digest_when_it_was_altered():
    # whatever the stage at which the original changes, the answer is either
    # the untouched projection or a typed rejection, never altered content
    expected = project(base_request()).projection
    outcomes = set()

    for stage in ("policy", "authority", "before"):
        registry = fresh_registry()
        overrides = {"registry": registry}

        if stage == "policy":
            overrides["policy"] = policy_with_hook(lambda r=registry: poison(r))
        elif stage == "authority":
            authority = make_authority()
            object.__setattr__(
                authority,
                "accounts",
                AccountsHook(authority.accounts, lambda r=registry: poison(r)),
            )
            overrides["authority"] = authority

        request = base_request(**overrides)

        if stage == "before":
            poison(registry)

        result = project(request)

        assert poisoned(registry), stage
        assert untouched(result, expected) or codes(result) == [
            "registry_integrity_failure"
        ], stage
        outcomes.add("untouched" if result.ok else "rejected")

    assert outcomes == {"untouched", "rejected"}


def test_the_snapshot_is_plain_json_and_shares_no_container_with_the_original(
    monkeypatch,
):
    from app.intelligence.proposal import builder

    recorded = []
    real = builder._plain_json

    def spy(value):
        copied = real(value)
        recorded.append((value, copied))

        return copied

    monkeypatch.setattr(builder, "_plain_json", spy)
    registry = fresh_registry()

    assert run(registry=registry).ok
    assert len(recorded) == 2 * len(registry.definitions)

    def containers(value):
        stack, found = [value], []

        while stack:
            item = stack.pop()

            if isinstance(item, (dict, list)):
                found.append(item)
                stack.extend(item.values() if isinstance(item, dict) else item)

        return found

    def plain(value):
        for item in containers(value):
            if type(item) not in (dict, list):
                return False

            for leaf in item.values() if type(item) is dict else item:
                if not isinstance(leaf, (dict, list)) and type(leaf) not in (
                    str,
                    int,
                    bool,
                ):
                    return False

        return True

    for original, copied in recorded:
        assert plain(copied)
        assert not {id(x) for x in containers(original)} & {
            id(x) for x in containers(copied)
        }


def test_the_snapshot_refuses_types_instead_of_coercing_them():
    cases = {
        "tuple for a list": lambda s: s.update(required=tuple(s["required"])),
        "non-string key": lambda s: s.update({1: "x"}),
        "float": lambda s: s["properties"]["account_ref"].update(minLength=1.5),
        "null": lambda s: s["properties"]["account_ref"].update(title=None),
        "set": lambda s: s.update(required={"account_ref"}),
        "decomposed text": lambda s: s["properties"]["account_ref"].update(
            title="é"
        ),
        "arbitrary object": lambda s: s.update(extra=object()),
    }

    for label, change in cases.items():
        registry = fresh_registry()
        request = base_request(registry=registry)

        change(registry.get("account.get_context", 1).input_schema)

        assert codes(project(request)) == ["registry_integrity_failure"], label


def test_hostile_limits_are_refused_and_later_changes_are_isolated():
    class HostileInt(int):
        pass

    for label, value in (("int subclass", HostileInt(8)), ("bool", True), ("float", 8.0)):
        registry = fresh_registry()
        request = base_request(registry=registry)

        object.__setattr__(registry.limits, "max_steps", value)

        assert codes(project(request)) == ["registry_integrity_failure"], label

    # an ordinary change of the original limits after the call changes nothing
    registry = fresh_registry()
    projection = project(base_request(registry=registry)).projection

    object.__setattr__(registry.limits, "max_steps", 99)

    assert projection.output_schema["properties"]["steps"]["maxItems"] == 8


def test_the_projection_stays_untouched_by_a_concurrent_mutation_of_the_original():
    # supplementary and probabilistic: the deterministic tests above are the proof
    import sys
    import threading

    expected = project(base_request()).projection
    registry = fresh_registry()
    request = base_request(registry=registry)
    target = registry.get("account.get_context", 1).input_schema["properties"][
        "account_ref"
    ]
    stop = threading.Event()

    def flipper():
        toggle = False

        while not stop.is_set():
            target["pattern"] = ".*" if toggle else PRISTINE_PATTERN
            toggle = not toggle

    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    thread = threading.Thread(target=flipper, daemon=True)
    thread.start()
    altered = 0

    try:
        for _ in range(scaled(120)):
            result = project(request)

            if result.ok:
                if result.projection != expected:
                    altered += 1
            else:
                assert codes(result) == ["registry_integrity_failure"]
    finally:
        stop.set()
        thread.join()
        sys.setswitchinterval(interval)

    assert altered == 0
