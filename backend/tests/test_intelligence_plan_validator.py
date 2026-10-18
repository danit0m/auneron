"""INTELLIGENCE-PLANNING -- deterministic plan.v1 validator.

Pure, offline tests: no database, no network, no provider. The shared
helpers at the top are imported by the sibling planning test modules.
"""

import copy
import json
import random

import pytest

from app.intelligence.gateway.contracts import DataClassification
from app.intelligence.planning import validator as validator_module
from app.intelligence.planning.admission import AdmissionDecision
from app.intelligence.planning.admission import PlanAdmission
from app.intelligence.planning.authority import AccountAccess
from app.intelligence.planning.authority import SyntheticAuthorityContext
from app.intelligence.planning.canonical import seal
from app.intelligence.planning.capabilities import PINNED_REGISTRY_DIGEST
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.codes import STAGE_OF
from app.intelligence.planning.codes import RejectionCode
from app.intelligence.planning.policy import ActionPolicyEntry
from app.intelligence.planning.policy import AutonomyEntry
from app.intelligence.planning.policy import PolicyReference
from app.intelligence.planning.schema import build_plan_schema
from app.intelligence.planning.validator import validate_plan


pytestmark = pytest.mark.contract

ACCT = "acct_7f3a9c"
OTHER_ACCT = "acct_000000"
DUE = "2026-09-30"
ELIGIBLE = {"fact": "episode.eligible_mark_overdue", "op": "eq", "value": True}
OBJECTIVE = "obj_k3m9x2ab"

ALL_PERMISSIONS = frozenset(
    {
        "approval:read",
        "clients.view",
        "clients.manage",
        "memory:read",
        "skill:execute_mutating",
        "work:create",
    }
)


# ----------------------------------------------------------------------
# shared helpers (also used by the sibling planning test modules)
# ----------------------------------------------------------------------


def make_step(
    key,
    capability_id,
    params,
    depends_on=(),
    preconditions=(),
    expected_outcomes=(),
    *,
    version=1,
    on_failure="abort",
    rationale="Justificativa sintetica.",
):
    return {
        "step_key": key,
        "capability_id": capability_id,
        "capability_version": version,
        "params": dict(params),
        "depends_on": list(depends_on),
        "preconditions": [dict(p) for p in preconditions],
        "expected_outcomes": [dict(p) for p in expected_outcomes],
        "on_failure": on_failure,
        "rationale": rationale,
    }


def good_plan():
    """ctx -> nba -> mark (the canonical admissible mutating plan)."""
    return {
        "contract": "plan.v1",
        "registry_version": "capreg.v1",
        "objective_ref": OBJECTIVE,
        "plan_version": 1,
        "summary": "Avaliar a conta e marcar como atrasada se elegivel.",
        "steps": [
            make_step("ctx", "account.get_context", {"account_ref": ACCT}),
            make_step(
                "nba",
                "nba.evaluate",
                {"account_ref": ACCT, "due_date": DUE},
                ["ctx"],
                expected_outcomes=[ELIGIBLE],
            ),
            make_step(
                "mark",
                "account.mark_overdue",
                {"account_ref": ACCT, "due_date": DUE},
                ["nba"],
                preconditions=[ELIGIBLE],
            ),
        ],
    }


def read_only_plan():
    plan = good_plan()
    plan["steps"] = plan["steps"][:2]
    return plan


def dumps(plan):
    return json.dumps(plan, ensure_ascii=False).encode("utf-8")


def make_authority(**overrides):
    options = {
        "initiator_ref": "user:42",
        "role": "manager",
        "permissions": ALL_PERMISSIONS,
        "accounts": {ACCT: AccountAccess(readable=True, manageable=True)},
    }
    options.update(overrides)
    return SyntheticAuthorityContext(**options)


def build_real_policy_reference():
    """PolicyReference assembled from the REAL modules (tests may import
    them; the planning package may not)."""
    import typing

    from app.core.action_space_evaluator import ACTION_METADATA
    from app.core.authorization import Permission
    from app.core.autonomy_policy import evaluate_skill_autonomy
    from app.models.skill import SkillVersion

    def autonomy(mode):
        decision = evaluate_skill_autonomy(
            actor_type="agent",
            version=SkillVersion(execution_mode=mode),
            capabilities=(),
        )
        return AutonomyEntry(
            disposition=decision.disposition,
            risk_level=decision.risk_level,
            approver_permission=decision.required_approval_permission,
        )

    return PolicyReference(
        actions={
            key: ActionPolicyEntry(**value)
            for key, value in ACTION_METADATA.items()
        },
        autonomy_by_effect={
            "READ": autonomy("read_only"),
            "MUTATE": autonomy("mutating"),
        },
        known_permissions=frozenset(typing.get_args(Permission)),
        read_route_permissions={
            "account.get_context": frozenset({"approval:read"}),
            "nba.evaluate": frozenset({"approval:read"}),
            "action_space.evaluate": frozenset({"approval:read"}),
            "memory.recall": frozenset({"memory:read"}),
        },
    )


