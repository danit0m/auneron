from typing import Any

import pytest

from app.agents.event_bus import EventBus
from app.orchestrator import orchestrator as orchestrator_module
from app.orchestrator.decision import OrchestrationDecision
from app.orchestrator.decision_store import decision_store
from app.orchestrator.metrics import metrics_collector
from app.orchestrator.orchestrator import AIOrchestrator
from app.orchestrator.pipeline import ExecutionPipeline
from app.orchestrator.registry import RegisteredAgent
from app.orchestrator.safety import LegacyAutonomyExecutionBlockedError
from app.orchestrator.telemetry import telemetry_service


def _decision(*, selected_agents: tuple[str, ...] = ()) -> OrchestrationDecision:
    return OrchestrationDecision(
        decision_name="TEST_OBSERVE_ONLY",
        selected_agents=selected_agents,
        reason="quarantine contract test",
        confidence=1.0,
        signals=(),
    )


def _agent(handler) -> RegisteredAgent:
    return RegisteredAgent(
        name="SpyAgent",
        handler=handler,
        priority=10,
    )


def test_event_bus_publish_calls_observe_not_execute(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def observe(event_name: str, payload: dict[str, Any]):
        calls.append((event_name, payload))
        return _decision()

    def execute(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("legacy execute must not be called")

    monkeypatch.setattr(AIOrchestrator, "observe", observe)
    monkeypatch.setattr(AIOrchestrator, "execute", execute)

    bus = EventBus()
    payload = {"id": 42, "cliente": "Observe Only"}

    assert bus.publish("cliente_criado", payload) is None
    assert calls == [("cliente_criado", payload)]


def test_observe_preserves_candidate_selection_without_handler_execution(
    monkeypatch,
) -> None:
    handler_calls = 0
    registry_calls: list[tuple[str, object]] = []
    decision = _decision(selected_agents=("SpyAgent",))

    def handler(payload: dict[str, Any]) -> None:
        nonlocal handler_calls
        handler_calls += 1

    agent = _agent(handler)

    def decide(*, event_name: str, payload: dict[str, Any]):
        assert event_name == "cliente_criado"
        assert payload == {"id": 7}
        return decision

    def get_agents(event_name: str):
        registry_calls.append(("get_agents", event_name))
        return [agent]

    def get_selected_agents(event_name: str, selected_names: tuple[str, ...]):
        registry_calls.append(("get_selected_agents", selected_names))
        assert event_name == "cliente_criado"
        return [agent]

    monkeypatch.setattr(orchestrator_module.decision_engine, "decide", decide)
    monkeypatch.setattr(orchestrator_module.registry, "get_agents", get_agents)
    monkeypatch.setattr(
        orchestrator_module.registry,
        "get_selected_agents",
        get_selected_agents,
    )
    monkeypatch.setattr(
        AIOrchestrator,
        "_print_header",
        staticmethod(lambda **kwargs: None),
    )

    result = AIOrchestrator.observe(
        event_name="cliente_criado",
        payload={"id": 7},
    )

    assert result is decision
    assert handler_calls == 0
    assert registry_calls == [
        ("get_agents", "cliente_criado"),
        ("get_selected_agents", ("SpyAgent",)),
    ]


def test_observe_preserves_real_decision_store_observation() -> None:
    decision_store.clear()

    try:
        result = AIOrchestrator.observe(
            event_name="evento_sem_execucao",
            payload={"cliente": "Observation"},
        )

        assert result.decision_name == "EVENTO_NAO_SUPORTADO"
        assert decision_store.count() == 1
        latest = decision_store.get_latest()
        assert latest is not None
        assert latest.event_name == "evento_sem_execucao"
        assert latest.decision_name == "EVENTO_NAO_SUPORTADO"
    finally:
        decision_store.clear()


def test_ai_orchestrator_execute_fails_before_decision(monkeypatch) -> None:
    decision_called = False

    def decide(*args, **kwargs):  # pragma: no cover - must never run
        nonlocal decision_called
        decision_called = True
        raise AssertionError("decision must not run on blocked execute")

    monkeypatch.setattr(orchestrator_module.decision_engine, "decide", decide)

    with pytest.raises(LegacyAutonomyExecutionBlockedError):
        AIOrchestrator.execute(
            event_name="cliente_criado",
            payload={"id": 1},
        )

    assert decision_called is False


def test_pipeline_execute_fails_before_handler_metrics_or_telemetry() -> None:
    handler_calls = 0

    def handler(payload: dict[str, Any]) -> None:
        nonlocal handler_calls
        handler_calls += 1

    agent = _agent(handler)
    metrics_collector.reset()
    telemetry_service.clear()

    with pytest.raises(LegacyAutonomyExecutionBlockedError):
        ExecutionPipeline.execute(
            event_name="cliente_criado",
            strategy_name="TEST",
            agents=[agent],
            payload={"id": 1},
        )

    assert handler_calls == 0
    assert metrics_collector.get_summary()["executions"] == 0
    assert telemetry_service.list_records() == []


def test_pipeline_execute_agent_fails_before_handler_metrics_or_telemetry() -> None:
    handler_calls = 0

    def handler(payload: dict[str, Any]) -> None:
        nonlocal handler_calls
        handler_calls += 1

    agent = _agent(handler)
    metrics_collector.reset()
    telemetry_service.clear()

    with pytest.raises(LegacyAutonomyExecutionBlockedError):
        ExecutionPipeline._execute_agent(
            event_name="cliente_criado",
            strategy_name="TEST",
            agent=agent,
            payload={"id": 2},
        )

    assert handler_calls == 0
    assert metrics_collector.get_summary()["executions"] == 0
    assert telemetry_service.list_records() == []


def test_quarantine_error_is_runtime_error() -> None:
    error = LegacyAutonomyExecutionBlockedError("blocked")
    assert isinstance(error, RuntimeError)


def test_observe_only_runtime_has_no_execution_bypass() -> None:
    import inspect

    observe_source = inspect.getsource(AIOrchestrator.observe)
    execute_source = inspect.getsource(AIOrchestrator.execute)
    pipeline_source = inspect.getsource(ExecutionPipeline)

    assert "ExecutionPipeline" not in observe_source
    assert "agent.handler" not in observe_source
    assert "decision_engine.decide" not in execute_source
    assert "agent.handler" not in pipeline_source
    assert "metrics_collector" not in pipeline_source
    assert "telemetry_service" not in pipeline_source
    assert "os.environ" not in pipeline_source
    assert "settings" not in pipeline_source
