"""
T-9 -- vetores-limite da replica das regras (Design Freeze v2, 2.1):
14/0/5/6 dias; 3 resolucoes; 0,50; 45 dias/R$ 500; 5 dias/R$ 15.000;
fronteira UTC 20:59/21:00 (G-SIM-9).
"""

from __future__ import annotations

import pytest

from sim.generator.timeline import to_minutes
from sim.oracle import rules


@pytest.mark.parametrize(
    "days_to_due,state",
    [(15, "open"), (14, "due_soon"), (1, "due_soon"), (0, "due_today"),
     (-1, "overdue"), (-5, "overdue"), (-6, "overdue_alert")],
)
def test_t9_lifecycle_thresholds(days_to_due, state):
    assert rules.lifecycle_state("aberto", 100 + days_to_due, 100) == state
    assert rules.lifecycle_state("atrasado", 100 + days_to_due, 100) == state
    assert rules.lifecycle_state("pago", 100 + days_to_due, 100) == "paid"


@pytest.mark.parametrize(
    "atrasos,label",
    [([], "INSUFFICIENT_DATA"), ([5, 5], "INSUFFICIENT_DATA"),
     ([1, 0, 0], "PAGAMENTO_REGULAR"), ([1, 1, 0], "ATRASO_RECORRENTE"),
     ([1, 1, 0, 0], "ATRASO_RECORRENTE"), ([1, 0, 0, 0], "PAGAMENTO_REGULAR"),
     ([0, 0, -3], "PAGAMENTO_REGULAR"), ([30, 1, 1], "ATRASO_RECORRENTE")],
)
def test_t9_classification_thresholds(atrasos, label):
    assert rules.classify(atrasos) == label


def test_t9_eligibility_precedence():
    assert rules.mark_overdue_reason("pago", 1, 5) == "already_paid"
    assert rules.mark_overdue_reason("atrasado", 1, 5) == "status_not_open"
    assert rules.mark_overdue_reason("aberto", 5, 5) == "not_overdue"
    assert rules.mark_overdue_reason("aberto", 4, 5) is None
    assert rules.escalation_reason("pago", 1, 5, True) == "account_paid"
    assert rules.escalation_reason("aberto", 5, 5, False) == "lifecycle_not_overdue"
    assert rules.escalation_reason("atrasado", 1, 5, True) == "active_escalation_exists"
    assert rules.escalation_reason("atrasado", 1, 5, False) is None


@pytest.mark.parametrize(
    "days,amount,expected",
    [(45, 50_001, ()), (46, 50_001, ("prolonged_overdue_escalation",)),
     (46, 50_000, ()), (4, 1_500_000, ()),
     (5, 1_500_000, ("high_exposure_early_escalation",)), (5, 1_499_999, ()),
     (46, 1_500_000, ("prolonged_overdue_escalation", "high_exposure_early_escalation"))],
)
def test_t9_nba_triggers(days, amount, expected):
    decision = rules.nba_decision(True, True, days, amount)
    assert decision.trigger_rules == expected
    if expected:
        assert decision.decision_type == "action_bundle"
    else:
        assert decision.selected_actions == (rules.MARK_OVERDUE,)


def test_t9_nba_r0_r1_r2():
    assert rules.nba_decision(False, False, 10, 1).decision_type == "no_action"
    assert rules.nba_decision(True, False, 10, 1).selected_actions == (rules.MARK_OVERDUE,)
    assert rules.nba_decision(False, True, 99, 9_999_999).selected_actions == (rules.ESCALATE,)


def test_t9_utc_date_boundary(inputs):
    cal = inputs.calendar
    assert cal.utc_day((10, to_minutes("20:59"))) == 10
    assert cal.utc_day((10, to_minutes("21:00"))) == 11
    assert cal.utc_day((10, to_minutes("23:59"))) == 11


def test_t9_tz_cases_produce_the_g_sim_9_finding(generated):
    oracle = generated["nh-standard"].oracle
    findings = [e for e in oracle["expectations"] if "G-SIM-9" in e.get("finding", [])]
    assert findings
    assert all(e["kind"] == "classification" for e in findings)


def test_t9_rule_versions_are_declared(generated):
    versions = generated["nh-standard"].oracle["rule_versions"]
    assert versions["classification"] == "recurrence_v1"
    assert versions["nba"] == "nba_policy_v1"
    assert versions["human_escalation_eligibility"] == "human_escalation_eligibility_v1"