@pytest.fixture(scope="module")
def policy():
    return build_real_policy_reference()


def validate(raw, policy, **overrides):
    options = {
        "registry": REGISTRY_V1,
        "policy": policy,
        "authority": make_authority(),
    }
    options.update(overrides)
    payload = raw if isinstance(raw, (bytes, str)) else dumps(raw)
    return validate_plan(payload, **options)


def codes_of(admission):
    return sorted({v.code for v in admission.violations})


# ----------------------------------------------------------------------
# positives
# ----------------------------------------------------------------------


def test_read_only_plan_is_admitted_but_nothing_is_verified(policy):
    admission = validate(read_only_plan(), policy)

    assert admission.decision is AdmissionDecision.ADMITTED_READ_ONLY
    assert admission.violations == ()
    assert admission.mutating_step_keys == ()
    assert admission.requires_step_approvals == ()
    assert admission.eligibility_verified is False
    assert admission.is_grant is False
    assert admission.execution_blocked_reason == "no_executor_in_this_phase"


def test_mutating_plan_is_admitted_pending_per_step_approval(policy):
    admission = validate(good_plan(), policy)

    assert admission.decision is AdmissionDecision.ADMITTED_PENDING_STEP_APPROVALS
    assert admission.mutating_step_keys == ("mark",)
    approval = admission.requires_step_approvals[0]
    assert (approval.step_key, approval.capability_id) == (
        "mark",
        "account.mark_overdue",
    )
    assert approval.approver_permission == "approval:decide"
    assert approval.corridor == "advisory_approval_work_skill"
    # I != D is DECLARED for the future gate, never enforced here
    assert admission.separation_requirements == ("initiator_ne_decider",)
    assert admission.initiator_ref == "user:42"
    assert admission.eligibility_verified is False


def test_digests_are_bound_to_content_and_registry(policy):
    admission = validate(good_plan(), policy)
    expected = seal(good_plan(), REGISTRY_V1.digest)

    assert admission.registry_digest == REGISTRY_V1.digest
    assert admission.execution_digest == expected["execution_digest"]
    assert admission.presentation_digest == expected["presentation_digest"]
    assert len(admission.idempotency_key) == 64
    assert admission.context_digest == make_authority().context_digest()


def test_validation_is_deterministic_and_order_independent(policy):
    first = validate(good_plan(), policy)
    second = validate(good_plan(), policy)
    shuffled = good_plan()
    shuffled["steps"].reverse()
    shuffled["steps"][0]["depends_on"].reverse()
    third = validate(shuffled, policy)

    assert first == second
    assert first.execution_digest == third.execution_digest
    assert first.presentation_digest == third.presentation_digest


def test_the_generated_schema_accepts_what_the_validator_admits(policy):
    from jsonschema import Draft202012Validator

    schema = Draft202012Validator(build_plan_schema(REGISTRY_V1))

    for plan in (good_plan(), read_only_plan()):
        assert validate(plan, policy).admitted
        assert schema.is_valid(plan)


# ----------------------------------------------------------------------
# negatives: one case per rejection code
# ----------------------------------------------------------------------


def mutate(fn):
    plan = good_plan()
    fn(plan)
    return plan


def step_of(plan, key):
    return next(s for s in plan["steps"] if s["step_key"] == key)


