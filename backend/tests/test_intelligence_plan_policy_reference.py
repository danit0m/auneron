"""INTELLIGENCE-PLANNING -- reconciliation with the REAL policy modules.

The planning package may not import the existing governance modules;
these tests may, and use them as the source of truth the registry must
agree with. A disagreement is a rejection, never a more permissive
reading.
"""

import ast
import copy
import dataclasses
import inspect
import os
import re
import typing
from datetime import date
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.action_space_evaluator import ACTION_METADATA
from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalConsumptionConflictError
from app.core.approval_errors import ApprovalElevationRequiredError
from app.core.approval_errors import ApprovalValidationError
from app.core.authorization import Permission
from app.core.authorization import ROLE_PERMISSIONS
from app.core.security import API_KEY_HEADER_NAME
from app.main import app
from app.models.account import Account
from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
from app.models.user import User
from app.services.approval_service import ApprovalRequester
from app.services.approval_service import ApprovalService
from app.services.governed_skill_execution import GovernedSkillExecutionService
from app.services.skill_runtime import SkillInvocationActor
from app.services.skill_service import CapabilityInput
from app.intelligence.planning import policy as p
from app.intelligence.planning.admission import AdmissionDecision
from app.intelligence.planning.capabilities import REGISTRY_V1
from app.intelligence.planning.capabilities import ApprovalKind
from app.intelligence.planning.capabilities import ApprovalPolicy
from app.intelligence.planning.capabilities import CapabilityDefinition
from app.intelligence.planning.capabilities import EffectClass
from app.intelligence.planning.policy import ActionPolicyEntry
from app.intelligence.planning.policy import AutonomyEntry
from app.intelligence.planning.policy import PolicyReference
from app.intelligence.planning.policy import reconcile_capability
from app.intelligence.planning.policy import reconcile_registry

from test_approval_service import _published_version as approval_version
from test_approval_service import _user as approval_user
from test_governed_skill_execution import _actor as agent_actor
from test_governed_skill_execution import _approve as approve_as_agent
from test_governed_skill_execution import _published_version as governed_version
from test_governed_skill_execution import _user as governed_user
from test_human_account_mark_overdue_execution import APPROVER_EMAIL
from test_human_account_mark_overdue_execution import APPROVER_PASSWORD
from test_human_account_mark_overdue_execution import _current_user
from test_human_account_mark_overdue_execution import _execute_url
from test_human_account_mark_overdue_execution import _governed_object_counts
from test_human_account_mark_overdue_execution import _setup_approved_episode
from test_intelligence_plan_validator import build_real_policy_reference
from test_intelligence_plan_validator import codes_of
from test_intelligence_plan_validator import good_plan
from test_intelligence_plan_validator import policy  # noqa: F401
from test_intelligence_plan_validator import read_only_plan
from test_intelligence_plan_validator import validate


pytestmark = pytest.mark.security

BACKEND = Path(__file__).resolve().parents[1]
ROUTES = BACKEND / "app" / "api" / "routes"

READ_ROUTES = {
    "account.get_context": ("customer_context.py", "get_account_customer_context"),
    "nba.evaluate": ("nba_policy.py", "get_account_nba_decision"),
    "action_space.evaluate": (
        "action_space_evaluator.py",
        "get_account_action_space_evaluation",
    ),
    "memory.recall": ("memory.py", "recall_memories"),
}


def route_permissions(filename, function):
    """require_permission("x") literals reachable from one route function:
    its decorators, defaults and body, plus the module-level lists its
    decorators reference (read_dependencies)."""
    tree = ast.parse((ROUTES / filename).read_text(encoding="utf-8"))
    module_lists = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    node = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function
    )
    scan = [node]

    for decorator in node.decorator_list:
        for keyword in getattr(decorator, "keywords", []):
            if isinstance(keyword.value, ast.Name) and keyword.value.id in module_lists:
                scan.append(module_lists[keyword.value.id])

    found = set()

    for root in scan:
        for child in ast.walk(root):
            if (
                isinstance(child, ast.Call)
                and getattr(child.func, "id", None) == "require_permission"
                and child.args
                and isinstance(child.args[0], ast.Constant)
            ):
                found.add(child.args[0].value)

    return frozenset(found)


# -- the real sources agree with the registry ------------------------------


def test_the_registry_reconciles_cleanly_with_the_real_policy(policy):  # noqa: F811
    assert reconcile_registry(REGISTRY_V1, policy) == {}


