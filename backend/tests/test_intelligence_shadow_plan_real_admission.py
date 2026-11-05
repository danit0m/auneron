"""A3-03 pending item 2: mapped plans through the REAL ``validate_plan``.

Offline and database-free. The registry is ``REGISTRY_V1`` and the policy is
``build_real_policy_reference()`` (built from the real action-space metadata,
authorization permissions and autonomy policy). Nothing is substituted.

Why ``validate_plan`` and not the A3-01 bridge: the bridge first needs a
verified ``Projection`` for a ``ProjectionRequest`` per scenario, and the
scenarios carry no projection inputs (no projection capability list, no
request snapshot). Building them here would invent a second, unreviewed
mapping. ``validate_plan`` is the stage the bridge ends in and the only one
that decides admission; the projection binding is already covered by
``test_intelligence_proposal_admission_bridge``. Projection binding is
therefore NOT exercised here.

The scripted label is the one ``_record`` in the adapter tests gives every
plan-channel output (``"admitted"``). Each case compares it with the real
label and reports both, scenario by scenario. A divergence is a finding for
the PO, not something to adjust: the label, the validator and the adapter are
left as they are. Abstention scenarios use the ``plan.v1`` channel only, with
both read-only and mutating plans; the abstention envelope is not ``plan.v1``
and never reaches the validator.
"""

import json

import pytest

from app.intelligence.gateway.contracts import DataClassification
from app.intelligence.planning.admission import AdmissionDecision
from app.intelligence.planning.authority import AccountAccess
from app.intelligence.planning.authority import SyntheticAuthorityContext
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.validator import validate_plan

import intelligence_shadow_scenarios as sc
from intelligence_shadow_plan_adapter import ACCOUNT_REF_MAP
from intelligence_shadow_plan_adapter import adapt_plan_v1
from test_intelligence_plan_validator import ALL_PERMISSIONS
from test_intelligence_plan_validator import build_real_policy_reference
from test_intelligence_shadow_plan_adapter import episode
from test_intelligence_shadow_plan_adapter import long_mutating_plan
from test_intelligence_shadow_plan_adapter import mutating_plan
from test_intelligence_shadow_plan_adapter import plan
from test_intelligence_shadow_plan_adapter import read_only_plan
from test_intelligence_shadow_plan_adapter import reads
from test_intelligence_shadow_plan_adapter import step

pytestmark = pytest.mark.contract

SCENARIOS = sc.scenario_by_id()
# Label that the adapter tests script for every plan.v1 output.
SCRIPTED_PLAN_LABEL = "admitted"
READ_ONLY = AdmissionDecision.ADMITTED_READ_ONLY
PENDING = AdmissionDecision.ADMITTED_PENDING_STEP_APPROVALS


@pytest.fixture(scope="module")
def real_policy():
    return build_real_policy_reference()


def authority_for(scenario, *, manageable=True):
    return SyntheticAuthorityContext(
        initiator_ref="user:42",
        role="manager",
        permissions=ALL_PERMISSIONS,
        accounts={
            ACCOUNT_REF_MAP[ref]: AccountAccess(readable=True, manageable=manageable)
            for ref in scenario.scope_refs
        },
    )


def real_admission(scenario, raw, policy, *, authority=None):
    return validate_plan(
        json.dumps(raw),
        registry=REGISTRY_V1,
        policy=policy,
        authority=authority or authority_for(scenario),
        data_classification=DataClassification.SYNTHETIC,
    )


def real_label(admission):
    """Scorer vocabulary for the real result; never a grant."""
    assert admission.internal_defect is False
    assert admission.is_grant is False

    return "admitted" if admission.admitted else "rejected"


def _cases():
    cases = []

    for scenario in sc.SCENARIOS:
        sid = scenario.scenario_id
        target = (
            scenario.expected.mutations[0].target_ref
            if not scenario.expects_abstention
            else scenario.accounts[0].ref
        )
        kind = "abstention" if scenario.expects_abstention else "eligible"

        cases.append((sid, kind, "read-only 1 step", read_only_plan(sid), READ_ONLY))
        cases.append(
            (sid, kind, "mutating 2 steps", mutating_plan(sid, target), PENDING)
        )

    for sid, target in (("s01", "ACC-01"), ("s03", "ACC-03")):
        kind = "abstention" if SCENARIOS[sid].expects_abstention else "eligible"

        for total in (5, 8):
            cases.append(
                (sid, kind, f"mutating {total} steps",
                 long_mutating_plan(sid, target, total), PENDING)
            )
            cases.append(
                (sid, kind, f"read-only {total} steps",
                 plan(sid, reads(sid, target, total)), READ_ONLY)
            )

    return cases


CASES = _cases()
CASE_IDS = [f"{sid}-{kind}-{shape.replace(' ', '_')}" for sid, kind, shape, _, _ in CASES]


@pytest.mark.parametrize("sid, kind, shape, raw, decision", CASES, ids=CASE_IDS)
def test_mapped_plan_label_agrees_with_the_real_admission(
    sid, kind, shape, raw, decision, real_policy
) -> None:
    scenario = SCENARIOS[sid]
    adapted = adapt_plan_v1(sid, raw)

    assert adapted.status == "mapped"  # only mapped plans are compared

    admission = real_admission(scenario, raw, real_policy)

    assert admission.violations == ()
    assert admission.decision is decision
    assert len(admission.step_keys) == len(raw["steps"])
    assert real_label(admission) == SCRIPTED_PLAN_LABEL


