import inspect
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core import client_behavior_memory_maintenance as maintenance_module
from app.core.client_behavior_memory_maintenance import (
    apply_client_behavior_memory_pattern,
)
from app.core.client_behavior_memory_maintenance import (
    compute_client_behavior_pattern,
)
from app.core.client_behavior_memory_maintenance import (
    list_client_behavior_recalculation_candidate_emails,
)
from app.core.client_behavior_memory_maintenance import (
    recalculate_all_client_behavior_patterns,
)
from app.main import lifespan
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.memory import MemoryItem
from app.services.memory_service import MemoryService
from app.services.memory_service import RememberResult
from app.services.memory_service import SupersedeResult
from tests.payment_date_helpers import make_effective_payment


def _unique_email() -> str:
    return f"padrao.{uuid4().hex[:12]}@example.com"


def _make_account(
    db: Session,
    *,
    email: str,
    vencimento: date,
    status: str = "aberto",
) -> Account:
    account = Account(
        cliente="Cliente Teste Percepcao",
        email=email,
        whatsapp=None,
        valor=Decimal("100.00"),
        vencimento=vencimento,
        status=status,
    )
    db.add(account)
    db.flush()
    return account


def _make_paid_event(
    db: Session,
    *,
    account: Account,
    occurred_at: datetime,
    previous_status: str = "atrasado",
    with_effective_date: bool = True,
    paid_on: date | None = None,
) -> AccountEvent:
    # O evento e auditoria; a DATA OFICIAL e account_payments.paid_on.
    # Por padrao paid_on = data (UTC) do evento, como nos cenarios
    # originais; with_effective_date=False simula titulo antigo pago sem
    # data efetiva registrada.
    if with_effective_date:
        make_effective_payment(
            db,
            account=account,
            paid_on=paid_on or occurred_at.astimezone(timezone.utc).date(),
        )
    event = AccountEvent(
        account_id=account.id,
        event_type="status_changed",
        actor_type="user",
        actor_reference="user:1",
        previous_status=previous_status,
        new_status="pago",
        occurred_at=occurred_at,
    )
    db.add(event)
    db.flush()
    return event


def test_compute_returns_none_for_unknown_email(
    db_session: Session,
) -> None:
    result = compute_client_behavior_pattern(
        db_session,
        _unique_email(),
    )

    assert result is None


def test_compute_returns_none_below_min_occurrences(
    db_session: Session,
) -> None:
    email = _unique_email()

    # Setting default e' 3 -- dois ciclos resolvidos nao formam padrao.
    for offset in range(2):
        account = _make_account(
            db_session,
            email=email,
            vencimento=date(2026, 1, 1 + offset),
            status="pago",
        )
        _make_paid_event(
            db_session,
            account=account,
            occurred_at=datetime(
                2026, 1, 4 + offset,
                tzinfo=timezone.utc,
            ),
        )

    db_session.commit()

    result = compute_client_behavior_pattern(
        db_session,
        email,
    )

    assert result is None


def test_compute_returns_correct_pattern_for_three_resolved_cycles(
    db_session: Session,
) -> None:
    email = _unique_email()

    # Tres ciclos: atraso de 1, 3 e 5 dias -- media 3, min 1, max 5.
    atrasos = [1, 3, 5]

    for index, atraso in enumerate(atrasos):
        vencimento = date(2026, 2, 1 + index)
        account = _make_account(
            db_session,
            email=email,
            vencimento=vencimento,
            status="pago",
        )
        _make_paid_event(
            db_session,
            account=account,
            occurred_at=datetime(
                vencimento.year,
                vencimento.month,
                vencimento.day,
                tzinfo=timezone.utc,
            ) + timedelta(days=atraso),
        )

    db_session.commit()

    result = compute_client_behavior_pattern(
        db_session,
        email,
    )

    assert result is not None
    assert result.ocorrencias_resolvidas == 3
    assert result.atraso_medio_dias == 3.0
    assert result.atraso_min_dias == 1
    assert result.atraso_max_dias == 5
    assert result.taxa_pagamento == 1.0
    assert result.confidence == 1 - (1 / 3)
    assert len(result.cycles) == 3


