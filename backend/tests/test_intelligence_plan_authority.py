"""INTELLIGENCE-PLANNING -- synthetic authority context and its use."""

import dataclasses
import typing

import pytest

from app.core.authorization import UserRole
from app.intelligence.planning import authority as a
from app.intelligence.planning.admission import AdmissionDecision
from app.intelligence.planning.authority import AccountAccess
from app.intelligence.planning.authority import AuthorityContextError
from app.intelligence.planning.authority import SyntheticAuthorityContext

from test_intelligence_plan_validator import ACCT
from test_intelligence_plan_validator import ALL_PERMISSIONS
from test_intelligence_plan_validator import OTHER_ACCT
from test_intelligence_plan_validator import codes_of
from test_intelligence_plan_validator import good_plan
from test_intelligence_plan_validator import make_authority
from test_intelligence_plan_validator import policy  # noqa: F401
from test_intelligence_plan_validator import read_only_plan
from test_intelligence_plan_validator import step_of
from test_intelligence_plan_validator import validate


pytestmark = pytest.mark.security


def test_known_roles_mirror_the_real_role_vocabulary():
    assert a.KNOWN_ROLES == frozenset(typing.get_args(UserRole))


def test_construction_rules():
    with pytest.raises(AuthorityContextError):
        make_authority(role="superuser")
    with pytest.raises(AuthorityContextError):
        make_authority(initiator_ref="  ")
    with pytest.raises(AuthorityContextError):
        make_authority(initiator_ref=None)
    with pytest.raises(AuthorityContextError):
        make_authority(permissions={"approval:read"})  # not a frozenset
    with pytest.raises(AuthorityContextError):
        make_authority(synthetic=False)
    with pytest.raises(AuthorityContextError):
        make_authority(accounts={ACCT: "yes"})


def test_a_synthetic_context_cannot_be_built_in_production(monkeypatch):
    for value in ("production", "PRODUCTION", " Production "):
        monkeypatch.setenv("APP_ENV", value)

        try:
            make_authority()
        except AuthorityContextError:
            continue

        pytest.fail(f"built a synthetic authority context under APP_ENV={value}")


def test_the_context_is_immutable_plain_data():
    ctx = make_authority()

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.role = "administrator"
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.permissions = frozenset()
    assert ctx.synthetic is True
    assert not any(
        hasattr(ctx, name) for name in ("execute", "authorize", "token", "session", "db")
    )


def test_account_access_hides_existence():
    ctx = make_authority(
        accounts={
            ACCT: AccountAccess(readable=True, manageable=False),
            "acct_denied1": AccountAccess(readable=False, manageable=False),
        }
    )

    assert ctx.access_to(ACCT) == AccountAccess(True, False)
    assert ctx.access_to("acct_denied1") is None
    assert ctx.access_to(OTHER_ACCT) is None  # unknown == inaccessible
    assert ctx.holds("approval:read") and not ctx.holds("nope")


def test_context_digest_is_stable_and_sensitive():
    base = make_authority()

    assert base.context_digest() == make_authority().context_digest()
    assert len(base.context_digest()) == 64
    assert base.context_digest() != make_authority(role="viewer").context_digest()
    assert base.context_digest() != make_authority(initiator_ref="user:43").context_digest()
    assert base.context_digest() != make_authority(session_elevated=True).context_digest()
    assert (
        base.context_digest()
        != make_authority(permissions=ALL_PERMISSIONS - {"clients.view"}).context_digest()
    )
    assert (
        base.context_digest()
        != make_authority(
            accounts={ACCT: AccountAccess(readable=True, manageable=False)}
        ).context_digest()
    )


# -- use inside the validator ---------------------------------------------


def test_missing_authority_context_is_rejected(policy):  # noqa: F811
    admission = validate(good_plan(), policy, authority=None)

    assert codes_of(admission) == ["authority_context_invalid"]
    assert validate(good_plan(), policy, authority="admin").decision is AdmissionDecision.REJECTED
    assert codes_of(validate(good_plan(), policy, authority={"role": "admin"})) == [
        "authority_context_invalid"
    ]


def test_every_required_permission_is_checked(policy):  # noqa: F811
    for missing in ("approval:read", "clients.view", "clients.manage", "skill:execute_mutating"):
        authority = make_authority(permissions=ALL_PERMISSIONS - {missing})
        admission = validate(good_plan(), policy, authority=authority)

        assert codes_of(admission) == ["authority_permission_missing"], missing

    # a read-only plan does not need the mutating permissions
    narrow = make_authority(permissions=frozenset({"approval:read", "clients.view"}))
    assert validate(read_only_plan(), policy, authority=narrow).admitted


def test_memory_recall_needs_memory_read(policy):  # noqa: F811
    from test_intelligence_plan_validator import make_step

    plan = read_only_plan()
    plan["steps"].append(
        make_step(
            "mem",
            "memory.recall",
            {"account_ref": ACCT, "query": "historico", "limit": 3},
            ["ctx"],
        )
    )
    without = make_authority(permissions=ALL_PERMISSIONS - {"memory:read"})

    assert validate(plan, policy).admitted
    assert codes_of(validate(plan, policy, authority=without)) == ["authority_permission_missing"]


def test_unknown_and_forbidden_accounts_are_indistinguishable(policy):  # noqa: F811
    forbidden = make_authority(
        accounts={ACCT: AccountAccess(readable=False, manageable=False)}
    )
    unknown = make_authority(accounts={})
    first = validate(good_plan(), policy, authority=forbidden)
    second = validate(good_plan(), policy, authority=unknown)

    assert codes_of(first) == codes_of(second) == ["authority_scope_inaccessible"]
    assert first.violations == second.violations  # same code, paths, details


def test_mutating_steps_need_manage_scope_reads_need_read_scope(policy):  # noqa: F811
    read_only_scope = make_authority(
        accounts={ACCT: AccountAccess(readable=True, manageable=False)}
    )

    assert validate(read_only_plan(), policy, authority=read_only_scope).admitted
    assert codes_of(validate(good_plan(), policy, authority=read_only_scope)) == [
        "authority_scope_inaccessible"
    ]

    manage_only = make_authority(
        accounts={ACCT: AccountAccess(readable=False, manageable=True)}
    )
    # good_plan also reads the account, which manage-only scope cannot do
    rejected = validate(good_plan(), policy, authority=manage_only)
    assert codes_of(rejected) == ["authority_scope_inaccessible"]
    assert {v.path for v in rejected.violations} == {
        "$.steps[0].params.account_ref",
        "$.steps[1].params.account_ref",
    }


def test_each_step_account_is_checked(policy):  # noqa: F811
    plan = good_plan()
    step_of(plan, "ctx")["params"]["account_ref"] = OTHER_ACCT

    assert codes_of(validate(plan, policy)) == ["authority_scope_inaccessible"]


def test_admission_records_the_initiator_but_never_a_credential(policy):  # noqa: F811
    admission = validate(good_plan(), policy)
    rendered = repr(admission)

    assert admission.initiator_ref == "user:42"
    assert admission.context_digest == make_authority().context_digest()
    assert "skill:execute_mutating" not in rendered
    assert admission.synthetic_authority is True