CASES = [
    # (id, payload builder, expected code)
    ("not_json", lambda: b"{nope", RejectionCode.INPUT_NOT_JSON),
    ("not_utf8", lambda: b"\xff\xfe{}", RejectionCode.INPUT_NOT_UTF8),
    ("too_large", lambda: b" " * 40_000, RejectionCode.INPUT_TOO_LARGE),
    ("not_object", lambda: b"[1,2]", RejectionCode.INPUT_NOT_OBJECT),
    ("duplicate_key", lambda: b'{"a":1,"a":2}', RejectionCode.INPUT_DUPLICATE_KEY),
    ("nan", lambda: b'{"a":NaN}', RejectionCode.INPUT_NON_FINITE_NUMBER),
    ("infinity", lambda: b'{"a":Infinity}', RejectionCode.INPUT_NON_FINITE_NUMBER),
    ("deep_nesting", lambda: b"[" * 5000, RejectionCode.INPUT_NESTING_TOO_DEEP),
    (
        "unknown_contract",
        lambda: mutate(lambda p: p.__setitem__("contract", "x.v1")),
        RejectionCode.CONTRACT_UNKNOWN,
    ),
    (
        "newer_contract",
        lambda: mutate(lambda p: p.__setitem__("contract", "plan.v2")),
        RejectionCode.CONTRACT_VERSION_UNSUPPORTED,
    ),
    (
        "registry_version",
        lambda: mutate(lambda p: p.__setitem__("registry_version", "capreg.v9")),
        RejectionCode.REGISTRY_VERSION_MISMATCH,
    ),
    (
        "model_sends_digest",
        lambda: mutate(lambda p: p.__setitem__("execution_digest", "0" * 64)),
        RejectionCode.SERVER_FIELD_FORBIDDEN,
    ),
    (
        "model_sends_registry_digest",
        lambda: mutate(lambda p: p.__setitem__("registry_digest", "0" * 64)),
        RejectionCode.SERVER_FIELD_FORBIDDEN,
    ),
    (
        "unknown_field",
        lambda: mutate(lambda p: p.__setitem__("extra", 1)),
        RejectionCode.SCHEMA_VIOLATION,
    ),
    (
        "missing_field",
        lambda: mutate(lambda p: p.pop("summary")),
        RejectionCode.SCHEMA_VIOLATION,
    ),
    (
        "empty_steps",
        lambda: mutate(lambda p: p.__setitem__("steps", [])),
        RejectionCode.SCHEMA_VIOLATION,
    ),
    (
        "bad_objective_ref",
        lambda: mutate(lambda p: p.__setitem__("objective_ref", "OBJ 1")),
        RejectionCode.SCHEMA_VIOLATION,
    ),
    (
        "null_value",
        lambda: mutate(lambda p: p.__setitem__("summary", None)),
        RejectionCode.VALUE_NULL_FORBIDDEN,
    ),
    (
        "float_value",
        lambda: mutate(lambda p: p.__setitem__("plan_version", 1.5)),
        RejectionCode.VALUE_FLOAT_FORBIDDEN,
    ),
    (
        "float_that_looks_integral",
        lambda: mutate(lambda p: p.__setitem__("plan_version", 1.0)),
        RejectionCode.VALUE_FLOAT_FORBIDDEN,
    ),
    (
        "integer_too_big",
        lambda: mutate(lambda p: p.__setitem__("plan_version", 2**53)),
        RejectionCode.INTEGER_OUT_OF_RANGE,
    ),
    (
        "summary_not_nfc",
        lambda: mutate(
            lambda p: p.__setitem__("summary", "á̧cao")
        ),
        RejectionCode.STRING_NOT_NFC,
    ),
    (
        "control_character",
        lambda: mutate(lambda p: p.__setitem__("summary", "linha\u0007")),
        RejectionCode.STRING_CONTROL_CHARACTER,
    ),
    (
        "lone_surrogate",
        lambda: json.dumps(
            mutate(lambda p: p.__setitem__("summary", "x\ud800"))
        ).encode("ascii"),
        RejectionCode.STRING_ENCODING_INVALID,
    ),
    (
        "too_many_steps",
        lambda: mutate(
            lambda p: p.__setitem__(
                "steps",
                [
                    make_step(f"s{i}", "account.get_context", {"account_ref": ACCT})
                    for i in range(9)
                ],
            )
        ),
        RejectionCode.LIMIT_STEPS,
    ),
    (
        "summary_too_long",
        lambda: mutate(lambda p: p.__setitem__("summary", "x" * 281)),
        RejectionCode.LIMIT_SUMMARY_LENGTH,
    ),
    (
        "rationale_too_long",
        lambda: mutate(
            lambda p: step_of(p, "ctx").__setitem__("rationale", "y" * 401)
        ),
        RejectionCode.LIMIT_RATIONALE_LENGTH,
    ),
    (
        "too_many_dependencies",
        lambda: mutate(
            lambda p: step_of(p, "mark").__setitem__(
                "depends_on", ["ctx", "nba", "a", "b"]
            )
        ),
        RejectionCode.LIMIT_DEPENDENCIES,
    ),
    (
        "too_many_preconditions",
        lambda: mutate(
            lambda p: step_of(p, "mark").__setitem__(
                "preconditions", [ELIGIBLE] * 5
            )
        ),
        RejectionCode.LIMIT_PREDICATES,
    ),
    (
        "unknown_capability",
        lambda: mutate(
            lambda p: step_of(p, "ctx").__setitem__("capability_id", "account.delete")
        ),
        RejectionCode.CAPABILITY_UNKNOWN,
    ),
    (
        "unsupported_capability_version",
        lambda: mutate(
            lambda p: step_of(p, "ctx").__setitem__("capability_version", 2)
        ),
        RejectionCode.CAPABILITY_VERSION_UNSUPPORTED,
    ),
    (
        "disabled_capability_mark_paid",
        lambda: mutate(
            lambda p: step_of(p, "mark").__setitem__("capability_id", "account.mark_paid")
        ),
        RejectionCode.CAPABILITY_DISABLED,
    ),
    (
        "capability_not_llm_proposable",
        lambda: mutate(
            lambda p: step_of(p, "mark").__setitem__(
                "capability_id", "work.escalate_to_human"
            )
        ),
        RejectionCode.CAPABILITY_NOT_LLM_PROPOSABLE,
    ),
    (
        "param_pattern",
        lambda: mutate(
            lambda p: step_of(p, "ctx")["params"].__setitem__("account_ref", "7")
        ),
        RejectionCode.PARAMS_INVALID,
    ),
    (
        "param_extra_key",
        lambda: mutate(
            lambda p: step_of(p, "ctx")["params"].__setitem__("note", "x")
        ),
        RejectionCode.PARAMS_INVALID,
    ),
    (
        "param_missing",
        lambda: mutate(lambda p: step_of(p, "nba")["params"].pop("due_date")),
        RejectionCode.PARAMS_INVALID,
    ),
    (
        "impossible_date",
        lambda: mutate(
            lambda p: step_of(p, "nba")["params"].__setitem__("due_date", "2026-02-30")
        ),
        RejectionCode.PARAMS_SEMANTIC_INVALID,
    ),
    (
        "unknown_fact",
        lambda: mutate(
            lambda p: step_of(p, "nba")["expected_outcomes"].append(
                {"fact": "account.balance", "op": "eq", "value": 1}
            )
        ),
        RejectionCode.PREDICATE_INVALID,
    ),
    (
        "predicate_wrong_type",
        lambda: mutate(
            lambda p: step_of(p, "nba")["expected_outcomes"].append(
                {"fact": "account.days_overdue", "op": "eq", "value": "x"}
            )
        ),
        RejectionCode.PREDICATE_INVALID,
    ),
    (
        "duplicate_step_key",
        lambda: mutate(lambda p: step_of(p, "nba").__setitem__("step_key", "ctx")),
        RejectionCode.DAG_DUPLICATE_STEP_KEY,
    ),
    (
        "self_dependency",
        lambda: mutate(lambda p: step_of(p, "ctx").__setitem__("depends_on", ["ctx"])),
        RejectionCode.DAG_SELF_DEPENDENCY,
    ),
    (
        "duplicate_dependency",
        lambda: mutate(
            lambda p: step_of(p, "nba").__setitem__("depends_on", ["ctx", "ctx"])
        ),
        RejectionCode.DAG_DUPLICATE_DEPENDENCY,
    ),
    (
        "dangling_dependency",
        lambda: mutate(
            lambda p: step_of(p, "nba").__setitem__("depends_on", ["ghost"])
        ),
        RejectionCode.DAG_UNKNOWN_DEPENDENCY,
    ),
    (
        "cycle",
        lambda: mutate(lambda p: step_of(p, "ctx").__setitem__("depends_on", ["mark"])),
        RejectionCode.DAG_CYCLE,
    ),
    (
        "mutating_limit",
        lambda: mutate(
            lambda p: p["steps"].append(
                make_step(
                    "mark2",
                    "account.mark_overdue",
                    {"account_ref": ACCT, "due_date": DUE},
                    ["nba"],
                    preconditions=[ELIGIBLE],
                )
            )
        ),
        RejectionCode.EFFECT_MUTATING_LIMIT,
    ),
    (
        "dataflow_reference",
        lambda: mutate(
            lambda p: p["steps"].append(
                make_step(
                    "mem",
                    "memory.recall",
                    {"account_ref": ACCT, "query": "{{nba.result}}", "limit": 3},
                    ["ctx"],
                )
            )
        ),
        RejectionCode.EFFECT_DATAFLOW_FORBIDDEN,
    ),
    (
        "eligibility_dependency_missing",
        lambda: mutate(lambda p: step_of(p, "mark").__setitem__("depends_on", ["ctx"])),
        RejectionCode.EFFECT_ELIGIBILITY_DEPENDENCY_MISSING,
    ),
    (
        "eligibility_precondition_missing",
        lambda: mutate(lambda p: step_of(p, "mark").__setitem__("preconditions", [])),
        RejectionCode.EFFECT_ELIGIBILITY_PRECONDITION_MISSING,
    ),
    (
        "token_like_summary",
        lambda: mutate(lambda p: p.__setitem__("summary", "A" * 20 + "b9" * 16)),
        RejectionCode.DATA_TOKEN_LIKE_VALUE,
    ),
    (
        "token_like_query",
        lambda: mutate(
            lambda p: p["steps"].append(
                make_step(
                    "mem",
                    "memory.recall",
                    {"account_ref": ACCT, "query": "0123456789abcdef01234567", "limit": 3},
                    ["ctx"],
                )
            )
        ),
        RejectionCode.DATA_TOKEN_LIKE_VALUE,
    ),
]


