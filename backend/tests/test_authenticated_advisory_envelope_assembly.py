import ast
from dataclasses import FrozenInstanceError
from dataclasses import fields
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest

from app.core.authentication import AuthenticatedSession
from app.models.auth_session import AuthSession
from app.models.user import User
from app.orchestrator.advisory_envelope import (
    AuthenticatedAdvisoryEnvelope,
)
from app.orchestrator.decision import OrchestrationDecision
from app.orchestrator.orchestrator import AIOrchestrator
from app.services.authenticated_advisory_envelope_assembly import (
    ADVISORY_EVENT_NAME_MAX_LENGTH,
)
from app.services.authenticated_advisory_envelope_assembly import (
    AuthenticatedAdvisoryEnvelopeAssemblyService,
)
from app.services.orchestrator_skill_binding_projection import (
    AdvisoryAgentSkillSet,
)
from app.services.orchestrator_skill_binding_projection import (
    AdvisorySkillBindingPlan,
)
from app.services.orchestrator_skill_binding_projection import (
    OrchestratorSkillBindingProjectionService,
)


class EmptySkillRepository:
    def list_bindings_for_agent(
        self,
        agent_name: str,
        *,
        enabled_only: bool = True,
    ) -> list[object]:
        return []

    def get_version(
        self,
        version_id: int,
    ) -> None:
        return None

    def get_skill(
        self,
        skill_id: int,
    ) -> None:
        return None


def _authenticated_session() -> AuthenticatedSession:
    user = User(
        id=11,
        name="Assembly Test",
        email="assembly@example.com",
        password_hash="not-used",
        role="manager",
        active=True,
    )
    session = AuthSession(
        id=22,
        user_id=11,
        token_hash="b" * 64,
        expires_at=(
            datetime.now(timezone.utc)
            + timedelta(hours=1)
        ),
    )
    return AuthenticatedSession(
        user=user,
        session=session,
    )


def _decision(
    *agents: str,
) -> OrchestrationDecision:
    return OrchestrationDecision(
        decision_name="assembly_decision",
        selected_agents=tuple(agents),
        reason="test",
        confidence=1.0,
        signals=(),
    )


def _plan(
    decision: OrchestrationDecision,
) -> AdvisorySkillBindingPlan:
    return AdvisorySkillBindingPlan(
        decision_name=decision.decision_name,
        agents=tuple(
            AdvisoryAgentSkillSet(
                agent_name=agent_name,
                bindings=(),
            )
            for agent_name in decision.selected_agents
        ),
    )


def _service() -> AuthenticatedAdvisoryEnvelopeAssemblyService:
    projection = OrchestratorSkillBindingProjectionService(
        EmptySkillRepository()
    )
    return AuthenticatedAdvisoryEnvelopeAssemblyService(
        projection
    )


def test_assembly_service_requires_authenticated_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()

    monkeypatch.setattr(
        AIOrchestrator,
        "observe",
        lambda **_: pytest.fail(
            "observe must not run for invalid authentication"
        ),
    )

    with pytest.raises(
        TypeError,
        match="AuthenticatedSession",
    ):
        service.assemble(
            authenticated=object(),  # type: ignore[arg-type]
            event_name="work.completed",
            payload={},
        )


def test_event_name_must_be_non_blank_bounded_text() -> None:
    service = _service()
    authenticated = _authenticated_session()

    for invalid in (
        "",
        "   ",
        "x" * (ADVISORY_EVENT_NAME_MAX_LENGTH + 1),
    ):
        with pytest.raises(ValueError):
            service.assemble(
                authenticated=authenticated,
                event_name=invalid,
                payload={},
            )

    with pytest.raises(TypeError):
        service.assemble(
            authenticated=authenticated,
            event_name=123,  # type: ignore[arg-type]
            payload={},
        )


def test_payload_must_be_a_dict() -> None:
    service = _service()

    with pytest.raises(
        TypeError,
        match="payload must be a dict",
    ):
        service.assemble(
            authenticated=_authenticated_session(),
            event_name="work.completed",
            payload=[],  # type: ignore[arg-type]
        )