def test_compute_ignores_extra_pago_events_and_uses_paid_on(
    db_session: Session,
) -> None:
    email = _unique_email()
    accounts = []

    for index in range(3):
        vencimento = date(2026, 3, 1 + index)
        account = _make_account(
            db_session,
            email=email,
            vencimento=vencimento,
            status="pago",
        )
        accounts.append((account, vencimento))
        _make_paid_event(
            db_session,
            account=account,
            occurred_at=datetime(
                vencimento.year,
                vencimento.month,
                vencimento.day,
                tzinfo=timezone.utc,
            ) + timedelta(days=2),
        )

    # Antes: o evento 'pago' mais recente vencia. Agora a data oficial e
    # account_payments.paid_on (unica por titulo); um evento 'pago'
    # extra, so de auditoria, NAO altera o atraso calculado.
    corrected_account, corrected_vencimento = accounts[0]
    _make_paid_event(
        db_session,
        account=corrected_account,
        occurred_at=datetime(
            corrected_vencimento.year,
            corrected_vencimento.month,
            corrected_vencimento.day,
            tzinfo=timezone.utc,
        ) + timedelta(days=9),
        previous_status="pago",
        with_effective_date=False,
    )

    db_session.commit()

    result = compute_client_behavior_pattern(
        db_session,
        email,
    )

    assert result is not None

    corrected_cycle = next(
        cycle
        for cycle in result.cycles
        if cycle.account_id == corrected_account.id
    )

    assert corrected_cycle.atraso_dias == 2
    assert corrected_cycle.data_pagamento_origem == "efetiva"


def test_compute_excludes_unresolved_cycles_from_occurrences(
    db_session: Session,
) -> None:
    email = _unique_email()

    for index in range(3):
        vencimento = date(2026, 4, 1 + index)
        account = _make_account(
            db_session,
            email=email,
            vencimento=vencimento,
            status="pago",
        )
        _make_paid_event(
            db_session,
            account=account,
            occurred_at=datetime(
                vencimento.year,
                vencimento.month,
                vencimento.day,
                tzinfo=timezone.utc,
            ) + timedelta(days=2),
        )

    # Uma quarta conta do mesmo email, ainda em aberto -- conta no
    # denominador da taxa de pagamento, mas nao nas ocorrencias resolvidas.
    _make_account(
        db_session,
        email=email,
        vencimento=date(2026, 4, 20),
        status="aberto",
    )

    db_session.commit()

    result = compute_client_behavior_pattern(
        db_session,
        email,
    )

    assert result is not None
    assert result.ocorrencias_resolvidas == 3
    assert result.taxa_pagamento == 3 / 4


def test_list_candidate_emails_includes_email_with_new_paid_event(
    db_session: Session,
) -> None:
    email = _unique_email()
    account = _make_account(
        db_session,
        email=email,
        vencimento=date(2026, 5, 1),
        status="pago",
    )
    _make_paid_event(
        db_session,
        account=account,
        occurred_at=datetime(
            2026, 5, 3,
            tzinfo=timezone.utc,
        ),
    )
    db_session.commit()

    candidates = list_client_behavior_recalculation_candidate_emails(
        db_session,
    )

    assert email in candidates


def test_list_candidate_emails_excludes_email_already_up_to_date(
    db_session: Session,
) -> None:
    email = _unique_email()
    account = _make_account(
        db_session,
        email=email,
        vencimento=date(2026, 6, 1),
        status="pago",
    )
    paid_event = _make_paid_event(
        db_session,
        account=account,
        occurred_at=datetime(
            2026, 6, 3,
            tzinfo=timezone.utc,
        ),
    )
    db_session.commit()

    memory = MemoryItem(
        memory_type="observation",
        title=f"Padrao de pagamento - {email}",
        content="Atraso medio: 2 dias.",
        scope_type="account",
        account_id=account.id,
        confidence=Decimal("0.670"),
        status="active",
        valid_from=paid_event.occurred_at
        + timedelta(minutes=1),
        source_type="derived",
        source_reference=f"client_behavior:{email}",
        # Memoria ja gravada com a fonte oficial de data (marcador).
        context_data={"ocorrencias_sem_data_efetiva": 0},
    )
    db_session.add(memory)
    db_session.commit()

    candidates = list_client_behavior_recalculation_candidate_emails(
        db_session,
    )

    assert email not in candidates


def test_list_candidate_emails_excludes_memory_without_effective_date_marker(
    db_session: Session,
) -> None:
    # Memoria de comportamento gravada ANTES da fonte oficial (sem o
    # marcador) NAO vira candidata so por isso: e recalculada no proximo
    # pagamento do cliente (a memoria de comportamento pode nao gravar
    # nada abaixo do minimo, entao o marcador causaria loop).
    email = _unique_email()
    account = _make_account(
        db_session,
        email=email,
        vencimento=date(2026, 6, 1),
        status="pago",
    )
    paid_event = _make_paid_event(
        db_session,
        account=account,
        occurred_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
    )
    db_session.commit()

    db_session.add(
        MemoryItem(
            memory_type="observation",
            title=f"Padrao de pagamento - {email}",
            content="Atraso medio: 2 dias.",
            scope_type="account",
            account_id=account.id,
            confidence=Decimal("0.670"),
            status="active",
            valid_from=paid_event.occurred_at + timedelta(minutes=1),
            source_type="derived",
            source_reference=f"client_behavior:{email}",
            memory_key="client_behavior_payment_pattern",
            context_data={"atraso_medio_dias": 2},
        )
    )
    db_session.commit()

    candidates = list_client_behavior_recalculation_candidate_emails(
        db_session,
    )

    assert email not in candidates