def test_matrix_includes_a_plan_of_five_to_eight_steps_in_each_class() -> None:
    long_cases = [c for c in CASES if 5 <= len(c[3]["steps"]) <= 8]

    assert {c[1] for c in long_cases} == {"eligible", "abstention"}
    assert {len(c[3]["steps"]) for c in long_cases} == {5, 8}
    assert {c[0] for c in CASES} == set(SCENARIOS)


def test_real_admission_ignores_eligibility_so_abstention_plans_are_admitted() -> None:
    """Coincidence with the label is structural only, never correctness."""
    policy = build_real_policy_reference()
    scenario = SCENARIOS["s03"]  # not due: abstention expected
    admission = real_admission(scenario, mutating_plan("s03", "ACC-03"), policy)

    assert scenario.expects_abstention
    assert admission.decision is PENDING
    assert admission.eligibility_verified is False


# ----------------------------------------------------------------------
# controls: the real call must be able to disagree with the scripted label
# ----------------------------------------------------------------------


def test_account_outside_the_authority_is_rejected_unlike_the_scripted_label(
    real_policy,
) -> None:
    scenario = SCENARIOS["s01"]
    raw = mutating_plan("s01", "ACC-01")
    admission = real_admission(
        scenario, raw, real_policy,
        authority=SyntheticAuthorityContext(
            initiator_ref="user:42",
            role="manager",
            permissions=ALL_PERMISSIONS,
            accounts={},
        ),
    )

    assert adapt_plan_v1("s01", raw).status == "mapped"
    assert admission.violations
    assert real_label(admission) == "rejected" != SCRIPTED_PLAN_LABEL


def test_unmanageable_account_is_rejected_for_a_mutating_plan_only(real_policy) -> None:
    scenario = SCENARIOS["s01"]
    authority = authority_for(scenario, manageable=False)
    mutating = real_admission(
        scenario, mutating_plan("s01", "ACC-01"), real_policy, authority=authority
    )
    read_only = real_admission(
        scenario, read_only_plan("s01"), real_policy, authority=authority
    )

    assert real_label(mutating) == "rejected"
    assert real_label(read_only) == "admitted"


def test_nine_step_plan_is_rejected_by_the_real_registry_limit(real_policy) -> None:
    scenario = SCENARIOS["s01"]
    raw = plan("s01", reads("s01", "ACC-01", 9))
    admission = real_admission(scenario, raw, real_policy)

    assert len(raw["steps"]) == REGISTRY_V1.limits.max_steps + 1
    assert adapt_plan_v1("s01", raw).status == "refused"
    assert real_label(admission) == "rejected" != SCRIPTED_PLAN_LABEL


# ----------------------------------------------------------------------
# A3 pending item 5: boundary regressions, real registry and real policy
# ----------------------------------------------------------------------

READ_PARAMS = {
    "account.get_context": lambda base: {"account_ref": base["account_ref"]},
    "action_space.evaluate": lambda base: dict(base),
    "nba.evaluate": lambda base: dict(base),
    "memory.recall": lambda base: {
        "account_ref": base["account_ref"], "query": "historico", "limit": 3,
    },
}


def _single_read_plan(capability_id):
    base = {"account_ref": ACCOUNT_REF_MAP["ACC-01"], "due_date": "2026-10-01"}

    return plan("s01", [step("only", capability_id, READ_PARAMS[capability_id](base))])


def test_capability_map_covers_exactly_the_enabled_proposable_capabilities() -> None:
    """Drift guard: a new proposable registry id must be mapped on purpose."""
    from intelligence_shadow_plan_adapter import CAPABILITY_MAP

    proposable = {
        d.capability_id
        for d in REGISTRY_V1.definitions
        if d.enabled and d.llm_proposable
    }

    assert proposable == set(CAPABILITY_MAP)


@pytest.mark.parametrize("capability_id", sorted(READ_PARAMS))
def test_each_read_capability_is_admitted_read_only_by_the_real_policy(
    capability_id, real_policy
) -> None:
    raw = _single_read_plan(capability_id)
    admission = real_admission(SCENARIOS["s01"], raw, real_policy)

    assert admission.violations == ()
    assert admission.decision is READ_ONLY


@pytest.mark.parametrize("capability_id", ["account.mark_paid", "work.escalate_to_human"])
def test_adapter_and_real_policy_both_refuse_non_proposable_capabilities(
    capability_id, real_policy
) -> None:
    raw = plan(
        "s01",
        [step("only", capability_id, episode("ACC-01", "s01"))],
    )
    admission = real_admission(SCENARIOS["s01"], raw, real_policy)

    assert adapt_plan_v1("s01", raw).status == "refused"
    assert real_label(admission) == "rejected"


def test_memory_recall_needs_the_real_memory_read_permission(real_policy) -> None:
    scenario = SCENARIOS["s01"]
    without_memory = SyntheticAuthorityContext(
        initiator_ref="user:42",
        role="manager",
        permissions=ALL_PERMISSIONS - {"memory:read"},
        accounts={
            ACCOUNT_REF_MAP["ACC-01"]: AccountAccess(readable=True, manageable=True)
        },
    )
    memory = real_admission(
        scenario, _single_read_plan("memory.recall"), real_policy,
        authority=without_memory,
    )
    other_read = real_admission(
        scenario, _single_read_plan("nba.evaluate"), real_policy,
        authority=without_memory,
    )

    assert real_label(memory) == "rejected"
    assert real_label(other_read) == "admitted"
