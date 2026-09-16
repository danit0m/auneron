"""
Pilot Action Space V1.C -- testes obrigatorios do Executable Contract
(congelado com Tomaz em 15/09/2026): precedencia de reason para
mark_overdue (due_date_mismatch antes de qualquer outra condicao),
distincao structurally_available/system_recommendable/
requires_external_fact para mark_paid (com a garantia explicita de que
nenhum sinal interno muda system_recommendable=false), e
due_date_matches_current_vencimento como campo de contexto que nunca
vira gate.
"""

from datetime import date
from datetime import timedelta

from app.core.governed_financial_action_eligibility import (
    get_mark_overdue_eligibility,
    get_mark_paid_eligibility,
)
from app.models.account import Account


def _make_account(
    db_session,
    *,
    vencimento: date,
    status: str = "aberto",
    cliente: str = "Cliente V1.C Teste",
    email: str | None = "cliente.v1c@example.com",
    valor=1000,
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


def _overdue_date() -> date:
    return date.today() - timedelta(days=10)


# ---------------------------------------------------------------------
# account.mark_overdue -- precedencia fixa de reason
# ---------------------------------------------------------------------


def test_mark_overdue_due_date_mismatch_takes_precedence(
    db_session,
) -> None:
    """
    Conta com vencimento atual != due_date solicitado -- mesmo que a
    conta esteja, por coincidencia, em estado que pareceria elegivel
    (aberto, vencida) sob OUTRA data -- deve reportar due_date_mismatch
    e nunca avaliar status/data adicionalmente.
    """

    real_due_date = _overdue_date()
    requested_due_date = real_due_date - timedelta(days=30)
    account = _make_account(db_session, vencimento=real_due_date)

    result = get_mark_overdue_eligibility(
        db_session, account=account, due_date=requested_due_date
    )

    assert result.structurally_available is False
    assert result.system_recommendable is False
    assert result.reason == "due_date_mismatch"
    assert result.requires_external_fact is None


def test_mark_overdue_reason_already_paid(db_session) -> None:
    due_date = _overdue_date()
    account = _make_account(
        db_session, vencimento=due_date, status="pago"
    )

    result = get_mark_overdue_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.structurally_available is False
    assert result.system_recommendable is False
    assert result.reason == "already_paid"


def test_mark_overdue_reason_status_not_open(db_session) -> None:
    due_date = _overdue_date()
    account = _make_account(
        db_session, vencimento=due_date, status="atrasado"
    )

    result = get_mark_overdue_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.structurally_available is False
    assert result.system_recommendable is False
    assert result.reason == "status_not_open"


def test_mark_overdue_reason_not_overdue(db_session) -> None:
    due_date = date.today() + timedelta(days=15)
    account = _make_account(db_session, vencimento=due_date)

    result = get_mark_overdue_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.structurally_available is False
    assert result.system_recommendable is False
    assert result.reason == "not_overdue"


def test_mark_overdue_available_when_aberto_and_overdue(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=42)
    account = _make_account(db_session, vencimento=due_date)

    result = get_mark_overdue_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.structurally_available is True
    assert result.system_recommendable is True
    assert result.reason is None
    assert result.requires_external_fact is None
    assert result.eligibility_policy == (
        "account_mark_overdue_eligibility_v1"
    )


# ---------------------------------------------------------------------
# account.mark_paid -- distincao central: disponivel != recomendavel
# ---------------------------------------------------------------------


def test_mark_paid_available_but_never_recommendable_when_aberto(
    db_session,
) -> None:
    """
    O teste explicito pedido: structurally_available=true nunca muda
    system_recommendable para true, e requires_external_fact permanece
    "payment_observed" -- nenhum sinal interno (aqui, nenhum sinal
    algum e passado a get_mark_paid_eligibility) pode alterar isso.
    """

    due_date = date.today() - timedelta(days=5)
    account = _make_account(db_session, vencimento=due_date, status="aberto")

    result = get_mark_paid_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.structurally_available is True
    assert result.system_recommendable is False
    assert result.requires_external_fact == "payment_observed"
    assert result.reason is None


def test_mark_paid_available_but_never_recommendable_when_atrasado(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=20)
    account = _make_account(
        db_session, vencimento=due_date, status="atrasado"
    )

    result = get_mark_paid_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.structurally_available is True
    assert result.system_recommendable is False
    assert result.requires_external_fact == "payment_observed"


def test_mark_paid_never_recommendable_regardless_of_amount_or_delay(
    db_session,
) -> None:
    """
    Mesmo uma conta com alto valor e muitos dias de atraso (sinais que
    poderiam, incorretamente, ser usados como proxy de "provavel
    pagamento") continua system_recommendable=false -- nenhum desses
    sinais e sequer lido por get_mark_paid_eligibility.
    """

    due_date = date.today() - timedelta(days=400)
    account = _make_account(
        db_session,
        vencimento=due_date,
        status="atrasado",
        valor=999999,
    )

    result = get_mark_paid_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.system_recommendable is False
    assert result.requires_external_fact == "payment_observed"


def test_mark_paid_ineligible_when_already_paid(db_session) -> None:
    due_date = date.today() - timedelta(days=5)
    account = _make_account(
        db_session, vencimento=due_date, status="pago"
    )

    result = get_mark_paid_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.structurally_available is False
    assert result.system_recommendable is False
    assert result.requires_external_fact is None
    assert result.reason == "already_paid"


def test_mark_paid_due_date_mismatch_is_context_not_gate(
    db_session,
) -> None:
    """
    due_date != account.vencimento nunca altera structurally_available/
    system_recommendable de mark_paid -- apenas o campo de contexto
    due_date_matches_current_vencimento reflete a divergencia.
    """

    real_due_date = date.today() - timedelta(days=5)
    requested_due_date = real_due_date - timedelta(days=100)
    account = _make_account(db_session, vencimento=real_due_date)

    result = get_mark_paid_eligibility(
        db_session, account=account, due_date=requested_due_date
    )

    assert result.structurally_available is True
    assert result.system_recommendable is False
    assert result.due_date_matches_current_vencimento is False


def test_mark_paid_due_date_matches_when_equal(db_session) -> None:
    due_date = date.today() - timedelta(days=5)
    account = _make_account(db_session, vencimento=due_date)

    result = get_mark_paid_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.due_date_matches_current_vencimento is True


# ---------------------------------------------------------------------
# I1 -- determinismo
# ---------------------------------------------------------------------


def test_same_facts_produce_same_decision_for_both_capabilities(
    db_session,
) -> None:
    due_date = _overdue_date()
    account = _make_account(db_session, vencimento=due_date)

    first_overdue = get_mark_overdue_eligibility(
        db_session, account=account, due_date=due_date
    )
    second_overdue = get_mark_overdue_eligibility(
        db_session, account=account, due_date=due_date
    )
    assert first_overdue == second_overdue

    first_paid = get_mark_paid_eligibility(
        db_session, account=account, due_date=due_date
    )
    second_paid = get_mark_paid_eligibility(
        db_session, account=account, due_date=due_date
    )
    assert first_paid == second_paid