def test_the_read_route_snapshot_matches_the_real_routes(policy):  # noqa: F811
    for capability_id, (filename, function) in READ_ROUTES.items():
        actual = route_permissions(filename, function)

        assert actual, capability_id  # the scanner really found a guard
        assert policy.read_route_permissions[capability_id] == actual, capability_id
        declared = set(REGISTRY_V1.get(capability_id, 1).required_permissions)
        assert actual <= declared, capability_id


def test_the_route_scanner_detects_a_change():
    source = (
        "from x import require_permission\n"
        "read_dependencies = [Depends(require_permission('a:b'))]\n"
        "@router.get('/', dependencies=read_dependencies)\n"
        "def fn(x=Depends(require_permission('c:d'))):\n"
        "    return require_permission('e:f')\n"
    )
    tree = ast.parse(source)

    assert {n.args[0].value for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "require_permission"} == {"a:b", "c:d", "e:f"}


def test_every_declared_permission_exists_in_the_rbac_vocabulary():
    known = set(typing.get_args(Permission))

    for d in REGISTRY_V1.definitions:
        assert set(d.required_permissions) <= known, d.capability_id
        approver = d.approval_policy.approver_permission
        assert approver is None or approver in known


def test_the_action_metadata_pins():
    assert ACTION_METADATA["account.mark_overdue"] == {
        "capability_kind": "skill_action",
        "initiation_actor": "agent",
        "authority_model": "approval",
        "required_authority": "approval:decide",
        "execution_corridor": "advisory_approval_work_skill",
    }
    assert ACTION_METADATA["account.mark_paid"]["initiation_actor"] == "user"
    assert ACTION_METADATA["escalate_to_human"]["initiation_actor"] == "rbac_actor"


def test_inv3_llm_proposable_follows_the_initiation_actor_for_governed_actions():
    for d in REGISTRY_V1.definitions:
        if d.reference_key is None:
            continue  # reads have no action reference and no INV-3

        entry = ACTION_METADATA[d.reference_key]
        # mark_paid is disabled on top of failing INV-3 (user-initiated)
        assert d.llm_proposable == (entry.get("initiation_actor") == "agent"), d.capability_id


def test_no_artificial_action_metadata_entries_exist_for_reads():
    assert set(ACTION_METADATA) == {
        "account.mark_overdue",
        "account.mark_paid",
        "escalate_to_human",
    }


def test_admin_like_roles_do_not_change_the_registry_requirements():
    # approval:decide must exist for some role, or the corridor is unreachable
    deciders = [r for r, perms in ROLE_PERMISSIONS.items() if "approval:decide" in perms]

    assert deciders


# -- mutants: every disagreement is detected ------------------------------


def mutated(**changes):
    base = build_real_policy_reference()
    return dataclasses.replace(base, **changes)


def with_action(key, **changes):
    base = build_real_policy_reference()
    actions = dict(base.actions)
    actions[key] = dataclasses.replace(actions[key], **changes)
    return dataclasses.replace(base, actions=actions)


def with_autonomy(effect, **changes):
    base = build_real_policy_reference()
    autonomy = dict(base.autonomy_by_effect)
    autonomy[effect] = dataclasses.replace(autonomy[effect], **changes)
    return dataclasses.replace(base, autonomy_by_effect=autonomy)


def tokens(capability_id, reference):
    return reconcile_capability(REGISTRY_V1.get(capability_id, 1), reference)


MUTANTS = [
    ("initiation_actor", "account.mark_overdue", with_action("account.mark_overdue", initiation_actor="user"), p.LLM_PROPOSABLE_VS_INITIATION_ACTOR),
    ("corridor", "account.mark_overdue", with_action("account.mark_overdue", execution_corridor="other"), p.CORRIDOR),
    ("authority_model", "account.mark_overdue", with_action("account.mark_overdue", authority_model="rbac"), p.RBAC_POLICY_MISMATCH),
    ("required_authority", "account.mark_overdue", with_action("account.mark_overdue", required_authority="approval:read"), p.APPROVAL_POLICY_VS_AUTHORITY_MODEL),
    ("kind", "account.mark_overdue", with_action("account.mark_overdue", capability_kind="work_action"), p.KIND_VS_APPROVAL),
    ("autonomy_disposition", "account.mark_overdue", with_autonomy("MUTATE", disposition="autonomous_allowed"), p.AUTONOMY_POLICY_MISMATCH),
    ("autonomy_approver", "account.mark_overdue", with_autonomy("MUTATE", approver_permission="approval:read"), p.AUTONOMY_POLICY_MISMATCH),
    ("unknown_permission", "account.mark_overdue", mutated(known_permissions=frozenset(typing.get_args(Permission)) - {"approval:decide"}), p.UNKNOWN_PERMISSION),
    ("read_autonomy", "nba.evaluate", with_autonomy("READ", disposition="approval_required"), p.READ_AUTONOMY_MISMATCH),
    ("read_reference_missing", "nba.evaluate", mutated(read_route_permissions={}), p.READ_REFERENCE_MISSING),
    ("read_permissions", "nba.evaluate", mutated(read_route_permissions={"nba.evaluate": frozenset({"approval:read", "extra:perm"})}), p.READ_PERMISSIONS_MISSING),
    ("rbac_authority", "work.escalate_to_human", with_action("escalate_to_human", required_authority="clients.manage"), p.RBAC_POLICY_MISMATCH),
]


