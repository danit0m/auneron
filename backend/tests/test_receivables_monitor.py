"""
F3 -- Receivables Monitor (app/core/receivables_monitor_maintenance.py).

Cobre a ordem congelada em design freeze com Tomaz em 12/09/2026:
verificar correlation_key existente ANTES de resolver/superseder o
Knowledge anterior, para que reexecucoes sem transicao real de estado
nao dupliquem nem resolvam um alerta ainda valido.

knowledge e accounts sao truncados pelo fixture autouse de
tests/conftest.py entre cada teste (RESTART IDENTITY CASCADE), entao
nao ha necessidade de account_id/vencimento unicos entre os testes
deste arquivo.
"""

from datetime import date
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.receivables_monitor_maintenance import (
    run_receivables_monitor,
)
from app.models.account import Account
from app.models.knowledge import Knowledge


def _make_account(
    db_session: Session,
    *,
    vencimento: date,
    status: str = "aberto",
    cliente: str = "Cliente F3 Teste",
) -> Account:
    account = Account(
        cliente=cliente,
        email="cliente.f3@example.com",
        whatsapp=None,
        valor=100,
        vencimento=vencimento,
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _receivable_knowledge(
    db_session: Session,
    account_id: int,
) -> list[Knowledge]:
    return (
        db_session.query(Knowledge)
        .filter(
            Knowledge.account_id == account_id,
            Knowledge.knowledge_type == "receivable_lifecycle",
        )
        .order_by(Knowledge.id.asc())
        .all()
    )


def test_creates_knowledge_on_first_overdue_detection(
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        vencimento=date.today() - timedelta(days=2),
    )

    result = run_receivables_monitor()

    assert result.knowledge_created == 1
    assert result.knowledge_resolved == 0

    rows = _receivable_knowledge(db_session, account.id)
    assert len(rows) == 1
    assert rows[0].correlation_key == (
        f"receivable_lifecycle:v1:{account.id}:"
        f"{account.vencimento.isoformat()}:overdue"
    )
    assert rows[0].resolved is False
    assert rows[0].resolved_at is None


def test_rerun_without_state_change_does_not_duplicate_or_resolve(
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        vencimento=date.today() - timedelta(days=2),
    )

    run_receivables_monitor()
    first_run_rows = _receivable_knowledge(db_session, account.id)
    assert len(first_run_rows) == 1

    result = run_receivables_monitor()

    assert result.knowledge_created == 0
    assert result.knowledge_resolved == 0
    assert result.skipped == 1

    rows = _receivable_knowledge(db_session, account.id)
    assert len(rows) == 1, (
        "reexecucao sem mudanca de estado nao deve duplicar Knowledge"
    )
    assert rows[0].id == first_run_rows[0].id
    assert rows[0].resolved is False
    assert rows[0].resolved_at is None, (
        "o bug de ordenacao corrigido no design freeze resolveria "
        "esta linha mesmo sem nenhuma transicao real de estado"
    )


def test_transition_to_new_state_resolves_previous_and_creates_new(
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        vencimento=date.today() - timedelta(days=2),
    )

    run_receivables_monitor()

    account.vencimento = date.today() - timedelta(days=7)
    db_session.commit()

    result = run_receivables_monitor()

    assert result.knowledge_created == 1
    assert result.knowledge_resolved == 1

    rows = _receivable_knowledge(db_session, account.id)
    assert len(rows) == 2

    previous, current = rows
    assert previous.correlation_key.endswith(":overdue")
    assert previous.resolved is True
    assert previous.resolved_at is not None

    assert current.correlation_key.endswith(":overdue_alert")
    assert current.resolved is False
    assert current.resolved_at is None


def test_paid_account_resolves_active_alert_without_creating_new(
    db_session: Session,
) -> None:
    account = _make_account(
        db_session,
        vencimento=date.today() - timedelta(days=7),
    )

    run_receivables_monitor()

    account.status = "pago"
    db_session.commit()

    result = run_receivables_monitor()

    assert result.knowledge_created == 0
    assert result.knowledge_resolved == 1

    rows = _receivable_knowledge(db_session, account.id)
    assert len(rows) == 1, (
        "pagamento nao deve criar Knowledge novo, so resolver o ativo"
    )
    assert rows[0].resolved is True
    assert rows[0].resolved_at is not None


def test_resolving_multiple_active_rows_counts_correctly(
    db_session: Session,
) -> None:
    """
    Regressao do BLOCK encontrado em revisao (4/10): o contador
    knowledge_resolved somava um booleano em vez do numero real de
    linhas afetadas por _resolve_active_receivable_knowledge(). Isso
    nunca corrompe o banco (a UPDATE em si sempre afeta todas as
    linhas ativas corretamente), mas o resultado retornado por
    run_receivables_monitor() ficava sub-relatado sempre que mais de
    uma linha ativa existisse para a mesma conta -- um estado que a
    operacao normal do monitor nunca produz sozinha (correlation_key
    garante no maximo uma linha ativa por vez), mas que este teste
    simula diretamente para provar que a contagem esta correta mesmo
    fora do caminho feliz.
    """
    account = _make_account(
        db_session,
        vencimento=date.today() - timedelta(days=2),
    )

    run_receivables_monitor()

    extra_active_row = Knowledge(
        agent_name="ReceivablesMonitorAgent",
        event_name="receivable_lifecycle_changed",
        knowledge_type="receivable_lifecycle",
        severity="critical",
        title="Linha ativa extra (simulada) — Cliente F3 Teste",
        message="Linha extra inserida diretamente pelo teste.",
        account_id=account.id,
        correlation_key=(
            f"receivable_lifecycle:v1:{account.id}:"
            f"{account.vencimento.isoformat()}:overdue_alert"
        ),
        resolved=False,
    )
    db_session.add(extra_active_row)
    db_session.commit()

    account.status = "pago"
    db_session.commit()

    result = run_receivables_monitor()

    assert result.knowledge_created == 0
    assert result.knowledge_resolved == 2

    # run_receivables_monitor() escreve por uma sessao propria e
    # separada; db_session ainda tem extra_active_row em seu identity
    # map de antes do db_session.add() acima, entao precisa descartar
    # esse cache antes de reler o estado persistido pela outra sessao.
    db_session.expire_all()

    rows = _receivable_knowledge(db_session, account.id)
    assert len(rows) == 2
    for row in rows:
        assert row.resolved is True
        assert row.resolved_at is not None