def test_each_rejection_code_is_reachable_and_stable(policy):
    # One loop (not parametrize): the autouse database cleanup of the
    # suite would otherwise dominate the cost of ~50 pure cases.
    failures = []

    for name, build, code in CASES:
        admission = validate(build(), policy)
        stages = {v.stage for v in admission.violations}

        ok = (
            admission.decision is AdmissionDecision.REJECTED
            and code.value in codes_of(admission)
            and admission.execution_digest is None
            and admission.is_grant is False
            # a rejection reports one stage only: the earliest that failed
            and stages == {int(STAGE_OF[code])}
        )

        if not ok:
            failures.append((name, code.value, codes_of(admission)))

    assert failures == []


def test_every_pipeline_code_is_exercised_by_these_tests():
    exercised = {case[2] for case in CASES}
    exercised |= {
        RejectionCode.REGISTRY_INTEGRITY_FAILURE,
        RejectionCode.DATA_REAL_DATA_REFUSED,
        RejectionCode.AUTHORITY_CONTEXT_INVALID,
        RejectionCode.AUTHORITY_PERMISSION_MISSING,
        RejectionCode.AUTHORITY_SCOPE_INACCESSIBLE,
        RejectionCode.POLICY_REFERENCE_MISSING,
        RejectionCode.POLICY_REFERENCE_MISMATCH,
        RejectionCode.DAG_DEPTH_EXCEEDED,
        RejectionCode.DAG_FAN_OUT_EXCEEDED,
        RejectionCode.LIMIT_JSON_DEPTH,
        RejectionCode.DIGEST_MISMATCH,
    }

    assert exercised == set(RejectionCode)


