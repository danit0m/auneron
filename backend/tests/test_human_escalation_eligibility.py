"""
Pilot Action Space V1.B -- testes obrigatorios do Executable Contract
(congelado com Tomaz em 15/09/2026): os 5 casos de supressao do
Secao 4 (terminal/nao-terminal, outro namespace, outro episodio),
a precedencia fixa de `reason`, os invariantes I1/I3/I4/I5, e a prova
de espaco de decisao real do account #17 (eligible junto de
mark_overdue).
"""

from datetime import date
from datetime import timedelta

from app.core.human_escalation_eligibility import (
    get_human_escalation_eligibility,
)
from app.models.account import Account
from app.models.work import WorkItem


def _make_account(
    db_session,
    *,
    vencimento: date,
    status: str = "aberto",
    cliente: str = "Cliente Escalation Teste",
    email: str | None = "cliente.escalation@example.com",
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


def _make_work_item(
    db_session,
    *,
    account_id: int,
    work_key: str,
    status: str,
) -> WorkItem:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    kwargs = dict(
        work_type="task",
        title="Escalar atendimento humano",
        work_key=work_key,
        scope_type="account",
        account_id=account_id,
        subject_user_id=None,
        status=status,
        origin_type="user",
        origin_reference=f"human_escalation_recommendation:v1:{work_key}",
        context_data={},
        status_changed_at=now,
        created_at=now,
        updated_at=now,
    )

    if status in ("in_progress", "blocked", "completed"):
        kwargs["started_at"] = now
    if status == "completed":
        kwargs["completed_at"] = now
    if status == "cancelled":
        kwargs["cancelled_at"] = now
        kwargs["status_reason"] = "Encerrado em teste."
    if status == "blocked":
        kwargs["blocked_reason"] = "Bloqueado em teste."

    item = WorkItem(**kwargs)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def _overdue_date() -> date:
    return date.today() - timedelta(days=10)


# ---------------------------------------------------------------------
# Precedencia fixa de reason
# ---------------------------------------------------------------------


def test_reason_is_account_paid_when_status_is_pago(
    db_session,
) -> None:
    due_date = _overdue_date()
    account = _make_account(
        db_session, vencimento=due_date, status="pago"
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "ineligible"
    assert result.reason == "account_paid"
    assert result.suppressing_work_item_id is None
    assert result.support_evidence is None


def test_reason_is_lifecycle_not_overdue_when_not_yet_due(
    db_session,
) -> None:
    due_date = date.today() + timedelta(days=30)
    account = _make_account(db_session, vencimento=due_date)

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "ineligible"
    assert result.reason == "lifecycle_not_overdue"
    assert result.suppressing_work_item_id is None
    assert result.support_evidence is None


def test_reason_precedence_account_paid_wins_over_active_escalation(
    db_session,
) -> None:
    """
    Uma conta paga com um WorkItem ativo (dados incoerentes, mas
    tecnicamente possiveis) deve reportar account_paid -- primeiro na
    precedencia congelada -- nunca active_escalation_exists.
    """

    due_date = _overdue_date()
    account = _make_account(
        db_session, vencimento=due_date, status="pago"
    )

    _make_work_item(
        db_session,
        account_id=account.id,
        work_key=f"human_escalation:v1:{account.id}:{due_date.isoformat()}",
        status="ready",
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.reason == "account_paid"


# ---------------------------------------------------------------------
# Secao 4 -- os 5 casos de supressao
# ---------------------------------------------------------------------


def test_active_work_item_suppresses_ready(db_session) -> None:
    due_date = _overdue_date()
    account = _make_account(db_session, vencimento=due_date)

    _make_work_item(
        db_session,
        account_id=account.id,
        work_key=f"human_escalation:v1:{account.id}:{due_date.isoformat()}",
        status="ready",
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "ineligible"
    assert result.reason == "active_escalation_exists"


def test_active_work_item_suppresses_in_progress(db_session) -> None:
    due_date = _overdue_date()
    account = _make_account(db_session, vencimento=due_date)

    work_item = _make_work_item(
        db_session,
        account_id=account.id,
        work_key=f"human_escalation:v1:{account.id}:{due_date.isoformat()}",
        status="in_progress",
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "ineligible"
    assert result.reason == "active_escalation_exists"
    assert result.suppressing_work_item_id == work_item.id


def test_active_work_item_suppresses_blocked(db_session) -> None:
    due_date = _overdue_date()
    account = _make_account(db_session, vencimento=due_date)

    _make_work_item(
        db_session,
        account_id=account.id,
        work_key=f"human_escalation:v1:{account.id}:{due_date.isoformat()}",
        status="blocked",
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "ineligible"
    assert result.reason == "active_escalation_exists"


def test_completed_work_item_does_not_suppress(db_session) -> None:
    due_date = _overdue_date()
    account = _make_account(db_session, vencimento=due_date)

    _make_work_item(
        db_session,
        account_id=account.id,
        work_key=f"human_escalation:v1:{account.id}:{due_date.isoformat()}",
        status="completed",
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "eligible"
    assert result.reason is None
    assert result.suppressing_work_item_id is None


def test_cancelled_work_item_does_not_suppress(db_session) -> None:
    due_date = _overdue_date()
    account = _make_account(db_session, vencimento=due_date)

    _make_work_item(
        db_session,
        account_id=account.id,
        work_key=f"human_escalation:v1:{account.id}:{due_date.isoformat()}",
        status="cancelled",
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "eligible"
    assert result.reason is None


def test_active_work_item_of_another_namespace_does_not_suppress(
    db_session,
) -> None:
    due_date = _overdue_date()
    account = _make_account(db_session, vencimento=due_date)

    _make_work_item(
        db_session,
        account_id=account.id,
        work_key=f"advisory:1:binding:1",
        status="in_progress",
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "eligible"
    assert result.reason is None


def test_active_escalation_of_another_episode_does_not_suppress(
    db_session,
) -> None:
    due_date = _overdue_date()
    other_due_date = due_date - timedelta(days=60)
    account = _make_account(db_session, vencimento=due_date)

    _make_work_item(
        db_session,
        account_id=account.id,
        work_key=(
            f"human_escalation:v1:{account.id}:"
            f"{other_due_date.isoformat()}"
        ),
        status="in_progress",
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "eligible"
    assert result.reason is None


# ---------------------------------------------------------------------
# I1 -- determinismo: mesmos fatos, mesma recommendation_key e decisao
# ---------------------------------------------------------------------


def test_same_facts_produce_same_recommendation_key_and_decision(
    db_session,
) -> None:
    due_date = _overdue_date()
    account = _make_account(db_session, vencimento=due_date)

    first = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )
    second = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert first.recommendation_key == second.recommendation_key
    assert first.status == second.status
    assert first.recommendation_key == (
        f"human_escalation_recommendation:v1:{account.id}:"
        f"{due_date.isoformat()}"
    )


# ---------------------------------------------------------------------
# I5 -- ausencia de evidencia explicativa nunca altera o gate
# ---------------------------------------------------------------------


def test_absence_of_optional_signals_never_blocks_eligibility(
    db_session,
) -> None:
    due_date = _overdue_date()
    account = _make_account(
        db_session, vencimento=due_date, email=None
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "eligible"
    assert result.support_evidence is not None
    assert result.support_evidence.classification is None
    assert result.support_evidence.average_late_days is None
    assert result.support_evidence.prior_episodes == ()


# ---------------------------------------------------------------------
# Prova de espaco de decisao real -- account #17 do survey (42 dias em
# atraso, aberto, sem WorkItem) reproduzida como propriedade testavel:
# mark_overdue elegivel (via lifecycle overdue) E escalate_to_human
# elegivel simultaneamente, sem qualquer supressao.
# ---------------------------------------------------------------------


def test_overdue_open_account_without_work_item_is_eligible(
    db_session,
) -> None:
    due_date = date.today() - timedelta(days=42)
    account = _make_account(
        db_session, vencimento=due_date, status="aberto"
    )

    result = get_human_escalation_eligibility(
        db_session, account=account, due_date=due_date
    )

    assert result.status == "eligible"
    assert result.support_evidence.days_overdue == 42