def test_each_mutant_is_detected_with_its_token():
    survivors = []

    for name, capability_id, reference, token in MUTANTS:
        if token not in tokens(capability_id, reference):
            survivors.append(name)

    assert survivors == []


def test_unmutated_reference_has_no_mismatch_for_any_capability(policy):  # noqa: F811
    for d in REGISTRY_V1.definitions:
        assert reconcile_capability(d, policy) == (), d.capability_id


def test_missing_action_entry_and_missing_autonomy_entry():
    base = build_real_policy_reference()
    no_actions = dataclasses.replace(base, actions={})
    no_autonomy = dataclasses.replace(base, autonomy_by_effect={})

    assert p.NO_REFERENCE_ENTRY in tokens("account.mark_overdue", no_actions)
    assert p.AUTONOMY_POLICY_MISMATCH in tokens("account.mark_overdue", no_autonomy)
    assert p.READ_AUTONOMY_MISMATCH in tokens("nba.evaluate", no_autonomy)


def test_executable_and_propose_definitions_are_never_reconcilable(policy):  # noqa: F811
    forced = copy.copy(REGISTRY_V1.get("nba.evaluate", 1))
    object.__setattr__(forced, "executable", True)  # bypass the constructor on purpose

    assert p.EXECUTABLE_NOT_ALLOWED in reconcile_capability(forced, policy)

    propose = CapabilityDefinition(
        capability_id="demo.propose",
        version=1,
        registered=True,
        enabled=True,
        llm_proposable=True,
        executable=False,
        effect_class=EffectClass.PROPOSE,
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        required_permissions=("approval:read",),
        approval_policy=ApprovalPolicy(ApprovalKind.PER_STEP_APPROVAL, "approval:decide"),
    )
    assert p.EFFECT_UNSUPPORTED in reconcile_capability(propose, policy)


def test_result_is_sorted_unique_and_content_free(policy):  # noqa: F811
    bad = mutated(known_permissions=frozenset(), read_route_permissions={}, autonomy_by_effect={})
    result = tokens("nba.evaluate", bad)

    assert list(result) == sorted(set(result))
    assert all(token.replace("_", "").isalpha() for token in result)


# -- integration with the validator ------------------------------------------


def test_missing_policy_reference_rejects_plans_that_use_capabilities():
    admission = validate(good_plan(), None)

    assert codes_of(admission) == ["policy_reference_missing"]


def test_mismatch_in_a_used_capability_rejects_the_plan():
    admission = validate(good_plan(), with_action("account.mark_overdue", initiation_actor="user"))

    assert codes_of(admission) == ["policy_reference_mismatch"]
    assert admission.decision is AdmissionDecision.REJECTED
    detail = " ".join(v.detail for v in admission.violations)
    assert p.LLM_PROPOSABLE_VS_INITIATION_ACTOR in detail


def test_a_mismatch_in_an_unused_capability_does_not_block_other_plans():
    broken_paid = with_action("account.mark_paid", execution_corridor="other")

    assert reconcile_registry(REGISTRY_V1, broken_paid)  # the registry check sees it
    assert validate(read_only_plan(), broken_paid).admitted  # a plan not using it is fine
    assert validate(good_plan(), broken_paid).admitted


def test_a_mutated_read_reference_rejects_read_plans():
    admission = validate(read_only_plan(), mutated(read_route_permissions={}))

    assert codes_of(admission) == ["policy_reference_mismatch"]