def test_dag_depth_and_fan_out_limits(policy):
    chain = good_plan()
    chain["steps"] = [make_step("a", "account.get_context", {"account_ref": ACCT})]
    previous = "a"

    for key in ("b", "c", "d", "e"):
        chain["steps"].append(
            make_step(key, "account.get_context", {"account_ref": ACCT}, [previous])
        )
        previous = key

    assert codes_of(validate(chain, policy)) == ["dag_depth_exceeded"]

    chain["steps"].pop()  # 4 nodes deep is the limit
    assert validate(chain, policy).admitted

    star = good_plan()
    star["steps"] = [make_step("r", "account.get_context", {"account_ref": ACCT})] + [
        make_step(f"x{i}", "account.get_context", {"account_ref": ACCT}, ["r"])
        for i in range(4)
    ]
    assert codes_of(validate(star, policy)) == ["dag_fan_out_exceeded"]
    star["steps"].pop()
    assert validate(star, policy).admitted


def test_json_depth_limit_is_enforced_by_the_limits_stage(policy):
    deep = 1

    for _ in range(9):
        deep = {"x": deep}

    plan = good_plan()
    step_of(plan, "ctx")["params"]["nested"] = deep  # depth 4 + 9 > 10

    admission = validate(plan, policy)

    assert codes_of(admission) == ["limit_json_depth"]

    shallow = good_plan()
    step_of(shallow, "ctx")["params"]["nested"] = {"x": 1}
    # shape passes the skeleton; the capability schema rejects it later
    assert codes_of(validate(shallow, policy)) == ["params_invalid"]


