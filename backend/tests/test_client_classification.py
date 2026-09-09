from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.client_classification import (
    LABEL_ATRASO_RECORRENTE,
)
from app.core.client_classification import (
    LABEL_INSUFFICIENT_DATA,
)
from app.core.client_classification import (
    LABEL_PAGAMENTO_REGULAR,
)
from app.core.client_classification import (
    MEMORY_KEY,
)
from app.core.client_classification import (
    REASON_BELOW_MINIMUM,
)
from app.core.client_classification import (
    apply_client_classification,
)
from app.core.client_classification import (
    compute_client_classification,
)
from app.core.client_classification import (
    recalculate_all_client_classifications,
)
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.memory import MemoryItem
from app.services.memory_service import MemoryService
from app.services.memory_service import RememberResult
from app.services.memory_service import SupersedeResult


def _unique_email() -> str:
    return f"classificacao.{uuid4().hex[:12]}@example.com"


def _make_account(
    db: Session,
    *,
    email: str,
    vencimento: date,
    status: str = "pago",
) -> Account:
    account = Account(
        cliente="Cliente Teste Classificacao",
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
) -> AccountEvent:
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


def _make_resolved_cycle(
    db: Session,
    *,
    email: str,
    vencimento: date,
    atraso_dias: int,
) -> Account:
    account = _make_account(db, email=email, vencimento=vencimento)
    _make_paid_event(
        db,
        account=account,
        occurred_at=datetime(
            vencimento.year,
            vencimento.month,
            vencimento.day,
            tzinfo=timezone.utc,
        )
        + timedelta(days=atraso_dias),
    )
    return account


def test_compute_returns_none_for_unknown_email(
    db_session: Session,
) -> None:
    result = compute_client_classification(
        db_session,
        _unique_email(),
    )

    assert result is None


def test_compute_classification_insufficient_data_below_minimum(
    db_session: Session,
) -> None:
    email = _unique_email()

    # Duas ocorrencias resolvidas -- abaixo do minimo (3).
    for index, atraso in enumerate([5, 0]):
        _make_resolved_cycle(
            db_session,
            email=email,
            vencimento=date(2026, 1, 1 + index),
            atraso_dias=atraso,
        )

    db_session.commit()

    result = compute_client_classification(db_session, email)

    assert result is not None
    assert result.label == LABEL_INSUFFICIENT_DATA
    assert result.ocorrencias_resolvidas == 2
    assert result.ciclos_em_atraso is None
    assert result.proporcao_atraso is None
    assert result.confidence == 0.0
    assert result.reason == REASON_BELOW_MINIMUM
    assert result.period_start is not None
    assert result.period_end is not None
    assert len(result.cycles) == 2


def test_compute_classification_pagamento_regular_below_threshold(
    db_session: Session,
) -> None:
    email = _unique_email()

    # 4 ciclos, 1 atrasado (25%) -- abaixo do limiar de 50%.
    atrasos = [3, 0, 0, 0]

    for index, atraso in enumerate(atrasos):
        _make_resolved_cycle(
            db_session,
            email=email,
            vencimento=date(2026, 2, 1 + index),
            atraso_dias=atraso,
        )

    db_session.commit()

    result = compute_client_classification(db_session, email)

    assert result is not None
    assert result.label == LABEL_PAGAMENTO_REGULAR
    assert result.ocorrencias_resolvidas == 4
    assert result.ciclos_em_atraso == 1
    assert result.proporcao_atraso == 0.25
    assert result.reason is None


def test_compute_classification_atraso_recorrente_at_exact_50_percent(
    db_session: Session,
) -> None:
    email = _unique_email()

    # 4 ciclos, 2 atrasados (exatamente 50%) -- deve classificar como
    # ATRASO_RECORRENTE (limiar e >=, nao >).
    atrasos = [1, 2, 0, 0]

    for index, atraso in enumerate(atrasos):
        _make_resolved_cycle(
            db_session,
            email=email,
            vencimento=date(2026, 3, 1 + index),
            atraso_dias=atraso,
        )

    db_session.commit()

    result = compute_client_classification(db_session, email)

    assert result is not None
    assert result.label == LABEL_ATRASO_RECORRENTE
    assert result.ocorrencias_resolvidas == 4
    assert result.ciclos_em_atraso == 2
    assert result.proporcao_atraso == 0.5


