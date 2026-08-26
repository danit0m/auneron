import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.orchestrator.decision import OrchestrationDecision
from app.services.orchestrator_skill_binding_projection import (
    AdvisorySkillBinding,
)
from app.services.orchestrator_skill_binding_projection import (
    OrchestratorSkillBindingProjectionService,
)


class FakeSkillRepository:
    def __init__(self) -> None:
        self.bindings_by_agent: dict[str, list[SimpleNamespace]] = {}
        self.versions: dict[int, SimpleNamespace] = {}
        self.skills: dict[int, SimpleNamespace] = {}
        self.binding_calls: list[tuple[str, bool]] = []

    def list_bindings_for_agent(
        self,
        agent_name: str,
        *,
        enabled_only: bool = True,
    ) -> list[SimpleNamespace]:
        self.binding_calls.append(
            (agent_name, enabled_only)
        )
        return list(
            self.bindings_by_agent.get(
                agent_name,
                [],
            )
        )

    def get_version(
        self,
        version_id: int,
    ) -> SimpleNamespace | None:
        return self.versions.get(version_id)

    def get_skill(
        self,
        skill_id: int,
    ) -> SimpleNamespace | None:
        return self.skills.get(skill_id)


def make_decision(
    *agents: str,
) -> OrchestrationDecision:
    return OrchestrationDecision(
        decision_name="test_decision",
        selected_agents=tuple(agents),
        reason="test",
        confidence=1.0,
        signals=(),
    )


def add_binding(
    repository: FakeSkillRepository,
    *,
    agent_name: str,
    binding_id: int,
    version_id: int,
    skill_id: int,
    priority: int,
    version_status: str = "published",
    skill_status: str = "active",
    execution_mode: str = "read_only",
    runtime_kind: str = "internal_python",
) -> None:
    repository.bindings_by_agent.setdefault(
        agent_name,
        [],
    ).append(
        SimpleNamespace(
            id=binding_id,
            agent_name=agent_name,
            skill_version_id=version_id,
            priority=priority,
            enabled=True,
            configuration={
                "must_not": "be_projected",
            },
        )
    )
    repository.versions[version_id] = SimpleNamespace(
        id=version_id,
        skill_id=skill_id,
        status=version_status,
        execution_mode=execution_mode,
        runtime_kind=runtime_kind,
        handler_reference="app.skills.fake:run",
        manifest={
            "must_not": "be_projected",
        },
    )
    repository.skills[skill_id] = SimpleNamespace(
        id=skill_id,
        status=skill_status,
    )


def test_rejects_non_orchestration_decision() -> None:
    repository = FakeSkillRepository()
    service = OrchestratorSkillBindingProjectionService(
        repository
    )

    with pytest.raises(
        TypeError,
        match="OrchestrationDecision",
    ):
        service.resolve(object())  # type: ignore[arg-type]


def test_empty_selected_agents_returns_empty_plan() -> None:
    repository = FakeSkillRepository()
    service = OrchestratorSkillBindingProjectionService(
        repository
    )

    plan = service.resolve(make_decision())

    assert plan.decision_name == "test_decision"
    assert plan.agents == ()
    assert repository.binding_calls == []


def test_preserves_selected_agent_order() -> None:
    repository = FakeSkillRepository()
    service = OrchestratorSkillBindingProjectionService(
        repository
    )

    plan = service.resolve(
        make_decision(
            "RiskAgent",
            "FinanceAgent",
            "AnalyticsAgent",
        )
    )

    assert tuple(
        item.agent_name
        for item in plan.agents
    ) == (
        "RiskAgent",
        "FinanceAgent",
        "AnalyticsAgent",
    )


def test_rejects_duplicate_selected_agent_names() -> None:
    repository = FakeSkillRepository()
    service = OrchestratorSkillBindingProjectionService(
        repository
    )

    with pytest.raises(
        ValueError,
        match="duplicate",
    ):
        service.resolve(
            make_decision(
                "RiskAgent",
                "RiskAgent",
            )
        )