def test_scan_value_reports_depth_without_recursion():
    from app.intelligence.planning.canonical import scan_value

    violations, depth = scan_value({"a": {"b": {"c": [1]}}})

    assert violations == [] and depth == 4

    deep = []
    for _ in range(50_000):
        deep = [deep]

    assert scan_value(deep)[1] == 50_001


# ----------------------------------------------------------------------
# fail-fast stage precedence
# ----------------------------------------------------------------------


def test_the_earliest_failing_stage_wins_and_is_reported_alone(policy):
    plan = good_plan()
    plan["summary"] = None  # stage 3
    step_of(plan, "ctx")["capability_id"] = "account.delete"  # stage 5
    step_of(plan, "nba")["depends_on"] = ["ghost"]  # stage 7

    admission = validate(plan, policy, authority=None)

    assert codes_of(admission) == ["value_null_forbidden"]

    plan["summary"] = "ok"
    admission = validate(plan, policy, authority=None)
    assert codes_of(admission) == ["capability_unknown"]

    step_of(plan, "ctx")["capability_id"] = "account.get_context"
    admission = validate(plan, policy, authority=None)
    assert codes_of(admission) == ["dag_unknown_dependency"]


def test_all_violations_of_the_failing_stage_are_collected_sorted(policy):
    plan = good_plan()
    step_of(plan, "ctx")["capability_id"] = "account.delete"
    step_of(plan, "nba")["capability_id"] = "nba.destroy"
    step_of(plan, "mark")["capability_version"] = 9

    admission = validate(plan, policy)

    assert sorted(v.code for v in admission.violations) == [
        "capability_unknown",
        "capability_unknown",
        "capability_version_unsupported",
    ]
    assert list(admission.violations) == sorted(admission.violations)
    assert validate(plan, policy) == admission


def test_violations_never_echo_input_values(policy):
    canary = "CANARY-5e1b7c-NEVER-ECHO"
    plan = good_plan()
    step_of(plan, "ctx")["params"]["account_ref"] = canary
    step_of(plan, "ctx")["rationale"] = canary

    admission = validate(plan, policy)
    rendered = repr(admission) + str(admission.audit_view())

    assert admission.decision is AdmissionDecision.REJECTED
    assert canary not in rendered


# ----------------------------------------------------------------------
# registry integrity, proposer, data classification
# ----------------------------------------------------------------------


def test_a_registry_that_does_not_match_the_pin_rejects_every_plan(policy):
    admission = validate(good_plan(), policy, pinned_registry_digest="0" * 64)

    assert codes_of(admission) == ["registry_integrity_failure"]
    assert PINNED_REGISTRY_DIGEST == REGISTRY_V1.digest


def test_only_the_llm_proposer_is_supported_in_this_phase(policy):
    for kind in ("human", "system", ""):
        with pytest.raises(ValueError):
            validate(good_plan(), policy, proposer_kind=kind)


def test_real_data_is_refused_by_classification_and_by_a_tampered_constant(
    policy, monkeypatch
):
    admission = validate(
        good_plan(), policy, data_classification=DataClassification.REAL
    )
    assert codes_of(admission) == ["data_real_data_refused"]

    monkeypatch.setattr(validator_module, "REAL_DATA_ALLOWED", True)
    admission = validate(good_plan(), policy)
    assert codes_of(admission) == ["data_real_data_refused"]  # fails closed


# ----------------------------------------------------------------------
# property: random mutation never breaks the invariants
# ----------------------------------------------------------------------