def test_policy_reference_is_plain_data():
    reference = build_real_policy_reference()

    assert isinstance(reference, PolicyReference)
    assert dataclasses.is_dataclass(reference) and reference.source == "synthetic"
    assert isinstance(next(iter(reference.actions.values())), ActionPolicyEntry)
    assert isinstance(next(iter(reference.autonomy_by_effect.values())), AutonomyEntry)
    with pytest.raises(dataclasses.FrozenInstanceError):
        reference.source = "other"


def test_required_permissions_must_exist_in_the_rbac_vocabulary():
    known = frozenset(typing.get_args(Permission))

    assert p.UNKNOWN_PERMISSION in tokens(
        "nba.evaluate", mutated(known_permissions=known - {"clients.view"})
    )
    assert p.UNKNOWN_PERMISSION in tokens(
        "account.mark_overdue", mutated(known_permissions=known - {"skill:execute_mutating"})
    )
    assert p.UNKNOWN_PERMISSION not in tokens("nba.evaluate", mutated(known_permissions=known))


# ----------------------------------------------------------------------
# approval-flow drift guard: contracts and REAL behaviour, no source text
#
# The planning layer models approval PER STEP and only DECLARES
# separation requirements. These tests pin what the EXISTING flow does
# today, using the real services against the test database. If one of
# them fails because the flow became STRICTER, that is a conscious
# change: update the registry `separation_requirements`,
# PLANNING_CONTRACT.md and the pin together, in a reviewed commit.
# ----------------------------------------------------------------------

SEPARATION_MESSAGE = "separação entre solicitante e decisor"
EXECUTOR_MESSAGE = (
    "Executor não pode ser o mesmo usuário que decidiu a aprovação."
)
MUTATING = (
    CapabilityInput(
        capability_key="accounts.update",
        access_mode="write",
        resource_scope="internal",
    ),
)
EXTERNAL = (
    CapabilityInput(
        capability_key="crm.sync",
        access_mode="execute",
        resource_scope="external",
    ),
)


def user_requester(user):
    return ApprovalRequester(
        actor_type="user",
        actor_reference=f"user:{user.id}",
        actor_user_id=user.id,
    )


def create_request(db_session, version, requester, key):
    return ApprovalService(db_session).create_skill_execution_request(
        version_id=version.id,
        requester=requester,
        input_payload={"value": 1},
        idempotency_key=key,
    ).request


def decision_count(db_session, request_id):
    return (
        db_session.query(ApprovalDecision)
        .filter(ApprovalDecision.approval_request_id == request_id)
        .count()
    )


def test_approval_requests_admit_only_per_step_skill_actions(db_session: Session):
    table = ApprovalRequest.__table__
    constraint = next(
        c
        for c in table.constraints
        if isinstance(c, CheckConstraint)
        and c.name == "ck_approval_requests_action_type_valid"
    )

    # the model declares exactly one action type: a skill step
    assert set(re.findall(r"'([^']+)'", str(constraint.sqltext))) == {
        "skill_execution"
    }
    # and the vocabulary has no plan-level object nor a human initiator
    columns = [c.name.lower() for c in table.columns]
    assert not [n for n in columns if "plan" in n]
    assert not [n for n in columns if re.search(r"initiator|on_behalf", n)]

    # the service offers one way to create an approval, with no plan input
    creators = sorted(
        name
        for name, _ in inspect.getmembers(ApprovalService, inspect.isfunction)
        if name.startswith("create")
    )
    assert creators == ["create_skill_execution_request"]
    parameters = inspect.signature(
        ApprovalService.create_skill_execution_request
    ).parameters
    assert not [n for n in parameters if "plan" in n]
    assert not [n for n in dir(ApprovalService) if "plan" in n.lower()]

    # the DATABASE enforces it too: a plan-level action cannot be stored
    version = approval_version(
        db_session,
        skill_key="plan.guard.table",
        execution_mode="mutating",
        capabilities=MUTATING,
    )
    request = create_request(
        db_session,
        version,
        ApprovalRequester(actor_type="agent", actor_reference="agent:planner"),
        "plan-guard-table-1",
    )

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "UPDATE approval_requests SET action_type = 'plan_execution' "
                "WHERE id = :id"
            ),
            {"id": request.id},
        )

    db_session.rollback()
    assert db_session.get(ApprovalRequest, request.id).action_type == (
        "skill_execution"
    )


