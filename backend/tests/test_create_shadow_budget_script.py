"""
Testes do script do orcamento do shadow S5 (scripts/create_shadow_budget.py):
recusa banco diferente do esperado, e idempotente, nao altera registro
existente, e o limite de tokens admite UMA chamada e recusa a segunda.
"""

import json
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy import text

from app.intelligence.gateway.budget import BudgetLedger
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.pricing import PRICE_TABLE
from app.intelligence.gateway.pricing import Reservation
from app.intelligence.gateway.pricing import input_token_upper_bound
from app.intelligence.gateway.pricing import worst_case_reservation
from app.database.database import SessionLocal
from app.models.intelligence_budget import IntelligenceBudget
from app.models.intelligence_budget import IntelligenceModelCall
from app.services import overdue_detection_shadow as shadow
from scripts import create_shadow_budget as script

MARGIN_BPS = 2000


def _database_name(db_session) -> str:
    return db_session.execute(text("SELECT current_database()")).scalar()


def _budgets(db_session) -> list[IntelligenceBudget]:
    db_session.expire_all()
    return (
        db_session.query(IntelligenceBudget)
        .order_by(IntelligenceBudget.id)
        .all()
    )


def test_default_expected_database_is_synthetic_and_not_test_db(db_session):
    assert script.EXPECTED_DATABASE == "auneron_neon_synthetic"
    assert _database_name(db_session) != script.EXPECTED_DATABASE
    assert script.run(db_session) == script.EXIT_WRONG_DATABASE


def test_wrong_database_exits_4_without_writing(db_session, capsys):
    code = script.run(db_session, "outro_banco_qualquer", MARGIN_BPS)
    out = capsys.readouterr().out

    assert code == script.EXIT_WRONG_DATABASE == 4
    assert "RECUSADO" in out
    assert _budgets(db_session) == []


def test_creates_deployment_budget_for_exactly_one_call(db_session, capsys):
    code = script.run(db_session, _database_name(db_session), MARGIN_BPS)
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    budgets = _budgets(db_session)
    assert len(budgets) == 1
    budget = budgets[0]
    reservation = script.one_call_reservation(MARGIN_BPS)
    assert budget.scope_kind == "deployment_period"
    assert budget.scope_ref == script.SCOPE_REF
    assert budget.limit_micro == 1
    assert budget.limit_tokens == reservation.tokens
    assert budget.reserved_tokens == budget.settled_tokens == 0
    assert budget.period_end > budget.period_start
    assert "provisionado agora" in out
    assert f"ID_ORCAMENTO={budget.id}" in out


def test_is_idempotent_and_does_not_change_existing(db_session, capsys):
    database = _database_name(db_session)
    assert script.run(db_session, database, MARGIN_BPS) == 0
    first = _budgets(db_session)[0]
    version = first.version
    capsys.readouterr()

    code = script.run(db_session, database, MARGIN_BPS)
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    assert "ja provisionado" in out
    assert f"budget_id    : {first.id}" in out
    budgets = _budgets(db_session)
    assert [b.id for b in budgets] == [first.id]
    assert budgets[0].version == version


def test_divergent_limits_exit_3_unchanged(db_session, capsys):
    database = _database_name(db_session)
    assert script.run(db_session, database, MARGIN_BPS) == 0
    budget = _budgets(db_session)[0]
    original = budget.limit_tokens
    capsys.readouterr()

    code = script.run(db_session, database, 5000)
    out = capsys.readouterr().out

    assert code == script.EXIT_DIVERGENT == 3
    assert "DIVERGENCIA" in out
    assert _budgets(db_session)[0].limit_tokens == original


def test_expired_period_exits_3(db_session):
    database = _database_name(db_session)
    assert script.run(db_session, database, MARGIN_BPS) == 0
    budget = _budgets(db_session)[0]
    budget.period_start = datetime.now(timezone.utc) - timedelta(days=9)
    budget.period_end = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.commit()

    assert script.run(db_session, database, MARGIN_BPS) == 3


def test_one_call_reservation_matches_gateway_formula():
    reservation = script.one_call_reservation(MARGIN_BPS)
    due = date(2000, 1, 1)
    input_text = json.dumps(
        {
            "due_date": due.isoformat(),
            "status": "aberto",
            "business_date": (due + timedelta(days=999)).isoformat(),
            "days_after_due": 999,
        },
        sort_keys=True,
    )
    input_bytes = input_token_upper_bound(
        shadow.INSTRUCTIONS,
        input_text,
        json.dumps(dict(shadow.OUTPUT_SCHEMA), sort_keys=True),
    )
    expected = worst_case_reservation(
        input_upper_bound_tokens=input_bytes,
        max_output_tokens=shadow.MAX_OUTPUT_TOKENS,
        price=PRICE_TABLE["local-zero"],
        margin_bps=MARGIN_BPS,
    )

    assert reservation == expected
    assert reservation.micro == 1


def _reserve(ledger, budget_id, call_key, tokens, micro=1):
    return ledger.reserve(
        call_key=call_key,
        purpose=shadow.SHADOW_PURPOSE,
        provider="ollama",
        model_profile="local-qwen3-4b",
        model_id="qwen3:4b",
        reservation=Reservation(micro=micro, tokens=tokens),
        deadline_seconds=30,
        deployment_budget_id=budget_id,
    )


def test_budget_admits_one_call_and_refuses_the_second(db_session):
    """O limite de tokens e o que garante UMA chamada (preco zero)."""
    assert (
        script.run(db_session, _database_name(db_session), MARGIN_BPS) == 0
    )
    budget = _budgets(db_session)[0]
    budget_id = budget.id
    limit = budget.limit_tokens
    # Reserva de uma conta real (2 digitos de dias) e <= limite de pior caso.
    real = limit - 2
    ledger = BudgetLedger(
        SessionLocal, unknown_fraction_bps=2500, recovery_grace_seconds=0
    )

    first = _reserve(ledger, budget_id, "s5-test:1", real)
    # Concorrente/segunda reserva enquanto a primeira esta em voo: nega.
    with pytest.raises(GatewayError) as in_flight:
        _reserve(ledger, budget_id, "s5-test:2", real)
    assert in_flight.value.code == GatewayErrorCode.BUDGET_EXCEEDED

    ledger.mark_in_flight(first, timeout_seconds=30)
    # Liquida com consumo real plausivel (entrada + saida): nao devolve o
    # limite ao saldo, pois consumo liquidado (S) permanece.
    ledger.settle(first, actual_micro=0, actual_tokens=300)

    with pytest.raises(GatewayError) as after_settle:
        _reserve(ledger, budget_id, "s5-test:3", real)
    assert after_settle.value.code == GatewayErrorCode.BUDGET_EXCEEDED
    assert ledger.verify_conservation(budget_id).consistent
    assert (
        db_session.query(IntelligenceModelCall).count() == 1
    )
