"""A falha do orquestrador observe-only nao pode afetar quem publica o evento."""

import logging

import pytest

from app.agents.event_bus import EventBus
from app.orchestrator import decision_engine as decision_engine_module
from app.orchestrator import orchestrator as orchestrator_module
from app.orchestrator.orchestrator import AIOrchestrator

ARROW = "→"


@pytest.mark.parametrize(
    "error",
    [
        UnicodeEncodeError("charmap", ARROW, 0, 1, "character maps to <undefined>"),
        RuntimeError("falha generica"),
    ],
)
def test_publish_swallows_and_logs_observe_failure(monkeypatch, caplog, error) -> None:
    def observe(event_name, payload):
        raise error

    monkeypatch.setattr(AIOrchestrator, "observe", observe)

    with caplog.at_level(logging.ERROR, logger="app.agents.event_bus"):
        assert EventBus().publish("cliente_criado", {"id": 1}) is None

    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(records) == 1
    assert "cliente_criado" in records[0].getMessage()
    assert records[0].exc_info is not None


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_publish_does_not_swallow_base_exceptions(monkeypatch, error) -> None:
    def observe(event_name, payload):
        raise error()

    monkeypatch.setattr(AIOrchestrator, "observe", observe)

    with pytest.raises(error):
        EventBus().publish("cliente_criado", {"id": 1})


def test_orchestrator_modules_use_logging_and_no_arrow() -> None:
    for module in (orchestrator_module, decision_engine_module):
        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        assert "print(" not in source
        assert ARROW not in source
