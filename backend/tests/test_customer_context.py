"""
Customer Intelligence 360 V1 -- testes obrigatorios do Aggregation
Contract (congelado com Tomaz em 15/09/2026): caso dificil 1
(email=None), caso dificil 2 (requested vs. anchor), reconstruibilidade
dos 4 agregados de financial_summary, mapeamento exato de
ClientBehaviorPatternSummary, composicao de episodes via
get_outcome_episode, e igualdade exata de email (sem normalizacao).
"""

from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

from app.core.customer_context import get_customer_context
from app.models.account import Account
from app.models.memory import MemoryItem


def _make_account(
    db_session,
    *,
    email: str | None,
    vencimento: date,
    status: str = "aberto",
    cliente: str = "Cliente 360 Teste",
    valor: Decimal | float = 1000,
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


def _insert_memory(
    db_session,
    *,
    account_id: int,
    memory_type: str,
    memory_key: str,
    context_data: dict,
    confidence: float = 0.8,
) -> MemoryItem:
    memory = MemoryItem(
        memory_type=memory_type,
        title="Memoria de teste -- Customer 360",
        content="Conteudo de teste.",
        memory_key=memory_key,
        scope_type="account",
        account_id=account_id,
        importance=Decimal("0.500"),
        confidence=Decimal(str(confidence)),
        status="active",
        valid_from=datetime.now(timezone.utc),
        source_type="derived",
        source_reference=f"test:{memory_key}:{account_id}",
        context_data=context_data,
    )
    db_session.add(memory)
    db_session.commit()
    db_session.refresh(memory)
    return memory


# ---------------------------------------------------------------------
# Caso dificil 1 -- email=None nunca agrega com outras contas NULL
# ---------------------------------------------------------------------


def test_email_none_produces_single_account_group(
    db_session,
) -> None:
    due_date = date.today() + timedelta(days=10)

    account_a = _make_account(
        db_session, email=None, vencimento=due_date
    )
    account_b = _make_account(
        db_session, email=None, vencimento=due_date
    )

    result = get_customer_context(
        db_session, requested_account=account_a
    )

    assert result.aggregation_identity.method == (
        "single_account_no_email"
    )
    assert result.aggregation_identity.linkage == (
        "none_single_account"
    )
    assert result.aggregation_identity.value is None
    assert result.aggregation_identity.anchor_account_id == (
        account_a.id
    )
    assert [a.account_id for a in result.accounts] == [
        account_a.id
    ]
    assert account_b.id not in [
        a.account_id for a in result.accounts
    ]


# ---------------------------------------------------------------------
# Caso dificil 2 -- requested_account_id distinto de anchor_account_id
# ---------------------------------------------------------------------


def test_requested_account_id_differs_from_anchor_when_not_oldest(
    db_session,
) -> None:
    email = "cliente.360.ancora@example.com"
    due_date_1 = date.today() + timedelta(days=5)
    due_date_2 = date.today() + timedelta(days=15)

    oldest = _make_account(
        db_session, email=email, vencimento=due_date_1
    )
    newest = _make_account(
        db_session, email=email, vencimento=due_date_2
    )

    result = get_customer_context(
        db_session, requested_account=newest
    )

    assert result.requested_account_id == newest.id
    assert result.aggregation_identity.anchor_account_id == (
        oldest.id
    )
    assert result.requested_account_id != (
        result.aggregation_identity.anchor_account_id
    )
    assert {a.account_id for a in result.accounts} == {
        oldest.id,
        newest.id,
    }


# ---------------------------------------------------------------------
# Igualdade exata de email -- sem normalizacao (D6)
# ---------------------------------------------------------------------


def test_email_grouping_is_exact_match_no_normalization(
    db_session,
) -> None:
    due_date = date.today() + timedelta(days=20)

    lower = _make_account(
        db_session,
        email="cliente.360.case@example.com",
        vencimento=due_date,
    )
    upper = _make_account(
        db_session,
        email="Cliente.360.Case@example.com",
        vencimento=due_date,
    )

    result = get_customer_context(
        db_session, requested_account=lower
    )

    account_ids = {a.account_id for a in result.accounts}

    assert lower.id in account_ids
    assert upper.id not in account_ids, (
        "D6: nenhuma normalizacao de case deve ser aplicada -- "
        "emails com maiuscula/minuscula diferentes permanecem "
        "grupos separados"
    )


# ---------------------------------------------------------------------
# Reconstruibilidade de financial_summary (4 agregados)
# ---------------------------------------------------------------------


def test_financial_summary_is_reconstructible_from_accounts(
    db_session,
) -> None:
    email = "cliente.360.financeiro@example.com"

    paid = _make_account(
        db_session,
        email=email,
        vencimento=date.today() - timedelta(days=30),
        status="pago",
        valor=1000,
    )
    overdue = _make_account(
        db_session,
        email=email,
        vencimento=date.today() - timedelta(days=10),
        status="aberto",
        valor=2000,
    )
    due_soon = _make_account(
        db_session,
        email=email,
        vencimento=date.today() + timedelta(days=5),
        status="aberto",
        valor=3000,
    )

    result = get_customer_context(
        db_session, requested_account=paid
    )

    summary = result.financial_summary
    account_ids = {a.account_id for a in result.accounts}

    assert summary.total_exposure == Decimal("6000")
    assert set(summary.total_exposure_account_ids) == account_ids
    assert summary.overdue_count == 1
    assert set(summary.overdue_account_ids) == {overdue.id}
    assert summary.due_soon_count == 1
    assert set(summary.due_soon_account_ids) == {due_soon.id}

    # next_due_date so exclui "paid" (formula congelada, Aggregation
    # Contract Secao 4) -- NAO exclui contas ja vencidas. Como
    # `overdue.vencimento` (passado) e menor que `due_soon.vencimento`
    # (futuro), o minimo real e o da conta vencida. Nao presumir "a
    # proxima data futura" aqui -- derivar a mesma forma que a producao.
    expected_next_due = min(
        overdue.vencimento, due_soon.vencimento
    )
    assert summary.next_due_date == expected_next_due
    assert set(summary.next_due_account_ids) == {overdue.id}

    # reconstruibilidade: cada *_account_ids e subconjunto de accounts
    for ids in (
        summary.total_exposure_account_ids,
        summary.overdue_account_ids,
        summary.due_soon_account_ids,
        summary.next_due_account_ids,
    ):
        assert set(ids).issubset(account_ids)


def test_next_due_account_ids_includes_all_tied_accounts(
    db_session,
) -> None:
    email = "cliente.360.empate@example.com"
    tied_date = date.today() + timedelta(days=7)

    account_a = _make_account(
        db_session, email=email, vencimento=tied_date
    )
    account_b = _make_account(
        db_session, email=email, vencimento=tied_date
    )
    _make_account(
        db_session,
        email=email,
        vencimento=tied_date + timedelta(days=1),
    )

    result = get_customer_context(
        db_session, requested_account=account_a
    )

    assert result.financial_summary.next_due_date == tied_date
    assert set(
        result.financial_summary.next_due_account_ids
    ) == {account_a.id, account_b.id}


def test_next_due_date_only_excludes_paid_not_overdue(
    db_session,
) -> None:
    """
    Documenta explicitamente uma propriedade real da formula congelada
    (Aggregation Contract Secao 4): next_due_date exclui apenas contas
    `paid`. Uma conta ja vencida (overdue) tem vencimento no passado,
    que e < qualquer vencimento futuro -- entao ela pode legitimamente
    ser o "next_due_date", mesmo isso soando contraintuitivo ("proximo
    vencimento" no passado). Nao e um bug: e o comportamento
    literalmente especificado (so `paid` e excluido). Descoberto
    durante o APPLY por um teste que presumia o contrario -- registrado
    aqui como propriedade intencional, nao corrigido silenciosamente.
    """
    email = "cliente.360.overdue-e-next@example.com"

    overdue = _make_account(
        db_session,
        email=email,
        vencimento=date.today() - timedelta(days=3),
        status="aberto",
    )
    _make_account(
        db_session,
        email=email,
        vencimento=date.today() + timedelta(days=30),
        status="aberto",
    )

    result = get_customer_context(
        db_session, requested_account=overdue
    )

    assert result.financial_summary.next_due_date == (
        overdue.vencimento
    )
    assert set(
        result.financial_summary.next_due_account_ids
    ) == {overdue.id}


def test_next_due_account_ids_empty_when_all_paid(
    db_session,
) -> None:
    account = _make_account(
        db_session,
        email=None,
        vencimento=date.today() - timedelta(days=1),
        status="pago",
    )

    result = get_customer_context(
        db_session, requested_account=account
    )

    assert result.financial_summary.next_due_date is None
    assert result.financial_summary.next_due_account_ids == ()


# ---------------------------------------------------------------------
# ClientBehaviorPatternSummary -- mapeamento exato do context_data real
# ---------------------------------------------------------------------


def test_behavior_pattern_maps_exactly_from_persisted_context_data(
    db_session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.360.padrao@example.com",
        vencimento=date.today() + timedelta(days=1),
    )

    context_data = {
        "email": account.email,
        "oldest_account_id": account.id,
        "ocorrencias_resolvidas": 5,
        "atraso_medio_dias": 3.4,
        "atraso_min_dias": 1,
        "atraso_max_dias": 9,
        "taxa_pagamento": 0.9,
        "confidence": 0.75,
    }

    memory = _insert_memory(
        db_session,
        account_id=account.id,
        memory_type="observation",
        memory_key="client_behavior_payment_pattern",
        context_data=context_data,
        confidence=0.75,
    )

    result = get_customer_context(
        db_session, requested_account=account
    )

    pattern = result.behavioral_intelligence.pattern
    assert pattern is not None
    assert pattern.oldest_account_id == account.id
    assert pattern.resolved_occurrences == 5
    assert pattern.average_late_days == 3.4
    assert pattern.min_late_days == 1
    assert pattern.max_late_days == 9
    assert pattern.payment_rate == 0.9
    assert pattern.confidence == 0.75
    assert pattern.recorded_at == memory.created_at


def test_behavioral_intelligence_absent_when_never_computed(
    db_session,
) -> None:
    account = _make_account(
        db_session,
        email="cliente.360.semdados@example.com",
        vencimento=date.today() + timedelta(days=1),
    )

    result = get_customer_context(
        db_session, requested_account=account
    )

    assert result.behavioral_intelligence.pattern is None
    assert result.behavioral_intelligence.classification is None
    assert (
        result.behavioral_intelligence.classification_status
        == "not_classified_yet"
    )


# ---------------------------------------------------------------------
# episodes -- composicao via get_outcome_episode, uma vez por conta
# ---------------------------------------------------------------------


def test_episodes_are_composed_once_per_account_via_outcome(
    db_session,
) -> None:
    email = "cliente.360.episodios@example.com"

    account_a = _make_account(
        db_session,
        email=email,
        vencimento=date.today() + timedelta(days=2),
    )
    account_b = _make_account(
        db_session,
        email=email,
        vencimento=date.today() + timedelta(days=8),
    )

    result = get_customer_context(
        db_session, requested_account=account_a
    )

    assert len(result.episodes) == 2

    episodes_by_account = {
        episode.episode.account_id: episode
        for episode in result.episodes
    }

    assert episodes_by_account[account_a.id].episode.due_date == (
        account_a.vencimento
    )
    assert episodes_by_account[account_b.id].episode.due_date == (
        account_b.vencimento
    )
    # nenhuma observacao/proposta/approval foi criada para essas
    # contas -- Outcome deve reportar ausencia, nao erro.
    assert episodes_by_account[account_a.id].detection.linkage == (
        "absent"
    )