def test_reads_enabled_bindings_only() -> None:
    repository = FakeSkillRepository()
    service = OrchestratorSkillBindingProjectionService(
        repository
    )

    service.resolve(
        make_decision("RiskAgent")
    )

    assert repository.binding_calls == [
        ("RiskAgent", True),
    ]


def test_preserves_repository_binding_order() -> None:
    repository = FakeSkillRepository()
    add_binding(
        repository,
        agent_name="RiskAgent",
        binding_id=11,
        version_id=101,
        skill_id=1001,
        priority=10,
    )
    add_binding(
        repository,
        agent_name="RiskAgent",
        binding_id=12,
        version_id=102,
        skill_id=1002,
        priority=20,
    )
    service = OrchestratorSkillBindingProjectionService(
        repository
    )

    plan = service.resolve(
        make_decision("RiskAgent")
    )

    assert tuple(
        binding.binding_id
        for binding in plan.agents[0].bindings
    ) == (
        11,
        12,
    )
    assert tuple(
        binding.binding_priority
        for binding in plan.agents[0].bindings
    ) == (
        10,
        20,
    )


def test_omits_unpublished_skill_version() -> None:
    repository = FakeSkillRepository()
    add_binding(
        repository,
        agent_name="FinanceAgent",
        binding_id=21,
        version_id=201,
        skill_id=2001,
        priority=10,
        version_status="draft",
    )
    service = OrchestratorSkillBindingProjectionService(
        repository
    )

    plan = service.resolve(
        make_decision("FinanceAgent")
    )

    assert plan.agents[0].bindings == ()


def test_omits_inactive_skill() -> None:
    repository = FakeSkillRepository()
    add_binding(
        repository,
        agent_name="AnalyticsAgent",
        binding_id=31,
        version_id=301,
        skill_id=3001,
        priority=10,
        skill_status="disabled",
    )
    service = OrchestratorSkillBindingProjectionService(
        repository
    )

    plan = service.resolve(
        make_decision("AnalyticsAgent")
    )

    assert plan.agents[0].bindings == ()


def test_projection_exposes_safe_metadata_only() -> None:
    repository = FakeSkillRepository()
    add_binding(
        repository,
        agent_name="FinanceAgent",
        binding_id=41,
        version_id=401,
        skill_id=4001,
        priority=25,
        execution_mode="read_only",
        runtime_kind="internal_python",
    )
    service = OrchestratorSkillBindingProjectionService(
        repository
    )

    plan = service.resolve(
        make_decision("FinanceAgent")
    )

    projected = plan.agents[0].bindings[0]

    assert isinstance(
        projected,
        AdvisorySkillBinding,
    )
    assert projected == AdvisorySkillBinding(
        agent_name="FinanceAgent",
        binding_id=41,
        skill_version_id=401,
        skill_id=4001,
        binding_priority=25,
        execution_mode="read_only",
        runtime_kind="internal_python",
    )

    for forbidden in (
        "configuration",
        "handler_reference",
        "manifest",
        "capabilities",
        "input_payload",
        "authority_user_id",
        "approval_request_id",
        "runtime_context",
        "memory",
    ):
        assert not hasattr(
            projected,
            forbidden,
        )


def test_projection_source_has_no_execution_or_authority_wiring() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "orchestrator_skill_binding_projection.py"
    )
    source = source_path.read_text(
        encoding="utf-8",
    )
    tree = ast.parse(source)

    forbidden_names = {
        "WorkManagerService",
        "WorkSkillExecutionService",
        "GovernedSkillExecutionService",
        "ApprovalService",
        "SkillRuntime",
        "SkillRuntimeService",
        "EventBus",
        "ExecutionPipeline",
        "MemoryService",
        "authority_user_id",
        "input_payload",
    }

    referenced_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }

    imported_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(
                    alias.asname
                    or alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(
                    alias.asname
                    or alias.name
                )

    assert not (
        (referenced_names | imported_names)
        & forbidden_names
    )

    forbidden_attributes = {
        "handler",
        "add",
        "delete",
        "flush",
        "commit",
        "dispatch",
        "execute",
        "configure",
        "publish",
    }

    assert not {
        node.attr
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Attribute)
            and node.attr in forbidden_attributes
        )
    }