def test_compute_uses_paid_on_not_registration_event_date(
    db_session: Session,
) -> None:
    # Pagou em 10/11, registrado (evento) em 14/11: o atraso usa 10/11;
    # o evento mantem 14/11 para auditoria.
    email = _unique_email()

    for index in range(3):
        vencimento = date(2026, 11, 5 + index)
        account = _make_account(
            db_session,
            email=email,
            vencimento=vencimento,
            status="pago",
        )
        _make_paid_event(
            db_session,
            account=account,
            occurred_at=datetime(2026, 11, 14, 12, tzinfo=timezone.utc),
            paid_on=date(2026, 11, 10),
        )

    db_session.commit()

    result = compute_client_behavior_pattern(db_session, email)

    assert result is not None
    assert sorted(c.atraso_dias for c in result.cycles) == [3, 4, 5]
    assert {c.resolved_at for c in result.cycles} == {date(2026, 11, 10)}
    assert result.atraso_medio_dias == 4.0
    assert result.ocorrencias_sem_data_efetiva == 0

    events = (
        db_session.query(AccountEvent)
        .filter(AccountEvent.new_status == "pago")
        .filter(
            AccountEvent.account_id.in_(
                [c.account_id for c in result.cycles]
            )
        )
        .all()
    )
    assert {e.occurred_at.date() for e in events} == {date(2026, 11, 14)}


def test_compute_counts_paid_without_effective_date_outside_averages(
    db_session: Session,
) -> None:
    email = _unique_email()

    for index in range(3):
        vencimento = date(2026, 2, 1 + index)
        account = _make_account(
            db_session,
            email=email,
            vencimento=vencimento,
            status="pago",
        )
        _make_paid_event(
            db_session,
            account=account,
            occurred_at=datetime(
                2026, 2, 1 + index, tzinfo=timezone.utc
            ) + timedelta(days=2),
        )

    # Titulo antigo: evento 'pago' sem account_payments. Se a data do
    # evento fosse usada, o atraso maximo seria 40, nao 2.
    legacy = _make_account(
        db_session,
        email=email,
        vencimento=date(2026, 1, 1),
        status="pago",
    )
    _make_paid_event(
        db_session,
        account=legacy,
        occurred_at=datetime(2026, 2, 10, tzinfo=timezone.utc),
        with_effective_date=False,
    )
    db_session.commit()

    result = compute_client_behavior_pattern(db_session, email)

    assert result is not None
    assert result.ocorrencias_resolvidas == 3
    assert result.ocorrencias_sem_data_efetiva == 1
    assert result.atraso_max_dias == 2
    assert result.atraso_medio_dias == 2.0
    assert legacy.id not in {c.account_id for c in result.cycles}
    # titulo pago sem data continua contando como pago na taxa
    assert result.taxa_pagamento == 1.0

    context = apply_client_behavior_memory_pattern(
        db_session, MemoryService(db_session), email
    ).memory.context_data
    assert context["ocorrencias_sem_data_efetiva"] == 1
    assert context["fonte_data_pagamento"] == "account_payments.paid_on"


def _make_paid_cycles(
    db_session: Session,
    *,
    email: str,
    month: int,
    count: int,
    atraso_dias: int = 2,
) -> list[Account]:
    accounts = []

    for index in range(count):
        vencimento = date(2026, month, 1 + index)
        account = _make_account(
            db_session,
            email=email,
            vencimento=vencimento,
            status="pago",
        )
        _make_paid_event(
            db_session,
            account=account,
            occurred_at=datetime(
                vencimento.year,
                vencimento.month,
                vencimento.day,
                tzinfo=timezone.utc,
            ) + timedelta(days=atraso_dias),
        )
        accounts.append(account)

    return accounts


def test_apply_returns_none_below_min_occurrences(
    db_session: Session,
) -> None:
    email = _unique_email()
    _make_paid_cycles(db_session, email=email, month=7, count=2)
    db_session.commit()

    memory_service = MemoryService(db_session)

    result = apply_client_behavior_memory_pattern(
        db_session,
        memory_service,
        email,
    )

    assert result is None
    assert (
        db_session.query(MemoryItem)
        .filter(MemoryItem.source_reference == f"client_behavior:{email}")
        .count()
        == 0
    )