def random_mutation(rng, plan):
    plan = copy.deepcopy(plan)
    steps = plan["steps"]
    choice = rng.randrange(14)

    if choice == 0:
        rng.choice(steps)["capability_id"] = rng.choice(
            ["account.mark_paid", "work.escalate_to_human", "x.y", "nba.evaluate"]
        )
    elif choice == 1:
        rng.choice(steps)["depends_on"] = rng.sample(
            [s["step_key"] for s in steps] + ["ghost"], rng.randrange(0, 4)
        )
    elif choice == 2:
        rng.choice(steps)["params"][rng.choice(["account_ref", "due_date", "limit"])] = rng.choice(
            ["acct_aaaaaa", "2026-13-01", 5, None, 1.5, "", "{{x}}"]
        )
    elif choice == 3:
        steps.append(copy.deepcopy(rng.choice(steps)))
    elif choice == 4 and len(steps) > 1:
        steps.pop(rng.randrange(len(steps)))
    elif choice == 5:
        plan[rng.choice(["summary", "objective_ref", "contract"])] = rng.choice(
            ["", None, "plan.v2", 7, "é" * 300]
        )
    elif choice == 6:
        rng.choice(steps)["preconditions"] = rng.choice([[], [ELIGIBLE], [ELIGIBLE] * 6])
    elif choice == 7:
        rng.choice(steps)["step_key"] = rng.choice(["ctx", "NBA", "a" * 40, "ok_key"])
    elif choice == 8:
        rng.choice(steps)["on_failure"] = rng.choice(["abort", "escalate", "ignore"])
    elif choice == 9:
        plan["unexpected"] = 1
    elif choice == 10:
        rng.choice(steps)["rationale"] = rng.choice(["x" * 500, "ok", "A" * 40])
    elif choice == 11:
        rng.shuffle(steps)
    elif choice == 12:
        rng.choice(steps)["capability_version"] = rng.choice([0, 1, 2, True])
    else:
        rng.choice(steps)["expected_outcomes"] = rng.choice(
            [[], [ELIGIBLE], [{"fact": "x", "op": "eq", "value": 1}]]
        )

    return plan


def test_random_mutations_never_crash_and_admitted_plans_obey_every_invariant(
    policy,
):
    from jsonschema import Draft202012Validator

    rng = random.Random(20261017)
    schema = Draft202012Validator(build_plan_schema(REGISTRY_V1))
    admitted = rejected = 0

    for _ in range(1500):
        plan = good_plan()

        for _ in range(rng.randrange(1, 4)):
            plan = random_mutation(rng, plan)

        raw = dumps(plan)
        first = validate(raw, policy)
        again = validate(raw, policy)

        assert first == again  # deterministic

        if not first.admitted:
            rejected += 1
            assert first.violations and first.execution_digest is None
            continue

        admitted += 1
        keys = [s["step_key"] for s in plan["steps"]]
        assert len(keys) == len(set(keys)) <= 8
        assert schema.is_valid(plan)  # layered admit => generated schema admit
        assert all(
            s["capability_id"]
            not in ("account.mark_paid", "work.escalate_to_human")
            for s in plan["steps"]
        )
        mutating = [s for s in plan["steps"] if s["capability_id"] == "account.mark_overdue"]
        assert len(mutating) <= 1
        assert first.eligibility_verified is False and first.is_grant is False

        for m in mutating:  # eligibility evidence is really an ancestor
            ancestors, stack = set(), list(m["depends_on"])
            by_key = {s["step_key"]: s for s in plan["steps"]}

            while stack:
                k = stack.pop()
                if k not in ancestors:
                    ancestors.add(k)
                    stack += by_key[k]["depends_on"]

            assert any(
                by_key[k]["capability_id"] in ("nba.evaluate", "action_space.evaluate")
                and by_key[k]["params"].get("account_ref") == m["params"]["account_ref"]
                and by_key[k]["params"].get("due_date") == m["params"]["due_date"]
                for k in ancestors
            )

    assert admitted > 20 and rejected > 250  # both outcomes are exercised


def test_admission_type_is_not_a_boolean(policy):
    admission = validate(good_plan(), policy)

    assert isinstance(admission, PlanAdmission)
    with pytest.raises(TypeError):
        bool(admission)
    with pytest.raises(TypeError):
        if admission:  # noqa: SIM102
            pass


# ----------------------------------------------------------------------
# boundary values: exactly at the limit is admitted, one past is not
# ----------------------------------------------------------------------


def test_every_limit_admits_the_exact_boundary(policy):
    plan = read_only_plan()
    plan["summary"] = ("ab " * 94)[:280]
    assert validate(plan, policy).admitted

    plan = read_only_plan()
    step_of(plan, "ctx")["rationale"] = ("ab " * 134)[:400]
    assert validate(plan, policy).admitted

    plan = read_only_plan()
    plan["steps"] = [
        make_step(f"s{i}", "account.get_context", {"account_ref": ACCT})
        for i in range(8)
    ]
    assert validate(plan, policy).admitted

    plan = read_only_plan()
    plan["steps"] = [
        make_step(f"r{i}", "account.get_context", {"account_ref": ACCT})
        for i in range(3)
    ] + [
        make_step(
            "tail",
            "account.get_context",
            {"account_ref": ACCT},
            ["r0", "r1", "r2"],
        )
    ]
    assert validate(plan, policy).admitted

    plan = read_only_plan()
    step_of(plan, "nba")["expected_outcomes"] = [
        {"fact": "account.days_overdue", "op": "gte", "value": n} for n in range(4)
    ]
    step_of(plan, "nba")["preconditions"] = [
        {"fact": "account.days_overdue", "op": "gte", "value": n} for n in range(4)
    ]
    assert validate(plan, policy).admitted