def test_authority_provenance_is_server_derived_from_authenticated_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    decision = _decision()

    monkeypatch.setattr(
        AIOrchestrator,
        "observe",
        lambda **_: decision,
    )

    envelope = service.assemble(
        authenticated=_authenticated_session(),
        event_name=" work.completed ",
        payload={"safe": "metadata"},
        request_id=" req-25i ",
    )

    assert envelope.authority.authority_user_id == 11
    assert envelope.authority.auth_session_id == 22
    assert envelope.authority.request_id == "req-25i"
    assert (
        envelope.authority.source
        == "authenticated_http_session"
    )


def test_assembly_invokes_observe_and_never_execute_or_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    calls: list[tuple[str, object]] = []
    payload = {"work_id": 7}
    decision = _decision()

    def observe(
        *,
        event_name: str,
        payload: dict[str, object],
    ) -> OrchestrationDecision:
        calls.append(
            (event_name, payload)
        )
        return decision

    monkeypatch.setattr(
        AIOrchestrator,
        "observe",
        observe,
    )
    monkeypatch.setattr(
        AIOrchestrator,
        "execute",
        lambda *_, **__: pytest.fail(
            "legacy execute must never run"
        ),
    )

    service.assemble(
        authenticated=_authenticated_session(),
        event_name=" work.completed ",
        payload=payload,
    )

    assert calls == [
        ("work.completed", payload),
    ]


def test_assembly_projects_the_exact_returned_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = OrchestratorSkillBindingProjectionService(
        EmptySkillRepository()
    )
    service = AuthenticatedAdvisoryEnvelopeAssemblyService(
        projection
    )
    decision = _decision(
        "RiskAgent",
        "FinanceAgent",
    )
    seen: list[OrchestrationDecision] = []

    monkeypatch.setattr(
        AIOrchestrator,
        "observe",
        lambda **_: decision,
    )

    def resolve(
        candidate: OrchestrationDecision,
    ) -> AdvisorySkillBindingPlan:
        seen.append(candidate)
        return _plan(candidate)

    monkeypatch.setattr(
        projection,
        "resolve",
        resolve,
    )

    envelope = service.assemble(
        authenticated=_authenticated_session(),
        event_name="work.completed",
        payload={},
    )

    assert seen == [decision]
    assert envelope.decision is decision
    assert envelope.plan == _plan(decision)


def test_assembly_returns_immutable_authenticated_advisory_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    decision = _decision()

    monkeypatch.setattr(
        AIOrchestrator,
        "observe",
        lambda **_: decision,
    )

    envelope = service.assemble(
        authenticated=_authenticated_session(),
        event_name="work.completed",
        payload={},
    )

    assert isinstance(
        envelope,
        AuthenticatedAdvisoryEnvelope,
    )

    with pytest.raises(FrozenInstanceError):
        envelope.plan = _plan(decision)


def test_payload_and_event_name_are_not_stored_in_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    decision = _decision()

    monkeypatch.setattr(
        AIOrchestrator,
        "observe",
        lambda **_: decision,
    )

    envelope = service.assemble(
        authenticated=_authenticated_session(),
        event_name="work.completed",
        payload={"secret_like": "must-not-be-stored"},
    )

    field_names = {
        field.name
        for field in fields(
            type(envelope)
        )
    }

    assert field_names == {
        "decision",
        "plan",
        "authority",
    }
    assert not hasattr(
        envelope,
        "payload",
    )
    assert not hasattr(
        envelope,
        "event_name",
    )


def test_module_has_no_mutating_runtime_database_or_eventbus_dependency() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "authenticated_advisory_envelope_assembly.py"
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
        "app.database.database",
        "sqlalchemy",
        "sqlalchemy.orm",
    }

    imported_modules: set[str] = set()

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
        "publish",
        "dispatch",
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


def test_documentation_preserves_non_routed_non_persistent_fail_closed_boundary() -> None:
    doc_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "orchestrator"
        / "AUTHENTICATED_ADVISORY_ENVELOPE_ASSEMBLY.md"
    )
    normalized = " ".join(
        doc_path.read_text(
            encoding="utf-8",
        )
        .lower()
        .split()
    )

    for required in (
        "authenticated session only",
        "no production route wiring",
        "no eventbus publish",
        "no persistence",
        "no work creation",
        "no skill execution",
        "no approval or memory mutation",
        "reload the current user",
        "reauthorize current scope",
        "reauthorize the exact skill",
        "fail closed",
    ):
        assert required in normalized