def test_apply_creates_memory_first_time(
    db_session: Session,
) -> None:
    email = _unique_email()
    accounts = _make_paid_cycles(
        db_session, email=email, month=8, count=3, atraso_dias=2,
    )
    db_session.commit()

    memory_service = MemoryService(db_session)

    result = apply_client_behavior_memory_pattern(
        db_session,
        memory_service,
        email,
    )

    assert isinstance(result, RememberResult)
    assert result.created is True
    assert result.duplicate is False
    assert result.memory.memory_type == "observation"
    assert result.memory.scope_type == "account"
    assert result.memory.account_id == accounts[0].id
    assert result.memory.status == "active"
    assert result.memory.source_reference == f"client_behavior:{email}"
    assert result.memory.context_data["email"] == email
    assert result.memory.context_data["ocorrencias_resolvidas"] == 3
    assert len(result.evidence) == 3


def test_apply_supersedes_on_recalculation(
    db_session: Session,
) -> None:
    email = _unique_email()
    _make_paid_cycles(
        db_session, email=email, month=9, count=3, atraso_dias=1,
    )
    db_session.commit()

    memory_service = MemoryService(db_session)

    first = apply_client_behavior_memory_pattern(
        db_session,
        memory_service,
        email,
    )
    assert isinstance(first, RememberResult)

    # Um quarto ciclo pago, com atraso maior -- muda a media.
    _make_paid_cycles(
        db_session, email=email, month=10, count=1, atraso_dias=9,
    )
    db_session.commit()

    second = apply_client_behavior_memory_pattern(
        db_session,
        memory_service,
        email,
    )

    assert isinstance(second, SupersedeResult)
    assert second.previous.id == first.memory.id
    assert second.previous.status == "superseded"
    assert second.replacement.status == "active"
    assert second.replacement.context_data["ocorrencias_resolvidas"] == 4
    assert second.replacement.confidence != first.memory.confidence

    active_memories = (
        db_session.query(MemoryItem)
        .filter(
            MemoryItem.account_id == first.memory.account_id,
            MemoryItem.memory_key == "client_behavior_payment_pattern",
            MemoryItem.status == "active",
        )
        .count()
    )
    assert active_memories == 1


def test_recalculate_all_processes_every_candidate(
    db_session: Session,
) -> None:
    ready_email = _unique_email()
    _make_paid_cycles(db_session, email=ready_email, month=11, count=3)

    not_ready_email = _unique_email()
    _make_paid_cycles(db_session, email=not_ready_email, month=12, count=1)

    db_session.commit()

    memory_service = MemoryService(db_session)

    summary = recalculate_all_client_behavior_patterns(
        db_session,
        memory_service,
    )

    assert summary.candidates == 2
    assert summary.created == 1
    assert summary.skipped == 1
    assert summary.superseded == 0


def test_recalculate_all_isolates_failures_per_email(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ok_email = _unique_email()
    _make_paid_cycles(db_session, email=ok_email, month=1, count=3)

    failing_email = _unique_email()
    _make_paid_cycles(db_session, email=failing_email, month=2, count=3)

    db_session.commit()

    real_apply = maintenance_module.apply_client_behavior_memory_pattern

    def fake_apply(db, memory_service, email):
        if email == failing_email:
            raise RuntimeError("falha simulada")
        return real_apply(db, memory_service, email)

    monkeypatch.setattr(
        maintenance_module,
        "apply_client_behavior_memory_pattern",
        fake_apply,
    )

    memory_service = MemoryService(db_session)

    summary = maintenance_module.recalculate_all_client_behavior_patterns(
        db_session,
        memory_service,
    )

    assert summary.candidates == 2
    assert summary.created == 1
    assert summary.skipped == 1
    assert summary.superseded == 0

    # A conta que falhou nao deve ter deixado nenhuma memoria orfa.
    assert (
        db_session.query(MemoryItem)
        .filter(
            MemoryItem.source_reference
            == f"client_behavior:{failing_email}"
        )
        .count()
        == 0
    )


def test_recalculation_wrappers_and_lifespan_wiring_source_contract() -> None:
    maintenance_source = inspect.getsource(maintenance_module)
    lifespan_source = inspect.getsource(lifespan)

    assert "def run_client_behavior_memory_recalculation(" in (
        maintenance_source
    )
    assert "async def run_client_behavior_memory_recalculation_async(" in (
        maintenance_source
    )
    assert "async def client_behavior_memory_maintenance_loop(" in (
        maintenance_source
    )
    assert (
        "settings.client_behavior_recalculation_interval_seconds"
        in maintenance_source
    )

    assert (
        "run_client_behavior_memory_recalculation_async" in lifespan_source
    )
    assert "client_behavior_memory_maintenance_loop" in lifespan_source