def test_one_past_each_limit_is_rejected(policy):
    plan = read_only_plan()
    step_of(plan, "nba")["depends_on"] = ["ctx", "a", "b", "c"]
    assert codes_of(validate(plan, policy)) == ["limit_dependencies"]

    plan = read_only_plan()
    step_of(plan, "nba")["expected_outcomes"] = [
        {"fact": "account.days_overdue", "op": "gte", "value": n} for n in range(5)
    ]
    assert codes_of(validate(plan, policy)) == ["limit_predicates"]


def test_json_depth_boundary_is_exactly_ten(policy):
    def with_depth(extra):
        nested = 1

        for _ in range(extra):
            nested = {"x": nested}

        plan = good_plan()
        step_of(plan, "ctx")["params"]["nested"] = nested
        return plan

    from app.intelligence.planning.canonical import scan_value

    assert scan_value(with_depth(6))[1] == 10
    assert codes_of(validate(with_depth(6), policy)) == ["params_invalid"]
    assert scan_value(with_depth(7))[1] == 11
    assert codes_of(validate(with_depth(7), policy)) == ["limit_json_depth"]


def test_input_size_boundary_is_exactly_32768_bytes(policy):
    body = dumps(good_plan())
    padded = body + b" " * (32768 - len(body))

    assert len(padded) == 32768
    assert validate(padded, policy).admitted
    assert codes_of(validate(padded + b" ", policy)) == ["input_too_large"]


def test_precondition_must_use_the_required_operator(policy):
    plan = good_plan()
    wrong = {"fact": "episode.eligible_mark_overdue", "op": "ne", "value": True}
    step_of(plan, "mark")["preconditions"] = [wrong]

    assert codes_of(validate(plan, policy)) == [
        "effect_eligibility_precondition_missing"
    ]

    wrong_value = {"fact": "episode.eligible_mark_overdue", "op": "eq", "value": False}
    step_of(plan, "mark")["preconditions"] = [wrong_value]
    assert codes_of(validate(plan, policy)) == [
        "effect_eligibility_precondition_missing"
    ]


def test_prerequisite_evidence_must_be_a_listed_capability(policy):
    """Generic rule, exercised with a private registry: an ancestor with the
    same parameters but a capability outside `any_of` is not evidence."""
    import dataclasses

    from app.intelligence.planning.capabilities import CapabilityRegistry
    from app.intelligence.planning.capabilities import EffectClass
    from app.intelligence.planning.capabilities import ApprovalKind
    from app.intelligence.planning.capabilities import ApprovalPolicy
    from app.intelligence.planning.capabilities import CapabilityDefinition

    nba = REGISTRY_V1.get("nba.evaluate", 1)
    decoy = CapabilityDefinition(
        capability_id="demo.episode_peek",
        version=1,
        registered=True,
        enabled=True,
        llm_proposable=True,
        executable=False,
        effect_class=EffectClass.READ,
        input_schema=dict(nba.input_schema),
        output_schema={"type": "object"},
        required_permissions=nba.required_permissions,
        approval_policy=ApprovalPolicy(ApprovalKind.NONE),
    )
    private = CapabilityRegistry(REGISTRY_V1.definitions + (decoy,))
    reference = dataclasses.replace(
        build_real_policy_reference(),
        read_route_permissions={
            **build_real_policy_reference().read_route_permissions,
            "demo.episode_peek": frozenset({"approval:read"}),
        },
    )
    plan = good_plan()
    step_of(plan, "nba")["capability_id"] = "demo.episode_peek"
    admission = validate(
        plan,
        reference,
        registry=private,
        pinned_registry_digest=private.digest,
    )

    assert codes_of(admission) == ["effect_eligibility_dependency_missing"]


def test_an_integer_never_stands_in_for_the_boolean_precondition(policy):
    # `1 == True` in Python: the predicate vocabulary (stage 6) is what keeps
    # the eligibility precondition from being satisfied by an integer.
    plan = good_plan()
    step_of(plan, "mark")["preconditions"] = [
        {"fact": "episode.eligible_mark_overdue", "op": "eq", "value": 1}
    ]

    assert codes_of(validate(plan, policy)) == ["predicate_invalid"]