def test_high_risk_user_request_cannot_be_decided_by_its_requester(
    db_session: Session,
):
    requester = approval_user(
        db_session, email="plan.guard.r1@example.com", role="manager"
    )
    other = approval_user(
        db_session, email="plan.guard.d1@example.com", role="manager"
    )
    version = approval_version(
        db_session,
        skill_key="plan.guard.high",
        execution_mode="mutating",
        capabilities=MUTATING,
    )
    request = create_request(
        db_session, version, user_requester(requester), "plan-guard-high-1"
    )
    service = ApprovalService(db_session)

    assert request.risk_level == "high"
    assert request.requester_user_id == requester.id

    with pytest.raises(ApprovalAuthorizationError, match=SEPARATION_MESSAGE):
        service.decide(request.id, decider_user_id=requester.id, decision="approved")

    db_session.expire_all()
    assert db_session.get(ApprovalRequest, request.id).status == "pending"
    assert decision_count(db_session, request.id) == 0

    decided = service.decide(request.id, decider_user_id=other.id, decision="approved")

    assert decided.request.status == "approved"
    assert decided.decision.decided_by_user_id == other.id
    assert other.id != requester.id


def test_the_requester_separation_rule_is_limited_to_high_and_critical_risk(
    db_session: Session,
):
    manager = approval_user(
        db_session, email="plan.guard.low@example.com", role="manager"
    )
    version = approval_version(
        db_session, skill_key="plan.guard.low", execution_mode="read_only"
    )
    request = create_request(
        db_session, version, user_requester(manager), "plan-guard-low-1"
    )

    assert request.risk_level == "low"

    decided = ApprovalService(db_session).decide(
        request.id, decider_user_id=manager.id, decision="approved"
    )

    assert decided.request.status == "approved"  # low risk: R may decide


def test_critical_user_request_needs_elevation_first_then_separation(
    db_session: Session,
):
    requester = approval_user(
        db_session, email="plan.guard.exec1@example.com", role="executive"
    )
    other = approval_user(
        db_session, email="plan.guard.exec2@example.com", role="executive"
    )
    version = approval_version(
        db_session,
        skill_key="plan.guard.critical",
        execution_mode="external",
        capabilities=EXTERNAL,
    )
    request = create_request(
        db_session, version, user_requester(requester), "plan-guard-crit-1"
    )
    service = ApprovalService(db_session)

    assert request.risk_level == "critical"

    with pytest.raises(ApprovalElevationRequiredError):
        service.decide(request.id, decider_user_id=requester.id, decision="approved")

    with pytest.raises(ApprovalAuthorizationError, match=SEPARATION_MESSAGE):
        service.decide(
            request.id,
            decider_user_id=requester.id,
            decision="approved",
            sensitive_elevation_verified=True,
        )

    decided = service.decide(
        request.id,
        decider_user_id=other.id,
        decision="approved",
        sensitive_elevation_verified=True,
    )
    assert decided.request.status == "approved"


def test_non_user_requesters_carry_no_human_identity_so_nothing_is_separated(
    db_session: Session,
):
    human = approval_user(
        db_session, email="plan.guard.human@example.com", role="manager"
    )
    version = approval_version(
        db_session,
        skill_key="plan.guard.nonuser",
        execution_mode="mutating",
        capabilities=MUTATING,
    )
    service = ApprovalService(db_session)

    for index, actor_type in enumerate(("agent", "system", "integration")):
        request = create_request(
            db_session,
            version,
            ApprovalRequester(
                actor_type=actor_type, actor_reference=f"{actor_type}:planner"
            ),
            f"plan-guard-nonuser-{index}",
        )

        assert request.risk_level == "high"
        assert request.requester_actor_type == actor_type
        assert request.requester_user_id is None  # no human is recorded

        decided = service.decide(
            request.id, decider_user_id=human.id, decision="approved"
        )
        assert decided.request.status == "approved"

    with pytest.raises(ApprovalValidationError):  # an agent cannot claim a human
        service.create_skill_execution_request(
            version_id=version.id,
            requester=ApprovalRequester(
                actor_type="agent",
                actor_reference="agent:planner",
                actor_user_id=human.id,
            ),
            input_payload={"value": 1},
            idempotency_key="plan-guard-nonuser-claim",
        )


