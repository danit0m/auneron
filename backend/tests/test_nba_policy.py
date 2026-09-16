"""
NBA V1 -- testes obrigatorios do Executable Contract (congelado com
Tomaz em 16/09/2026, com as 2 emendas finais): R0-R3, os boundaries
exatos de operador (> vs >=) para os dois gatilhos de R3, o caso de
dupla ativacao de applied_rules, a invariancia de recurrence perante a
decisao, a invariante selected_actions subset-of recommendable_actions,
e o snapshot contratual da calibracao inicial.
"""

from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

from app.core.config import Settings
from app.core.config import settings
from app.core.nba_policy import ESCALATE_TO_HUMAN_ACTION_KEY
from app.core.nba_policy import MARK_OVERDUE_ACTION_KEY
from app.core.nba_policy import get_nba_decision
from app.models.account import Account
from app.models.memory import MemoryItem


TEST_URL = (
    "postgresql+psycopg://"
    "auneron:test_password"
    "@localhost:5432/auneron_test"
)


def _make_account(
    db_session,
    *,
    vencimento: date,
    status: str = "aberto",
    valor: Decimal | float = 1000,
    cliente: str = "Cliente NBA Teste",
    email: str | None = "cliente.nba@example.com",
) -> Account:
    account = Account(
        cliente=cliente,
        email=email,
        whatsapp=None,
        valor=valor,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _insert_behavior_pattern(
    db_session,
    *,
    account_id: int,
    average_late_days: float = 7.0,
    resolved_occurrences: int = 5,
) -> MemoryItem:
    memory = MemoryItem(
        memory_type="observation",
        title="Memoria de teste -- NBA",
        content="Conteudo de teste.",
        memory_key="client_behavior_payment_pattern",
        scope_type="account",
        account_id=account_id,
        importance=Decimal("0.500"),
        confidence=Decimal("0.750"),
        status="active",
        valid_from=datetime.now(timezone.utc),
        source_type="derived",
        source_reference=f"test:nba:{account_id}",
        context_data={
            "email": None,
            "oldest_account_id": account_id,
            "ocorrencias_resolvidas": resolved_occurrences,
            "atraso_medio_dias": average_late_days,
            "atraso_min_dias": 1,
            "atraso_max_dias": 20,
            "taxa_pagamento": 0.9,
            "confidence": 0.75,
        },
    )
    db_session.add(memory)
    db_session.commit()
    db_session.refresh(memory)
    return memory


def _overdue_account(
    db_session,
    *,
    days_overdue: int,
    valor: Decimal | float,
    email: str | None = "cliente.nba@example.com",
) -> tuple[Account, date]:
    due_date = date.today() - timedelta(days=days_overdue)
    account = _make_account(
        db_session, vencimento=due_date, valor=valor, email=email
    )
    return account, due_date


# ---------------------------------------------------------------------
# R0 -- nenhuma capability recomendavel
# ---------------------------------------------------------------------


def test_r0_no_action_when_nothing_recommendable(db_session) -> None:
    due_date = date.today() + timedelta(days=30)
    account = _make_account(db_session, vencimento=due_date)

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.decision.decision_type == "no_action"
    assert result.decision.selected_actions == ()
    assert result.applied_rules == ()


# ---------------------------------------------------------------------
# R1 / R2 -- so uma capability recomendavel
# ---------------------------------------------------------------------


def test_r1_single_action_mark_overdue_only(db_session) -> None:
    # active_escalation suprime escalate_to_human via V1.B, deixando
    # so mark_overdue recomendavel -- prova R1 sem inventar um novo
    # mecanismo de supressao (S6, herdado).
    from app.models.work import WorkItem

    account, due_date = _overdue_account(
        db_session, days_overdue=10, valor=1000
    )

    work_key = f"human_escalation:v1:{account.id}:{due_date.isoformat()}"
    now = datetime.now(timezone.utc)
    db_session.add(
        WorkItem(
            work_type="task",
            title="Escalar atendimento humano",
            work_key=work_key,
            scope_type="account",
            account_id=account.id,
            subject_user_id=None,
            status="ready",
            origin_type="user",
            origin_reference=(
                f"human_escalation_recommendation:v1:{work_key}"
            ),
            context_data={},
            status_changed_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.action_space.recommendable_actions == (
        MARK_OVERDUE_ACTION_KEY,
    )
    assert result.decision.decision_type == "single_action"
    assert result.decision.selected_actions == (
        MARK_OVERDUE_ACTION_KEY,
    )
    assert result.applied_rules == ("mark_overdue_only_candidate",)
    assert result.observed_facts.active_escalation is True


def test_r2_single_action_escalate_only(db_session) -> None:
    # status ja "atrasado" -> mark_overdue sai de recommendable_actions
    # via V1.C (status_not_open), sem nenhuma logica nova do NBA.
    due_date = date.today() - timedelta(days=10)
    account = _make_account(
        db_session, vencimento=due_date, status="atrasado"
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.action_space.recommendable_actions == (
        ESCALATE_TO_HUMAN_ACTION_KEY,
    )
    assert result.decision.decision_type == "single_action"
    assert result.decision.selected_actions == (
        ESCALATE_TO_HUMAN_ACTION_KEY,
    )
    assert result.applied_rules == (
        "escalate_to_human_only_candidate",
    )


# ---------------------------------------------------------------------
# R3 -- ambas recomendaveis, mark_overdue NUNCA descartado pelo NBA
# ---------------------------------------------------------------------


def test_r3c_no_trigger_keeps_mark_overdue_alone(db_session) -> None:
    account, due_date = _overdue_account(
        db_session, days_overdue=10, valor=Decimal("1000")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.action_space.recommendable_actions == (
        MARK_OVERDUE_ACTION_KEY,
        ESCALATE_TO_HUMAN_ACTION_KEY,
    )
    assert result.decision.decision_type == "single_action"
    assert result.decision.selected_actions == (
        MARK_OVERDUE_ACTION_KEY,
    )
    assert result.applied_rules == ("mark_overdue_precedence",)


def test_r3a_high_exposure_early_trigger_alone(db_session) -> None:
    account, due_date = _overdue_account(
        db_session, days_overdue=5, valor=Decimal("15000.00")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.decision.decision_type == "action_bundle"
    assert result.decision.selected_actions == (
        MARK_OVERDUE_ACTION_KEY,
        ESCALATE_TO_HUMAN_ACTION_KEY,
    )
    assert result.applied_rules == ("high_exposure_early_escalation",)


def test_r3b_prolonged_overdue_trigger_alone(db_session) -> None:
    account, due_date = _overdue_account(
        db_session, days_overdue=46, valor=Decimal("500.01")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.decision.decision_type == "action_bundle"
    assert result.decision.selected_actions == (
        MARK_OVERDUE_ACTION_KEY,
        ESCALATE_TO_HUMAN_ACTION_KEY,
    )
    assert result.applied_rules == ("prolonged_overdue_escalation",)


def test_r3_both_triggers_produce_both_rule_names(db_session) -> None:
    """
    Emenda 1: quando os dois gatilhos sao verdadeiros simultaneamente,
    applied_rules preserva os dois nomes -- nunca apenas um.
    """

    account, due_date = _overdue_account(
        db_session, days_overdue=60, valor=Decimal("20000.00")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.decision.decision_type == "action_bundle"
    assert result.applied_rules == (
        "prolonged_overdue_escalation",
        "high_exposure_early_escalation",
    )


# ---------------------------------------------------------------------
# Boundaries exatos de operador (> vs >=)
# ---------------------------------------------------------------------


def test_prolonged_boundary_exactly_45_days_500_amount_is_false(
    db_session,
) -> None:
    account, due_date = _overdue_account(
        db_session, days_overdue=45, valor=Decimal("501.00")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    # 45 > 45 e False -- dias exatamente no limite nao dispara,
    # mesmo com valor acima do piso.
    assert "prolonged_overdue_escalation" not in result.applied_rules


def test_prolonged_boundary_46_days_exactly_500_amount_is_false(
    db_session,
) -> None:
    account, due_date = _overdue_account(
        db_session, days_overdue=46, valor=Decimal("500.00")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    # 500 > 500 e False -- valor exatamente no piso nao dispara.
    assert "prolonged_overdue_escalation" not in result.applied_rules


def test_prolonged_boundary_46_days_500_01_is_true(
    db_session,
) -> None:
    account, due_date = _overdue_account(
        db_session, days_overdue=46, valor=Decimal("500.01")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert "prolonged_overdue_escalation" in result.applied_rules


def test_high_exposure_boundary_4_days_15000_is_false(
    db_session,
) -> None:
    account, due_date = _overdue_account(
        db_session, days_overdue=4, valor=Decimal("15000.00")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    # 4 >= 5 e False.
    assert (
        "high_exposure_early_escalation" not in result.applied_rules
    )


def test_high_exposure_boundary_5_days_14999_99_is_false(
    db_session,
) -> None:
    account, due_date = _overdue_account(
        db_session, days_overdue=5, valor=Decimal("14999.99")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    # 14999.99 >= 15000 e False.
    assert (
        "high_exposure_early_escalation" not in result.applied_rules
    )


def test_high_exposure_boundary_5_days_15000_is_true(
    db_session,
) -> None:
    account, due_date = _overdue_account(
        db_session, days_overdue=5, valor=Decimal("15000.00")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert "high_exposure_early_escalation" in result.applied_rules


# ---------------------------------------------------------------------
# Invariancia de recurrence perante a decisao
# ---------------------------------------------------------------------


def test_recurrence_never_changes_decision_or_applied_rules(
    db_session,
) -> None:
    account_a, due_date_a = _overdue_account(
        db_session,
        days_overdue=10,
        valor=Decimal("1000"),
        email="cliente.nba.recurrence.a@example.com",
    )
    account_b, due_date_b = _overdue_account(
        db_session,
        days_overdue=10,
        valor=Decimal("1000"),
        email="cliente.nba.recurrence.b@example.com",
    )

    _insert_behavior_pattern(
        db_session,
        account_id=account_b.id,
        average_late_days=45.0,
        resolved_occurrences=99,
    )

    result_a = get_nba_decision(
        db_session, account=account_a, due_date=due_date_a
    )
    result_b = get_nba_decision(
        db_session, account=account_b, due_date=due_date_b
    )

    assert result_a.observed_facts.recurrence is None
    assert result_b.observed_facts.recurrence is not None
    assert (
        result_b.observed_facts.recurrence.average_late_days == 45.0
    )

    assert (
        result_a.decision.decision_type
        == result_b.decision.decision_type
    )
    assert (
        result_a.decision.selected_actions
        == result_b.decision.selected_actions
    )
    assert result_a.applied_rules == result_b.applied_rules


# ---------------------------------------------------------------------
# Invariante: selected_actions subset-of recommendable_actions
# ---------------------------------------------------------------------


def test_selected_actions_is_always_subset_of_recommendable_actions(
    db_session,
) -> None:
    scenarios = [
        (30, Decimal("1000"), "aberto"),
        (10, Decimal("1000"), "aberto"),
        (5, Decimal("15000"), "aberto"),
        (46, Decimal("500.01"), "aberto"),
        (60, Decimal("20000"), "aberto"),
    ]

    for index, (days_overdue, valor, status) in enumerate(scenarios):
        due_date = date.today() - timedelta(days=days_overdue)
        account = _make_account(
            db_session,
            vencimento=due_date,
            valor=valor,
            status=status,
            email=f"cliente.nba.subset.{index}@example.com",
        )

        result = get_nba_decision(
            db_session, account=account, due_date=due_date
        )

        assert set(result.decision.selected_actions).issubset(
            set(result.action_space.recommendable_actions)
        )


# ---------------------------------------------------------------------
# Snapshot contratual da calibracao inicial
# ---------------------------------------------------------------------


EXPECTED_INITIAL_CALIBRATION = {
    "version": "initial_calibration_reference_v1",
    "critical_overdue_reference_days": 45,
    "early_high_exposure_reference_day": 5,
    "absolute_high_value_reference": Decimal("15000.00"),
    "operational_cost_floor_reference": Decimal("500.00"),
}


def test_initial_calibration_matches_frozen_snapshot() -> None:
    assert settings.nba_calibration_version == (
        EXPECTED_INITIAL_CALIBRATION["version"]
    )
    assert settings.nba_critical_overdue_reference_days == (
        EXPECTED_INITIAL_CALIBRATION[
            "critical_overdue_reference_days"
        ]
    )
    assert settings.nba_early_high_exposure_reference_day == (
        EXPECTED_INITIAL_CALIBRATION[
            "early_high_exposure_reference_day"
        ]
    )
    assert settings.nba_absolute_high_value_reference == (
        EXPECTED_INITIAL_CALIBRATION["absolute_high_value_reference"]
    )
    assert settings.nba_operational_cost_floor_reference == (
        EXPECTED_INITIAL_CALIBRATION[
            "operational_cost_floor_reference"
        ]
    )


def test_decimal_settings_load_from_env_without_float_precision_loss(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "NBA_ABSOLUTE_HIGH_VALUE_REFERENCE", "15000.50"
    )
    monkeypatch.setenv(
        "NBA_OPERATIONAL_COST_FLOOR_REFERENCE", "500.25"
    )

    loaded = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL=TEST_URL,
    )

    assert loaded.nba_absolute_high_value_reference == Decimal(
        "15000.50"
    )
    assert isinstance(
        loaded.nba_absolute_high_value_reference, Decimal
    )
    assert loaded.nba_operational_cost_floor_reference == Decimal(
        "500.25"
    )
    assert isinstance(
        loaded.nba_operational_cost_floor_reference, Decimal
    )


# ---------------------------------------------------------------------
# policy_version / applied_rules nunca vazio quando decision != no_action
# ---------------------------------------------------------------------


def test_applied_rules_never_empty_when_decision_is_not_no_action(
    db_session,
) -> None:
    account, due_date = _overdue_account(
        db_session, days_overdue=10, valor=Decimal("1000")
    )

    result = get_nba_decision(
        db_session, account=account, due_date=due_date
    )

    assert result.decision.decision_type != "no_action"
    assert len(result.applied_rules) > 0
    assert result.policy_version == "nba_policy_v1"
