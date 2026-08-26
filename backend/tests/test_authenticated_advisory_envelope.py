import ast
from dataclasses import FrozenInstanceError
from dataclasses import fields
from pathlib import Path

import pytest

from app.core.authority_provenance import AuthorityProvenance
from app.orchestrator.advisory_envelope import (
    AuthenticatedAdvisoryEnvelope,
)
from app.orchestrator.decision import OrchestrationDecision
from app.services.orchestrator_skill_binding_projection import (
    AdvisoryAgentSkillSet,
)
from app.services.orchestrator_skill_binding_projection import (
    AdvisorySkillBindingPlan,
)


def _decision(
    *agents: str,
    decision_name: str = "test_decision",
) -> OrchestrationDecision:
    return OrchestrationDecision(
        decision_name=decision_name,
        selected_agents=tuple(agents),
        reason="test",
        confidence=1.0,
        signals=(),
    )


def _plan(
    *agents: str,
    decision_name: str = "test_decision",
) -> AdvisorySkillBindingPlan:
    return AdvisorySkillBindingPlan(
        decision_name=decision_name,
        agents=tuple(
            AdvisoryAgentSkillSet(
                agent_name=agent_name,
                bindings=(),
            )
            for agent_name in agents
        ),
    )


def _authority() -> AuthorityProvenance:
    return AuthorityProvenance(
        authority_user_id=11,
        auth_session_id=22,
        request_id="req-25h",
    )


def test_authenticated_advisory_envelope_is_immutable() -> None:
    envelope = AuthenticatedAdvisoryEnvelope(
        decision=_decision("RiskAgent"),
        plan=_plan("RiskAgent"),
        authority=_authority(),
    )

    assert envelope.decision.decision_name == "test_decision"
    assert envelope.plan.decision_name == "test_decision"
    assert envelope.authority.authority_user_id == 11

    with pytest.raises(FrozenInstanceError):
        envelope.plan = _plan("FinanceAgent")


def test_decision_type_is_required() -> None:
    with pytest.raises(
        TypeError,
        match="OrchestrationDecision",
    ):
        AuthenticatedAdvisoryEnvelope(
            decision=object(),  # type: ignore[arg-type]
            plan=_plan(),
            authority=_authority(),
        )


def test_plan_type_is_required() -> None:
    with pytest.raises(
        TypeError,
        match="AdvisorySkillBindingPlan",
    ):
        AuthenticatedAdvisoryEnvelope(
            decision=_decision(),
            plan=object(),  # type: ignore[arg-type]
            authority=_authority(),
        )


def test_authority_type_is_required() -> None:
    with pytest.raises(
        TypeError,
        match="AuthorityProvenance",
    ):
        AuthenticatedAdvisoryEnvelope(
            decision=_decision(),
            plan=_plan(),
            authority=object(),  # type: ignore[arg-type]
        )


def test_decision_name_must_match_plan_decision_name() -> None:
    with pytest.raises(
        ValueError,
        match="decision_name",
    ):
        AuthenticatedAdvisoryEnvelope(
            decision=_decision(
                "RiskAgent",
                decision_name="decision_a",
            ),
            plan=_plan(
                "RiskAgent",
                decision_name="decision_b",
            ),
            authority=_authority(),
        )


def test_selected_agent_order_and_membership_must_match_exactly() -> None:
    with pytest.raises(
        ValueError,
        match="order and membership",
    ):
        AuthenticatedAdvisoryEnvelope(
            decision=_decision(
                "RiskAgent",
                "FinanceAgent",
            ),
            plan=_plan(
                "FinanceAgent",
                "RiskAgent",
            ),
            authority=_authority(),
        )

    with pytest.raises(
        ValueError,
        match="order and membership",
    ):
        AuthenticatedAdvisoryEnvelope(
            decision=_decision(
                "RiskAgent",
                "FinanceAgent",
            ),
            plan=_plan("RiskAgent"),
            authority=_authority(),
        )


def test_empty_selected_agent_set_is_preserved() -> None:
    envelope = AuthenticatedAdvisoryEnvelope(
        decision=_decision(),
        plan=_plan(),
        authority=_authority(),
    )

    assert envelope.decision.selected_agents == ()
    assert envelope.plan.agents == ()


def test_envelope_has_only_decision_plan_and_authority_fields() -> None:
    field_names = {
        field.name
        for field in fields(
            AuthenticatedAdvisoryEnvelope
        )
    }

    assert field_names == {
        "decision",
        "plan",
        "authority",
    }

    for forbidden in (
        "input_payload",
        "runtime_context",
        "work_id",
        "work_item",
        "approval_request_id",
        "authorization_decision",
        "role",
        "permissions",
        "scope_type",
        "session_elevated",
        "credentials",
        "tokens",
        "memory",
    ):
        assert forbidden not in field_names


def test_module_imports_no_execution_mutation_or_database_dependencies() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "orchestrator"
        / "advisory_envelope.py"
    )
    tree = ast.parse(
        source_path.read_text(
            encoding="utf-8",
        )
    )

    forbidden_modules = {
        "app.agents.event_bus",
        "app.services.work_service",
        "app.services.work_skill_execution",
        "app.services.governed_skill_execution",
        "app.services.approval_service",
        "app.services.skill_runtime",
        "app.services.memory_service",
        "sqlalchemy",
        "sqlalchemy.orm",
    }

    imported_modules = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(
                alias.name
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported_modules.add(
                    node.module
                )

    assert not (
        imported_modules
        & forbidden_modules
    )

    forbidden_calls = {
        "execute",
        "dispatch",
        "publish",
        "commit",
        "flush",
        "add",
        "delete",
        "create_work",
        "authorize_skill_execution",
    }

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        )
    } | {
        node.func.id
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        )
    }

    assert not (
        called
        & forbidden_calls
    )


def test_documentation_preserves_future_reauthorization_fail_closed_rule() -> None:
    doc_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "orchestrator"
        / "AUTHENTICATED_ADVISORY_ENVELOPE.md"
    )
    normalized = " ".join(
        doc_path.read_text(
            encoding="utf-8",
        )
        .lower()
        .split()
    )

    for required in (
        "grants no authority",
        "not an authorization decision",
        "not executable intent",
        "reload the current user",
        "reload the current auth session",
        "reauthorize current scope",
        "reauthorize the exact skill",
        "fail closed",
        "no production eventbus wiring",
        "no database access",
    ):
        assert required in normalized