def test_agent_originated_approval_does_not_separate_decider_from_executor_today(
    db_session: Session,
):
    """KNOWN GAP, pinned. A human who stands behind an agent plan (I) can
    approve it (D) and be the principal that executes it (X): the flow
    records no initiator, and its separation rules apply only to
    approvals requested by a USER. The planning layer therefore DECLARES
    initiator_ne_decider for the future execution gate to enforce."""
    human = governed_user(
        db_session, email="plan.guard.agent-human@example.com", role="manager"
    )
    version = governed_version(
        db_session,
        skill_key="plan.guard.agent",
        execution_mode="mutating",
        capabilities=MUTATING,
    )
    actor = agent_actor("agent:planner")
    payload = {"value": 1}
    approved = approve_as_agent(
        db_session,
        version=version,
        actor=actor,
        input_payload=payload,
        key="plan-guard-agent-1",
        decider=human,
    )

    validation = GovernedSkillExecutionService(
        db_session
    ).validate_approved_action_only(
        version.id,
        actor=actor,
        authority_user_id=human.id,
        input_payload=payload,
        approval_request_id=approved.request.id,
    )

    assert validation.request.risk_level == "high"
    assert validation.request.requester_user_id is None
    assert validation.decision.decided_by_user_id == human.id
    assert validation.authority.id == human.id  # D == X was accepted
    assert db_session.query(ApprovalConsumption).count() == 0  # nothing ran


def test_user_requested_approvals_never_reach_the_generic_governed_path(
    db_session: Session,
):
    requester = governed_user(
        db_session, email="plan.guard.gp-r@example.com", role="manager"
    )
    decider = governed_user(
        db_session, email="plan.guard.gp-d@example.com", role="manager"
    )
    version = governed_version(
        db_session,
        skill_key="plan.guard.generic",
        execution_mode="mutating",
        capabilities=MUTATING,
    )
    request = create_request(
        db_session, version, user_requester(requester), "plan-guard-generic-1"
    )
    ApprovalService(db_session).decide(
        request.id, decider_user_id=decider.id, decision="approved"
    )
    governed = GovernedSkillExecutionService(db_session)

    def attempt(actor):
        return governed.validate_approved_action_only(
            version.id,
            actor=actor,
            authority_user_id=decider.id,
            input_payload={"value": 1},
            approval_request_id=request.id,
        )

    with pytest.raises(ApprovalValidationError):  # human actors are refused
        attempt(
            SkillInvocationActor(
                actor_type="user", actor_reference=f"user:{requester.id}"
            )
        )

    with pytest.raises(ApprovalConsumptionConflictError):  # actor must match
        attempt(agent_actor("agent:planner"))


def test_human_corridor_enforces_executor_not_decider_with_a_positive_control(
    client: TestClient,
    db_session: Session,
):
    due_date = date.today() - timedelta(days=10)
    account, request_id = _setup_approved_episode(
        client,
        db_session,
        due_date=due_date,
        email="cliente.plan-guard.human-corridor@example.com",
    )
    requester = _current_user(db_session)
    approver = db_session.query(User).filter(User.email == APPROVER_EMAIL).one()
    request = db_session.get(ApprovalRequest, request_id)
    decision = (
        db_session.query(ApprovalDecision)
        .filter(ApprovalDecision.approval_request_id == request_id)
        .one()
    )

    # R and D are different people (the separation rule held when deciding)
    assert request.requester_actor_type == "user"
    assert request.requester_user_id == requester.id
    assert decision.decided_by_user_id == approver.id
    assert approver.id != requester.id

    approver_client = TestClient(app)
    approver_client.headers.update({API_KEY_HEADER_NAME: os.environ["API_KEY"]})
    login = approver_client.post(
        "/auth/login",
        json={"email": APPROVER_EMAIL, "password": APPROVER_PASSWORD},
    )
    assert login.status_code == 200
    before = _governed_object_counts(db_session)

    refused = approver_client.post(_execute_url(account.id, due_date))

    assert refused.status_code == 403
    assert refused.json()["detail"] == EXECUTOR_MESSAGE  # X != D
    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"
    assert _governed_object_counts(db_session) == before

    allowed = client.post(_execute_url(account.id, due_date))  # X == R is fine

    assert allowed.status_code == 200
    db_session.expire_all()
    consumption = (
        db_session.query(ApprovalConsumption)
        .filter(ApprovalConsumption.approval_request_id == request_id)
        .one()
    )
    assert consumption.authority_user_id == requester.id
    assert consumption.authority_user_id != decision.decided_by_user_id


def test_declared_separation_requirements_are_known_and_cover_the_gap():
    known = {"initiator_ne_decider", "executor_ne_decider"}

    for definition in REGISTRY_V1.definitions:
        assert set(definition.separation_requirements) <= known, definition.capability_id

    overdue = REGISTRY_V1.get("account.mark_overdue", 1)
    assert "initiator_ne_decider" in overdue.separation_requirements  # see the gap pin
    assert overdue.approval_policy.kind is ApprovalKind.PER_STEP_APPROVAL
