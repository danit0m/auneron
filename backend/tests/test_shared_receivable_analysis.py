from datetime import date
from datetime import timedelta
from decimal import Decimal
import importlib
from unittest.mock import Mock

import pytest

from app.core import receivable_analysis
from app.core.receivable_analysis import evaluate_receivable_analysis
from app.core.receivable_lifecycle import evaluate_receivable_lifecycle
from app.orchestrator.rules import DEFAULT_RULES
from app.orchestrator.rules import build_context


TODAY = date(2026, 11, 10)


@pytest.fixture(autouse=True)
def business_clock(monkeypatch):
    monkeypatch.setattr(receivable_analysis, "business_today", lambda: TODAY)


@pytest.mark.parametrize("status", ["aberto", "atrasado", "pago", " PAGO "])
@pytest.mark.parametrize("offset", [-31, -30, -7, -6, -5, -1, 0, 1, 14, 15])
def test_shared_observation_matches_canonical_lifecycle(status, offset):
    due = TODAY + timedelta(days=offset)
    expected = evaluate_receivable_lifecycle(
        financial_status=status, vencimento=due, today=TODAY,
    )
    result = evaluate_receivable_analysis(
        financial_status=status, vencimento=due.isoformat(),
    )
    assert result is not None
    assert result.financial_status == status.strip().lower()
    if expected.state == "paid":
        assert result.operational_status == "pago"
        assert result.days_overdue == 0
    elif expected.days_overdue:
        assert result.operational_status == "atrasado"
        assert result.days_overdue == expected.days_overdue
    else:
        assert result.operational_status == "aberto"
        assert result.days_overdue == 0


@pytest.mark.parametrize("status,due", [
    ("", "2026-11-01"), ("unknown", "2026-11-01"),
    ("aberto", ""), ("aberto", "invalid"), ("aberto", None),
    ("atrasado", "2026-02-30"),
])
def test_invalid_facts_produce_no_observation(status, due):
    assert evaluate_receivable_analysis(financial_status=status, vencimento=due) is None


@pytest.mark.parametrize("status,due,expected_status,expected_days", [
    ("aberto", "2026-11-09", "atrasado", 1),
    ("atrasado", "2026-11-11", "aberto", 0),
    ("pago", "2026-10-01", "pago", 0),
])
def test_decision_engine_uses_operational_facts_without_mutating_payload(
    status, due, expected_status, expected_days,
):
    payload = {"cliente": "Synthetic", "valor": "100.00", "status": status, "vencimento": due}
    original = payload.copy()
    context = build_context(event_name="cliente_criado", payload=payload)
    assert context.status == expected_status
    assert context.dias_atraso == expected_days
    assert payload == original
    decision = next(rule for rule in DEFAULT_RULES if rule.matches(context)).build_decision(context)
    if expected_status == "atrasado":
        assert "NotificationAgent" in decision.selected_agents


def test_invalid_context_stops_before_selecting_agents():
    context = build_context(event_name="cliente_criado", payload={"status": "aberto"})
    decision = next(rule for rule in DEFAULT_RULES if rule.matches(context)).build_decision(context)
    assert decision.decision_name == "DADOS_FINANCEIROS_INSUFICIENTES"
    assert decision.selected_agents == ()
    assert decision.confidence == 0.0


AGENTS = [
    ("finance_agent", "FinanceAgent"),
    ("risk_agent", "RiskAgent"),
    ("analytics_agent", "AnalyticsAgent"),
    ("notification_agent", "NotificationAgent"),
]


@pytest.mark.parametrize("module_name,class_name", AGENTS)
@pytest.mark.parametrize("status,overdue", [("aberto", True), ("pago", False)])
def test_real_agent_entry_points_share_due_date_observation(
    monkeypatch, module_name, class_name, status, overdue,
):
    module = importlib.import_module("app.agents." + module_name)
    db = Mock()
    create = Mock()
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module.KnowledgeService, "create", create)
    payload = {"id": 123, "cliente": "Synthetic", "valor": "100.00",
               "status": status, "vencimento": "2026-11-09"}
    original = payload.copy()
    getattr(module, class_name).on_cliente_criado(payload)
    assert payload == original
    calls = [call.kwargs for call in create.call_args_list]
    if overdue:
        assert calls
        if class_name == "RiskAgent":
            assert "Risk Score 55" in calls[0]["title"]
        else:
            assert any(call["severity"] in {"critical", "high"} for call in calls)
    elif class_name != "RiskAgent":
        assert calls == []
    db.commit.assert_not_called()


@pytest.mark.parametrize("module_name,class_name", AGENTS)
def test_agents_do_not_analyze_invalid_dates(monkeypatch, module_name, class_name):
    module = importlib.import_module("app.agents." + module_name)
    session = Mock()
    monkeypatch.setattr(module, "SessionLocal", session)
    getattr(module, class_name).on_cliente_criado(
        {"valor": Decimal("100.00"), "status": "atrasado", "vencimento": "invalid"}
    )
    session.assert_not_called()


def test_risk_agent_public_method_uses_same_business_clock():
    from app.agents.risk_agent import RiskAgent
    assert RiskAgent.calcular_dias_atraso(status="aberto", vencimento="2026-11-09") == 1
    assert RiskAgent.calcular_dias_atraso(status="pago", vencimento="2026-11-01") == 0
