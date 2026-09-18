"""
Action Space Evaluator V1 -- testes obrigatorios T1-T5 do Executable
Contract (congelado com Tomaz em 16/09/2026): conta #17 (duas
recomendaveis), cenario inverso (nenhuma recomendavel, mark_paid ainda
assim structurally_available), vocabulario de reason nao-normalizado,
ordem fisica fixa (nao-ranking), e recommendable_actions como projecao
mecanica exata de actions.
"""

from datetime import date
from datetime import timedelta

from app.core.action_space_evaluator import (
    ESCALATE_TO_HUMAN_ACTION_KEY,
    MARK_OVERDUE_ACTION_KEY,
    MARK_PAID_ACTION_KEY,
    get_action_space_evaluation,
)
from app.models.account import Account


def _make_account(
    db_session,
    *,
    vencimento: date,
    status: str = "aberto",
    cliente: str = "Cliente Action Space Teste",
    email: str | None = "cliente.action.space@example.com",
) -> Account:
    account = Account(
        cliente=cliente,
        email=email,
        whatsapp=None,
        valor=1000,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


# ---------------------------------------------------------------------
# T1 -- conta #17: mark_overdue + escalate_to_human recomendaveis,
# mark_paid disponivel mas nunca recomendavel
# ---------------------------------------------------------------------


def test_t1_overdue_open_account_two_recommendable_one_conditional(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=42)
    account = _make_account(db_session, vencimento=due_date)

    result = get_action_space_evaluation(
        db_session, account=account, due_date=due_date
    )

    by_key = {a.action_key: a for a in result.actions}

    assert by_key[MARK_OVERDUE_ACTION_KEY].system_recommendable is True
    assert by_key[ESCALATE_TO_HUMAN_ACTION_KEY].system_recommendable is True

    mark_paid = by_key[MARK_PAID_ACTION_KEY]
    assert mark_paid.structurally_available is True
    assert mark_paid.system_recommendable is False
    assert mark_paid.requires_external_fact == "payment_observed"

    assert result.recommendable_actions == (
        MARK_OVERDUE_ACTION_KEY,
        ESCALATE_TO_HUMAN_ACTION_KEY,
    )
    assert MARK_PAID_ACTION_KEY not in result.recommendable_actions
    assert result.no_action is False


# ---------------------------------------------------------------------
# T2 -- cenario inverso: nenhuma capability recomendavel, mas mark_paid
# permanece structurally_available/requires_external_fact
# ---------------------------------------------------------------------


def test_t2_not_yet_due_account_no_recommendable_actions(
    db_session,
) -> None:
    due_date = date.today() + timedelta(days=30)
    account = _make_account(db_session, vencimento=due_date)

    result = get_action_space_evaluation(
        db_session, account=account, due_date=due_date
    )

    by_key = {a.action_key: a for a in result.actions}

    assert by_key[MARK_OVERDUE_ACTION_KEY].system_recommendable is False
    assert by_key[ESCALATE_TO_HUMAN_ACTION_KEY].system_recommendable is False

    mark_paid = by_key[MARK_PAID_ACTION_KEY]
    assert mark_paid.system_recommendable is False
    assert mark_paid.structurally_available is True
    assert mark_paid.requires_external_fact == "payment_observed"

    assert result.recommendable_actions == ()
    assert result.no_action is True


# ---------------------------------------------------------------------
# T3 -- reason transportado sem normalizacao de vocabulario
# ---------------------------------------------------------------------


def test_t3_reason_vocabulary_is_not_unified_across_capabilities(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=5)
    account = _make_account(
        db_session, vencimento=due_date, status="pago"
    )

    result = get_action_space_evaluation(
        db_session, account=account, due_date=due_date
    )

    by_key = {a.action_key: a for a in result.actions}

    assert by_key[MARK_OVERDUE_ACTION_KEY].reason == "already_paid"
    assert by_key[ESCALATE_TO_HUMAN_ACTION_KEY].reason == "account_paid"
    assert (
        by_key[MARK_OVERDUE_ACTION_KEY].reason
        != by_key[ESCALATE_TO_HUMAN_ACTION_KEY].reason
    )
    assert by_key[MARK_PAID_ACTION_KEY].reason == "already_paid"


# ---------------------------------------------------------------------
# P1.3B.2a -- episodio obsoleto (account.vencimento != due_date pedido)
# nunca e recomendavel para escalate_to_human, herdado automaticamente
# do ponto unico get_human_escalation_eligibility() -- nenhuma logica
# nova neste avaliador.
# ---------------------------------------------------------------------


def test_obsolete_episode_never_recommends_escalate_to_human(
    db_session,
) -> None:
    requested_due_date = date.today() - timedelta(days=42)
    actual_vencimento = requested_due_date - timedelta(days=15)
    account = _make_account(
        db_session, vencimento=actual_vencimento
    )

    result = get_action_space_evaluation(
        db_session, account=account, due_date=requested_due_date
    )

    by_key = {a.action_key: a for a in result.actions}

    assert (
        by_key[ESCALATE_TO_HUMAN_ACTION_KEY].system_recommendable
        is False
    )
    assert (
        by_key[ESCALATE_TO_HUMAN_ACTION_KEY].reason
        == "due_date_mismatch"
    )
    assert (
        ESCALATE_TO_HUMAN_ACTION_KEY
        not in result.recommendable_actions
    )


# ---------------------------------------------------------------------
# T4 -- ordem fisica fixa, nao-ranking, em cenarios distintos
# ---------------------------------------------------------------------


def test_t4_actions_order_is_fixed_regardless_of_recommendability(
    db_session,
) -> None:
    overdue_due_date = date.today() - timedelta(days=42)
    overdue_account = _make_account(
        db_session, vencimento=overdue_due_date
    )
    result_t1 = get_action_space_evaluation(
        db_session, account=overdue_account, due_date=overdue_due_date
    )
    assert [a.action_key for a in result_t1.actions] == [
        MARK_OVERDUE_ACTION_KEY,
        MARK_PAID_ACTION_KEY,
        ESCALATE_TO_HUMAN_ACTION_KEY,
    ]

    future_due_date = date.today() + timedelta(days=30)
    future_account = _make_account(
        db_session,
        vencimento=future_due_date,
        email="cliente.action.space.future@example.com",
    )
    result_t2 = get_action_space_evaluation(
        db_session, account=future_account, due_date=future_due_date
    )
    assert [a.action_key for a in result_t2.actions] == [
        MARK_OVERDUE_ACTION_KEY,
        MARK_PAID_ACTION_KEY,
        ESCALATE_TO_HUMAN_ACTION_KEY,
    ]


# ---------------------------------------------------------------------
# T5 -- recommendable_actions e EXATAMENTE a projecao mecanica de
# actions, reconstruida e comparada, nunca hardcoded
# ---------------------------------------------------------------------


def test_t5_recommendable_actions_is_exact_mechanical_projection(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=42)
    account = _make_account(db_session, vencimento=due_date)

    result = get_action_space_evaluation(
        db_session, account=account, due_date=due_date
    )

    expected = tuple(
        action.action_key
        for action in result.actions
        if action.system_recommendable is True
    )

    assert result.recommendable_actions == expected
    assert result.no_action == (len(expected) == 0)


# ---------------------------------------------------------------------
# Registry estatico -- metadados corretos por capability
# ---------------------------------------------------------------------


def test_action_metadata_registry_matches_frozen_contract(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=42)
    account = _make_account(db_session, vencimento=due_date)

    result = get_action_space_evaluation(
        db_session, account=account, due_date=due_date
    )
    by_key = {a.action_key: a for a in result.actions}

    mark_overdue = by_key[MARK_OVERDUE_ACTION_KEY]
    assert mark_overdue.capability_kind == "skill_action"
    assert mark_overdue.initiation_actor == "agent"
    assert mark_overdue.authority_model == "approval"
    assert mark_overdue.required_authority == "approval:decide"
    assert mark_overdue.execution_corridor == (
        "advisory_approval_work_skill"
    )

    mark_paid = by_key[MARK_PAID_ACTION_KEY]
    assert mark_paid.capability_kind == "skill_action"
    assert mark_paid.initiation_actor == "user"
    assert mark_paid.authority_model == "approval"
    assert mark_paid.required_authority == "approval:decide"
    assert mark_paid.execution_corridor == "direct_approval_skill"

    escalate = by_key[ESCALATE_TO_HUMAN_ACTION_KEY]
    assert escalate.capability_kind == "work_action"
    assert escalate.initiation_actor == "rbac_actor"
    assert escalate.authority_model == "rbac"
    assert escalate.required_authority == "work:create"
    assert escalate.execution_corridor == "work_materialization"


# ---------------------------------------------------------------------
# actions sempre contem exatamente 3 itens
# ---------------------------------------------------------------------


def test_actions_always_contains_exactly_three_items(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=1)
    account = _make_account(db_session, vencimento=due_date)

    result = get_action_space_evaluation(
        db_session, account=account, due_date=due_date
    )

    assert len(result.actions) == 3
    assert {a.action_key for a in result.actions} == {
        MARK_OVERDUE_ACTION_KEY,
        MARK_PAID_ACTION_KEY,
        ESCALATE_TO_HUMAN_ACTION_KEY,
    }