def test_compute_classification_atraso_recorrente_majority(
    db_session: Session,
) -> None:
    email = _unique_email()

    # 3 ciclos, 2 atrasados (66.7%).
    atrasos = [1, 3, 0]

    for index, atraso in enumerate(atrasos):
        _make_resolved_cycle(
            db_session,
            email=email,
            vencimento=date(2026, 4, 1 + index),
            atraso_dias=atraso,
        )

    db_session.commit()

    result = compute_client_classification(db_session, email)

    assert result is not None
    assert result.label == LABEL_ATRASO_RECORRENTE
    assert result.ciclos_em_atraso == 2
    assert result.ocorrencias_resolvidas == 3


def test_compute_classification_zero_dias_de_atraso_nao_conta(
    db_session: Session,
) -> None:
    email = _unique_email()

    # atraso_dias == 0 (pago exatamente no vencimento) nao conta como
    # ciclo em atraso -- so atraso_dias > 0 conta.
    atrasos = [0, 0, 0]

    for index, atraso in enumerate(atrasos):
        _make_resolved_cycle(
            db_session,
            email=email,
            vencimento=date(2026, 5, 1 + index),
            atraso_dias=atraso,
        )

    db_session.commit()

    result = compute_client_classification(db_session, email)

    assert result is not None
    assert result.label == LABEL_PAGAMENTO_REGULAR
    assert result.ciclos_em_atraso == 0
    assert result.proporcao_atraso == 0.0


def test_apply_creates_memory_on_first_call(
    db_session: Session,
) -> None:
    email = _unique_email()

    for index, atraso in enumerate([1, 2, 0, 0]):
        _make_resolved_cycle(
            db_session,
            email=email,
            vencimento=date(2026, 6, 1 + index),
            atraso_dias=atraso,
        )

    db_session.commit()

    memory_service = MemoryService(db_session)
    result = apply_client_classification(
        db_session, memory_service, email
    )

    assert isinstance(result, RememberResult)
    assert result.created is True
    assert result.memory.memory_type == "decision"
    assert result.memory.memory_key == MEMORY_KEY
    assert result.memory.context_data["label"] == (
        LABEL_ATRASO_RECORRENTE
    )
    assert result.memory.context_data["reason"] is None
    assert (
        result.memory.context_data["analysis_scope"]
        == "all_available_history"
    )
    assert len(result.evidence) == 4


def test_apply_supersedes_on_recalculation(
    db_session: Session,
) -> None:
    email = _unique_email()

    # Estado inicial: 1 de 3 ciclos atrasado (33,3%) -> PAGAMENTO_REGULAR.
    for index, atraso in enumerate([1, 0, 0]):
        _make_resolved_cycle(
            db_session,
            email=email,
            vencimento=date(2026, 7, 1 + index),
            atraso_dias=atraso,
        )

    db_session.commit()

    memory_service = MemoryService(db_session)
    first = apply_client_classification(
        db_session, memory_service, email
    )
    assert isinstance(first, RememberResult)
    assert first.memory.context_data["label"] == (
        LABEL_PAGAMENTO_REGULAR
    )

    # Novo ciclo com atraso, virando a proporcao para 50% (2 de 4).
    _make_resolved_cycle(
        db_session,
        email=email,
        vencimento=date(2026, 7, 10),
        atraso_dias=5,
    )
    db_session.commit()

    second = apply_client_classification(
        db_session, memory_service, email
    )

    assert isinstance(second, SupersedeResult)
    assert second.previous.id == first.memory.id
    assert second.previous.status == "superseded"
    assert second.replacement.status == "active"
    assert second.replacement.context_data["label"] == (
        LABEL_ATRASO_RECORRENTE
    )


def test_apply_returns_none_for_unknown_email(
    db_session: Session,
) -> None:
    memory_service = MemoryService(db_session)

    result = apply_client_classification(
        db_session, memory_service, _unique_email()
    )

    assert result is None


def test_recalculate_all_client_classifications_summary(
    db_session: Session,
) -> None:
    email = _unique_email()

    for index, atraso in enumerate([1, 2, 0, 0]):
        _make_resolved_cycle(
            db_session,
            email=email,
            vencimento=date(2026, 8, 1 + index),
            atraso_dias=atraso,
        )

    db_session.commit()

    memory_service = MemoryService(db_session)
    summary = recalculate_all_client_classifications(
        db_session, memory_service
    )

    assert summary.candidates >= 1
    assert summary.created >= 1

    # Rodar de novo sem nenhum evento novo nao deve recriar/superseder
    # nada para esse email (mesmo criterio de candidato da Fatia 1).
    summary_again = recalculate_all_client_classifications(
        db_session, memory_service
    )
    assert summary_again.candidates == 0
