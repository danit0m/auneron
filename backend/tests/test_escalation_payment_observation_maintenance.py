"""
VALUE-3.3B -- worker de auto-wiring de observed_fact (codigo real).

Prova o worker de producao
(app.core.escalation_payment_observation_maintenance), nunca a funcao
de referencia dos arquivos experimentais do VALUE-3.3A/3.3B. Usa como
checklist de cobertura os 10 cenarios do 3.3A + a corrida do 3.3B +
os tres casos de Settings (datetime ausente / ISO-8601 valido /
invalido) exigidos pelo freeze.

Isolamento: `auneron_test` e compartilhado e persistente entre testes,
entao cada teste que afirma contagens exatas usa um activation_floor
unico, monotonicamente crescente e muito acima de qualquer evento de
outros testes (`_unique_floor`). Testes de fronteira temporal que
precisam de um floor proximo ao WorkItem afirmam apenas sobre as
proprias entidades, nunca sobre contagens globais.
"""

from __future__ import annotations

import ast
import inspect
import re
import threading
import time
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.orm import Session

from app import main as main_module
from app.core.authentication import hash_password
from app.core.config import Settings
from app.core.config import settings as global_settings
from app.core.escalation_payment_observation_maintenance import (
    run_escalation_payment_observation_recovery,
)
from app.database.database import SessionLocal
from app.database.database import engine
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.escalation_observation import EscalationObservation
from app.models.user import User
from app.models.work import WorkItem
from app.services.escalation_observation_service import (
    EscalationObservationService,
)
from app.services.human_escalation_materialization_service import (
    HumanEscalationMaterializationService,
)
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService

from evidence_provenance_helpers import make_service


TEST_URL = (
    "postgresql+psycopg://"
    "auneron:test_password"
    "@localhost:5432/auneron_test"
)

_COUNTER = {"n": 0}


@pytest.fixture(autouse=True)
def _valid_provenance(monkeypatch: pytest.MonkeyPatch):
    """VALUE-3.4D-2b: o worker so materializa com proveniencia valida.
    Estes testes (VALUE-3.3B) provam a semantica de coleta; a proveniencia
    e injetada com fontes VALIDAS (o fail-closed e provado em
    test_evidence_provenance_worker.py). Sem `conftest`."""
    import app.core.escalation_payment_observation_maintenance as worker

    worker.reset_blocked_log_state()
    monkeypatch.setattr(
        worker,
        "_provenance_service_for",
        lambda session_factory: make_service(session_factory),
    )
    yield
    worker.reset_blocked_log_state()


def _unique_floor() -> datetime:
    base = datetime(3000, 1, 1, tzinfo=timezone.utc)
    return base + timedelta(microseconds=time.time_ns() // 1000)


def _next_email(prefix: str) -> str:
    _COUNTER["n"] += 1
    return f"{prefix}-{time.time_ns()}-{_COUNTER['n']}@example.com"


def _account(
    db_session: Session, *, vencimento: date
) -> Account:
    account = Account(
        cliente="Cliente VALUE-3.3B Worker",
        email=_next_email("worker-account"),
        whatsapp=None,
        valor=500,
        vencimento=vencimento,
        status="atrasado",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _actor_user(db_session: Session) -> User:
    user = User(
        name="VALUE-3.3B Worker Actor",
        email=_next_email("worker-actor"),
        password_hash=hash_password("not-used-Aa1!"),
        role="administrator",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _work_actor(user: User) -> WorkActor:
    return WorkActor(
        actor_type="user",
        actor_reference=f"user:{user.id}",
        actor_user_id=user.id,
    )


def _setup_escalation(
    db_session: Session,
) -> tuple[Account, User, WorkItem]:
    due_date = date.today() - timedelta(days=10)
    account = _account(db_session, vencimento=due_date)
    actor_user = _actor_user(db_session)
    result = HumanEscalationMaterializationService(
        db_session
    ).materialize(
        account=account,
        due_date=due_date,
        actor=_work_actor(actor_user),
    )
    return account, actor_user, result.work_item


def _payment_event(
    db_session: Session,
    *,
    account: Account,
    occurred_at: datetime,
) -> AccountEvent:
    event = AccountEvent(
        account_id=account.id,
        event_type="status_changed",
        actor_type="system",
        actor_reference="test:value33b-worker",
        previous_status="atrasado",
        new_status="pago",
        occurred_at=occurred_at,
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)
    return event


def _observations(
    db_session: Session, work_item_id: int
) -> list[EscalationObservation]:
    db_session.expire_all()
    return (
        db_session.query(EscalationObservation)
        .filter(
            EscalationObservation.escalation_work_item_id
            == work_item_id
        )
        .order_by(EscalationObservation.id)
        .all()
    )


# ---------------------------------------------------------------------
# 1 -- floor ausente = recurso DESABILITADO (nunca "sem limite")
# ---------------------------------------------------------------------


def test_missing_floor_means_disabled_not_unbounded(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        global_settings,
        "escalation_payment_observation_activation_floor",
        None,
    )
    account, _, work_item = _setup_escalation(db_session)
    _payment_event(
        db_session,
        account=account,
        occurred_at=work_item.created_at + timedelta(hours=1),
    )

    summary = run_escalation_payment_observation_recovery()

    assert summary.disabled is True
    assert summary.candidate_count == 0
    assert summary.created_count == 0
    assert _observations(db_session, work_item.id) == []


# ---------------------------------------------------------------------
# 2 -- caminho feliz (floor unico => contagens exatas)
# ---------------------------------------------------------------------


def test_happy_path_creates_observed_fact(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    account, _, work_item = _setup_escalation(db_session)
    payment = _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(microseconds=1),
    )

    summary = run_escalation_payment_observation_recovery(
        activation_floor=floor
    )

    assert summary.disabled is False
    assert summary.candidate_count == 1
    assert summary.created_count == 1
    assert summary.failure_count == 0
    rows = _observations(db_session, work_item.id)
    assert len(rows) == 1
    assert rows[0].observation_type == "observed_fact"
    assert rows[0].linked_account_event_id == payment.id
    assert rows[0].observed_at == payment.occurred_at


# ---------------------------------------------------------------------
# 3 -- pagamento anterior ao escalonamento: abstencao
# ---------------------------------------------------------------------


def test_payment_before_escalation_abstains(
    db_session: Session,
) -> None:
    account, _, work_item = _setup_escalation(db_session)
    payment = _payment_event(
        db_session,
        account=account,
        occurred_at=work_item.created_at - timedelta(hours=1),
    )

    summary = run_escalation_payment_observation_recovery(
        activation_floor=payment.occurred_at
        - timedelta(minutes=1)
    )

    assert summary.failure_count == 0
    assert _observations(db_session, work_item.id) == []


# ---------------------------------------------------------------------
# 4 -- timestamp exatamente igual: aceito (so "<" rejeita)
# ---------------------------------------------------------------------


def test_exact_equal_timestamp_is_accepted(
    db_session: Session,
) -> None:
    account, _, work_item = _setup_escalation(db_session)
    payment = _payment_event(
        db_session,
        account=account,
        occurred_at=work_item.created_at,
    )

    summary = run_escalation_payment_observation_recovery(
        activation_floor=work_item.created_at
        - timedelta(minutes=1)
    )

    assert summary.failure_count == 0
    rows = _observations(db_session, work_item.id)
    assert len(rows) == 1
    assert rows[0].linked_account_event_id == payment.id


# ---------------------------------------------------------------------
# 5 -- mudanca de vencimento: abstencao mesmo com WorkItem real
# ---------------------------------------------------------------------


def test_vencimento_change_abstains(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    account, _, work_item = _setup_escalation(db_session)
    account.vencimento = date.today() - timedelta(days=3)
    db_session.commit()
    _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(microseconds=1),
    )

    summary = run_escalation_payment_observation_recovery(
        activation_floor=floor
    )

    assert summary.created_count == 0
    assert summary.abstained_count == 1
    assert summary.failure_count == 0
    assert _observations(db_session, work_item.id) == []


# ---------------------------------------------------------------------
# 6 -- replay: nunca duplica (2o ciclo nao reseleciona o evento)
# ---------------------------------------------------------------------


def test_replay_never_duplicates(db_session: Session) -> None:
    floor = _unique_floor()
    account, _, work_item = _setup_escalation(db_session)
    _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(microseconds=1),
    )

    first = run_escalation_payment_observation_recovery(
        activation_floor=floor
    )
    second = run_escalation_payment_observation_recovery(
        activation_floor=floor
    )

    assert first.created_count == 1
    assert second.candidate_count == 0
    assert second.created_count == 0
    assert len(_observations(db_session, work_item.id)) == 1


# ---------------------------------------------------------------------
# 7 -- human_assessment coexistente
# ---------------------------------------------------------------------


def test_human_assessment_coexists(db_session: Session) -> None:
    floor = _unique_floor()
    account, actor_user, work_item = _setup_escalation(db_session)
    EscalationObservationService(db_session).record_human_assessment(
        escalation_work_item=work_item,
        assessment_code="payment_promised",
        actor=_work_actor(actor_user),
        declared_by_role="administrator",
        idempotency_key=f"worker-coexist:{time.time_ns()}",
    )
    _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(microseconds=1),
    )

    summary = run_escalation_payment_observation_recovery(
        activation_floor=floor
    )

    assert summary.created_count == 1
    types = sorted(
        row.observation_type
        for row in _observations(db_session, work_item.id)
    )
    assert types == ["human_assessment", "observed_fact"]


# ---------------------------------------------------------------------
# 8 -- dois episodios isolados
# ---------------------------------------------------------------------


def test_two_episodes_are_isolated(db_session: Session) -> None:
    floor = _unique_floor()
    account_a, _, work_item_a = _setup_escalation(db_session)
    account_b, _, work_item_b = _setup_escalation(db_session)
    payment_a = _payment_event(
        db_session,
        account=account_a,
        occurred_at=floor + timedelta(microseconds=1),
    )
    payment_b = _payment_event(
        db_session,
        account=account_b,
        occurred_at=floor + timedelta(microseconds=2),
    )

    summary = run_escalation_payment_observation_recovery(
        activation_floor=floor
    )

    assert summary.created_count == 2
    rows_a = _observations(db_session, work_item_a.id)
    rows_b = _observations(db_session, work_item_b.id)
    assert [r.linked_account_event_id for r in rows_a] == [
        payment_a.id
    ]
    assert [r.linked_account_event_id for r in rows_b] == [
        payment_b.id
    ]


# ---------------------------------------------------------------------
# 9 -- conta nunca escalonada: nem e candidata (pre-filtro EXISTS)
# ---------------------------------------------------------------------


def test_account_without_escalation_is_not_a_candidate(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    account = _account(
        db_session, vencimento=date.today() - timedelta(days=10)
    )
    _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(microseconds=1),
    )

    summary = run_escalation_payment_observation_recovery(
        activation_floor=floor
    )

    assert summary.candidate_count == 0
    assert summary.created_count == 0
    assert summary.failure_count == 0


# ---------------------------------------------------------------------
# 10 -- falha em um candidato nao interrompe os demais
# ---------------------------------------------------------------------


def test_failure_in_one_candidate_does_not_block_others(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    floor = _unique_floor()
    account_bad, _, work_item_bad = _setup_escalation(db_session)
    account_ok, _, work_item_ok = _setup_escalation(db_session)
    payment_bad = _payment_event(
        db_session,
        account=account_bad,
        occurred_at=floor + timedelta(microseconds=1),
    )
    _payment_event(
        db_session,
        account=account_ok,
        occurred_at=floor + timedelta(microseconds=2),
    )

    original = EscalationObservationService.record_observed_fact

    def flaky(self, *, escalation_work_item, account_event, **kw):
        if account_event.id == payment_bad.id:
            raise RuntimeError("falha injetada")
        return original(
            self,
            escalation_work_item=escalation_work_item,
            account_event=account_event,
            **kw,
        )

    monkeypatch.setattr(
        EscalationObservationService, "record_observed_fact", flaky
    )

    summary = run_escalation_payment_observation_recovery(
        activation_floor=floor
    )

    assert summary.failure_count == 1
    assert summary.created_count == 1
    assert _observations(db_session, work_item_bad.id) == []
    assert len(_observations(db_session, work_item_ok.id)) == 1


# ---------------------------------------------------------------------
# 11 -- fronteira do floor: < ignora, == e > elegiveis
# ---------------------------------------------------------------------


def test_floor_boundary_is_closed_on_the_inside(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    account_before, _, work_item_before = _setup_escalation(db_session)
    account_at, _, work_item_at = _setup_escalation(db_session)
    account_after, _, work_item_after = _setup_escalation(db_session)
    _payment_event(
        db_session,
        account=account_before,
        occurred_at=floor - timedelta(microseconds=1),
    )
    _payment_event(
        db_session, account=account_at, occurred_at=floor
    )
    _payment_event(
        db_session,
        account=account_after,
        occurred_at=floor + timedelta(microseconds=1),
    )

    summary = run_escalation_payment_observation_recovery(
        activation_floor=floor
    )

    assert summary.created_count == 2
    assert _observations(db_session, work_item_before.id) == []
    assert len(_observations(db_session, work_item_at.id)) == 1
    assert len(_observations(db_session, work_item_after.id)) == 1


# ---------------------------------------------------------------------
# 12 -- WorkItem terminal continua elegivel
# ---------------------------------------------------------------------


def test_terminal_work_item_is_still_eligible(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    account, actor_user, work_item = _setup_escalation(db_session)
    WorkManagerService(db_session).transition_status(
        work_item.id,
        expected_version=work_item.version,
        actor=_work_actor(actor_user),
        status="cancelled",
        reason="teste de WorkItem terminal",
    )
    _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(microseconds=1),
    )

    summary = run_escalation_payment_observation_recovery(
        activation_floor=floor
    )

    assert summary.created_count == 1
    assert len(_observations(db_session, work_item.id)) == 1


# ---------------------------------------------------------------------
# 13 -- abstencoes permanentes nao bloqueiam candidatos mais novos
#       (varredura por keyset; sem ela, `limit` abstenções ocupariam
#       o lote para sempre)
# ---------------------------------------------------------------------


def test_permanent_abstentions_do_not_starve_newer_candidates(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    for offset in (1, 2, 3):
        account, _, _ = _setup_escalation(db_session)
        account.vencimento = date.today() - timedelta(days=1)
        db_session.commit()
        _payment_event(
            db_session,
            account=account,
            occurred_at=floor + timedelta(microseconds=offset),
        )
    valid_account, _, valid_work_item = _setup_escalation(
        db_session
    )
    _payment_event(
        db_session,
        account=valid_account,
        occurred_at=floor + timedelta(microseconds=10),
    )

    summary = run_escalation_payment_observation_recovery(
        limit=2, activation_floor=floor
    )

    assert summary.candidate_count == 4
    assert summary.abstained_count == 3
    assert summary.created_count == 1
    assert len(_observations(db_session, valid_work_item.id)) == 1


# ---------------------------------------------------------------------
# 13b -- Amendment V1: keyset cronologico (occurred_at, id)
# ---------------------------------------------------------------------


def _record_processing_order(
    monkeypatch: pytest.MonkeyPatch,
) -> list[int]:
    order: list[int] = []
    original = EscalationObservationService.record_observed_fact

    def spy(self, *, escalation_work_item, account_event, **kw):
        order.append(account_event.id)
        return original(
            self,
            escalation_work_item=escalation_work_item,
            account_event=account_event,
            **kw,
        )

    monkeypatch.setattr(
        EscalationObservationService, "record_observed_fact", spy
    )
    return order


@pytest.mark.parametrize("limit", [1, 2, 100])
def test_processing_order_is_chronological_not_id_order(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
) -> None:
    floor = _unique_floor()
    # IDs crescem na ordem de criacao, mas occurred_at e invertido:
    # o evento de MENOR id e o mais recente no tempo.
    occurred = [
        floor + timedelta(microseconds=30),
        floor + timedelta(microseconds=10),
        floor + timedelta(microseconds=20),
    ]
    events = []
    work_items = []
    for occurred_at in occurred:
        account, _, work_item = _setup_escalation(db_session)
        events.append(
            _payment_event(
                db_session, account=account, occurred_at=occurred_at
            )
        )
        work_items.append(work_item)

    assert events[0].id < events[1].id < events[2].id

    order = _record_processing_order(monkeypatch)

    summary = run_escalation_payment_observation_recovery(
        limit=limit, activation_floor=floor
    )

    chronological = [
        event.id
        for event in sorted(events, key=lambda e: e.occurred_at)
    ]
    assert chronological == [
        events[1].id,
        events[2].id,
        events[0].id,
    ]
    assert order == chronological
    assert order != sorted(event.id for event in events)
    assert summary.candidate_count == 3
    assert summary.created_count == 3
    assert summary.failure_count == 0


def test_same_occurred_at_advances_by_id_tiebreak(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    floor = _unique_floor()
    tied_at = floor + timedelta(microseconds=5)
    events = []
    work_items = []
    for _ in range(3):
        account, _, work_item = _setup_escalation(db_session)
        events.append(
            _payment_event(
                db_session, account=account, occurred_at=tied_at
            )
        )
        work_items.append(work_item)
    later_account, _, later_work_item = _setup_escalation(
        db_session
    )
    later = _payment_event(
        db_session,
        account=later_account,
        occurred_at=tied_at + timedelta(microseconds=1),
    )

    order = _record_processing_order(monkeypatch)

    # limit=1: cada pagina tem um unico evento, entao o cursor
    # precisa atravessar os empates sem pular nem repetir nenhum.
    summary = run_escalation_payment_observation_recovery(
        limit=1, activation_floor=floor
    )

    assert order == [e.id for e in events] + [later.id]
    assert summary.candidate_count == 4
    assert summary.created_count == 4
    assert summary.failure_count == 0
    for work_item in work_items + [later_work_item]:
        assert len(_observations(db_session, work_item.id)) == 1


# ---------------------------------------------------------------------
# 14 -- corrida de dois workers sobre o mesmo candidato
# ---------------------------------------------------------------------


def test_two_concurrent_workers_never_duplicate(
    db_session: Session,
) -> None:
    floor = _unique_floor()
    account, _, work_item = _setup_escalation(db_session)
    _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(microseconds=1),
    )

    summaries: list = []
    errors: list = []
    barrier = threading.Barrier(2)

    def _worker() -> None:
        try:
            barrier.wait(timeout=5)
            summaries.append(
                run_escalation_payment_observation_recovery(
                    activation_floor=floor,
                    session_factory=SessionLocal,
                )
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [
        threading.Thread(target=_worker) for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not errors, errors
    assert len(summaries) == 2
    assert sum(s.created_count for s in summaries) == 1
    assert sum(s.failure_count for s in summaries) == 0
    assert len(_observations(db_session, work_item.id)) == 1


# ---------------------------------------------------------------------
# 15 -- validacao de parametros do worker
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bad_limit", [0, -1, 1001, True])
def test_invalid_limit_is_rejected(bad_limit: object) -> None:
    with pytest.raises(ValueError):
        run_escalation_payment_observation_recovery(
            limit=bad_limit,  # type: ignore[arg-type]
            activation_floor=_unique_floor(),
        )


def test_naive_floor_argument_is_rejected() -> None:
    # VALUE-3.4D-2b (D-b6): antes `ValueError`; agora ABSTENCAO TIPADA
    # (sem excecao, sem escrita, codigo estavel). Nunca coleta com floor
    # sem fuso explicito.
    summary = run_escalation_payment_observation_recovery(
        activation_floor=datetime(2026, 10, 2)
    )

    assert summary.provenance_blocked is True
    assert summary.provenance_code == "floor_not_timezone_aware"
    assert summary.candidate_count == 0
    assert summary.created_count == 0


# ---------------------------------------------------------------------
# 16 -- Settings: primeiro campo datetime do projeto (3 casos + bordas)
# ---------------------------------------------------------------------


def test_settings_floor_absent_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR",
        raising=False,
    )
    settings = Settings(
        _env_file=None, APP_ENV="test", DATABASE_URL=TEST_URL
    )

    assert (
        settings.escalation_payment_observation_activation_floor
        is None
    )
    assert settings.escalation_payment_observation_batch_size == 100
    assert (
        settings.escalation_payment_observation_interval_seconds
        == 60
    )


def test_settings_floor_valid_iso8601_is_parsed() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL=TEST_URL,
        ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR=(
            "2026-10-02T03:00:00+00:00"
        ),
    )

    assert (
        settings.escalation_payment_observation_activation_floor
        == datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
    )


def test_settings_floor_invalid_string_fails_startup() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="test",
            DATABASE_URL=TEST_URL,
            ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR=(
                "not-a-date"
            ),
        )


def test_settings_floor_without_timezone_fails_startup() -> None:
    with pytest.raises(ValidationError, match="fuso"):
        Settings(
            _env_file=None,
            APP_ENV="test",
            DATABASE_URL=TEST_URL,
            ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR=(
                "2026-10-02T03:00:00"
            ),
        )


@pytest.mark.parametrize(
    "env_name,bad_value",
    [
        ("ESCALATION_PAYMENT_OBSERVATION_BATCH_SIZE", "0"),
        ("ESCALATION_PAYMENT_OBSERVATION_BATCH_SIZE", "1001"),
        ("ESCALATION_PAYMENT_OBSERVATION_INTERVAL_SECONDS", "29"),
        ("ESCALATION_PAYMENT_OBSERVATION_INTERVAL_SECONDS", "3601"),
    ],
)
def test_settings_interval_and_batch_bounds(
    env_name: str, bad_value: str
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="test",
            DATABASE_URL=TEST_URL,
            **{env_name: bad_value},
        )


# ---------------------------------------------------------------------
# 17 -- registro no lifespan (startup + loop periodico)
# ---------------------------------------------------------------------


def test_worker_is_registered_in_lifespan() -> None:
    source = inspect.getsource(main_module.lifespan)

    assert "run_escalation_payment_observation_recovery_async" in source
    assert (
        "escalation_payment_observation_maintenance_loop" in source
    )


# ---------------------------------------------------------------------
# 18 -- VALUE-3.4B: normalizacao estreita do floor ("" / whitespace)
# ---------------------------------------------------------------------

_FLOOR_ENV_NAME = "ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR"


@pytest.mark.parametrize(
    "blank_value", ["", " ", "   ", "\t", "\n", " \t\r\n "]
)
def test_settings_blank_floor_argument_is_normalized_to_none(
    blank_value: str,
) -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL=TEST_URL,
        ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR=blank_value,
    )

    assert (
        settings.escalation_payment_observation_activation_floor
        is None
    )


@pytest.mark.parametrize("blank_value", ["", "   ", "\t\n"])
def test_settings_blank_floor_from_environment_is_none(
    monkeypatch: pytest.MonkeyPatch, blank_value: str
) -> None:
    # Compose/shell/.env podem materializar a variavel vazia (ver
    # Design Freeze VALUE-3.4B): precisa desabilitar, nao derrubar o
    # import dos Settings.
    monkeypatch.setenv(_FLOOR_ENV_NAME, blank_value)

    settings = Settings(
        _env_file=None, APP_ENV="test", DATABASE_URL=TEST_URL
    )

    assert (
        settings.escalation_payment_observation_activation_floor
        is None
    )


@pytest.mark.parametrize(
    "malformed_value",
    [
        "not-a-date",
        "amanha",
        "2030-01-01T00:00:00",  # naive
        "2030-01-01",  # so data
        " 2030-01-01T00:00:00+00:00 ",  # nao vazio: sem trim
    ],
)
def test_settings_non_blank_malformed_floor_still_fails_startup(
    malformed_value: str,
) -> None:
    # A normalizacao NAO pode mascarar configuracao malformada:
    # valores nao vazios continuam sob a validacao existente.
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="test",
            DATABASE_URL=TEST_URL,
            ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR=(
                malformed_value
            ),
        )


@pytest.mark.parametrize(
    "valid_value",
    [
        "2030-01-01T00:00:00+00:00",
        "2030-01-01T00:00:00Z",
        "2030-01-01T00:00:00-03:00",
    ],
)
def test_settings_tz_aware_floor_is_still_accepted(
    valid_value: str,
) -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL=TEST_URL,
        ESCALATION_PAYMENT_OBSERVATION_ACTIVATION_FLOOR=valid_value,
    )

    floor = settings.escalation_payment_observation_activation_floor
    assert floor is not None and floor.tzinfo is not None


# ---------------------------------------------------------------------
# 19 -- VALUE-3.4B: unico destino de escrita em runtime
# ---------------------------------------------------------------------

_WRITE_STATEMENT = re.compile(
    r"^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\"?(\w+)\"?",
    re.IGNORECASE,
)


def _capture_statements(run) -> list[str]:
    statements: list[str] = []

    def listener(
        conn, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        run()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return statements


def _write_targets(statements: list[str]) -> dict[str, set[str]]:
    targets: dict[str, set[str]] = {}
    for statement in statements:
        match = _WRITE_STATEMENT.match(statement)
        if match:
            verb = match.group(1).split()[0].upper()
            targets.setdefault(verb, set()).add(match.group(2))
    return targets


def test_worker_writes_only_escalation_observations_and_replay_is_silent(
    db_session: Session,
) -> None:
    account, _, _ = _setup_escalation(db_session)
    floor = _unique_floor()
    _payment_event(
        db_session,
        account=account,
        occurred_at=floor + timedelta(seconds=1),
    )
    summaries = []

    first = _capture_statements(
        lambda: summaries.append(
            run_escalation_payment_observation_recovery(
                activation_floor=floor
            )
        )
    )

    assert summaries[0].created_count == 1
    assert summaries[0].failure_count == 0
    # TODO SQL de escrita emitido pelo worker tem como unico alvo
    # escalation_observations (nenhum UPDATE/DELETE em lugar nenhum).
    # VALUE-3.4D-2b: no 1o pass com floor novo o contexto de proveniencia
    # tambem e criado (INSERT); nenhum UPDATE/DELETE em lugar nenhum.
    assert _write_targets(first) == {
        "INSERT": {
            "escalation_observations",
            "evidence_provenance_contexts",
        }
    }
    assert not any(
        re.search(r"FOR\s+UPDATE", statement, re.IGNORECASE)
        for statement in first
    )

    replay = _capture_statements(
        lambda: summaries.append(
            run_escalation_payment_observation_recovery(
                activation_floor=floor
            )
        )
    )

    assert summaries[1].created_count == 0
    assert _write_targets(replay) == {}


# ---------------------------------------------------------------------
# 20 -- VALUE-3.4B: boundary AST/import do evidence worker
# ---------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_WORKER_PATH = (
    "app/core/escalation_payment_observation_maintenance.py"
)
_SERVICE_PATH = "app/services/escalation_observation_service.py"

# modulo -> nomes permitidos (None = `import modulo` inteiro).
_ALLOWED_IMPORTS = {
    _WORKER_PATH: {
        "__future__": None,
        "asyncio": None,
        "logging": None,
        "dataclasses": {"dataclass"},
        "datetime": {"datetime"},
        "typing": {"Callable"},
        "sqlalchemy": {"and_", "exists", "or_", "select"},
        "sqlalchemy.orm": {"Session"},
        "app.core.config": {"settings"},
        "app.core.human_escalation_eligibility": {
            "work_key_for_episode"
        },
        "app.database.database": {"SessionLocal"},
        "app.models.account": {"Account"},
        "app.models.account_event": {"AccountEvent"},
        "app.models.escalation_observation": {"EscalationObservation"},
        "app.models.work": {"WorkItem"},
        "app.services.escalation_observation_service": {
            "WORK_KEY_PREFIX",
            "EscalationObservationConflictError",
            "EscalationObservationService",
            "EscalationObservationValidationError",
        },
        "uuid": {"uuid4"},
        "app.services.evidence_provenance_service": {
            "CODE_FLOOR_NOT_TIMEZONE_AWARE",
            "EvidenceProvenanceService",
        },
    },
    _SERVICE_PATH: {
        "__future__": {"annotations"},
        "dataclasses": {"dataclass"},
        "datetime": {"datetime", "timezone"},
        "sqlalchemy.exc": {"IntegrityError"},
        "sqlalchemy.orm": {"Session"},
        "app.models.account_event": {"AccountEvent"},
        "app.models.escalation_observation": {
            "ASSESSMENT_CODES",
            "EscalationObservation",
        },
        "app.models.work": {"WorkItem"},
        "app.services.work_service": {"WorkActor"},
        "app.services.evidence_provenance_service": {
            "ProvenanceBinding"
        },
    },
}

_WRITE_CALLS = frozenset(
    {
        "add",
        "add_all",
        "commit",
        "delete",
        "merge",
        "flush",
        "bulk_save_objects",
        "bulk_insert_mappings",
        "bulk_update_mappings",
        "update",
        "insert",
        "executemany",
        "begin",
    }
)


def _boundary_problems(
    relative_path: str, source: bytes | None = None
) -> list[str]:
    raw = (
        source
        if source is not None
        else (_BACKEND_DIR / relative_path).read_bytes()
    )
    tree = ast.parse(raw)
    allowed = _ALLOWED_IMPORTS[relative_path]
    problems: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            names = {alias.name for alias in node.names}
            if module not in allowed:
                problems.append(f"import fora da allowlist: {module}")
            elif allowed[module] is not None and not names <= allowed[
                module
            ]:
                problems.append(
                    f"nomes fora da allowlist em {module}: "
                    f"{sorted(names - allowed[module])}"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in allowed:
                    problems.append(
                        f"import fora da allowlist: {alias.name}"
                    )

    attribute_calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    ]

    if relative_path == _WORKER_PATH:
        forbidden = sorted(set(attribute_calls) & _WRITE_CALLS)
        if forbidden:
            problems.append(
                f"worker chama operacao de escrita: {forbidden}"
            )
    else:
        forbidden = sorted(
            set(attribute_calls) & (_WRITE_CALLS - {"add", "commit"})
        )
        if forbidden:
            problems.append(
                f"servico chama escrita nao permitida: {forbidden}"
            )
        built = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "EscalationObservation"
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add"
            ):
                argument = node.args[0] if node.args else None
                is_observation = (
                    isinstance(argument, ast.Name)
                    and argument.id in built | {"observation"}
                ) or (
                    isinstance(argument, ast.Call)
                    and getattr(argument.func, "id", "")
                    == "EscalationObservation"
                )
                if not is_observation:
                    problems.append(
                        "add() de algo que nao e EscalationObservation: "
                        f"{ast.unparse(node)}"
                    )
    return problems


@pytest.mark.parametrize(
    "relative_path", [_WORKER_PATH, _SERVICE_PATH]
)
def test_evidence_worker_and_service_respect_the_boundary(
    relative_path: str,
) -> None:
    assert _boundary_problems(relative_path) == []


@pytest.mark.parametrize(
    "relative_path,appended,expected_fragment",
    [
        (
            _WORKER_PATH,
            "\nfrom app.services.approval_service import "
            "ApprovalService\n",
            "import fora da allowlist",
        ),
        (
            _WORKER_PATH,
            "\nfrom app.services.overdue_detection_service import "
            "OverdueDetectionService\n",
            "import fora da allowlist",
        ),
        (
            _WORKER_PATH,
            "\nfrom sqlalchemy import update\n",
            "nomes fora da allowlist",
        ),
        (
            _WORKER_PATH,
            "\ndef _x(db):\n    db.delete(object())\n",
            "operacao de escrita",
        ),
        (
            _WORKER_PATH,
            "\ndef _x(db):\n    db.commit()\n",
            "operacao de escrita",
        ),
        (
            _SERVICE_PATH,
            "\ndef _x(self):\n    self.db.add(Account())\n",
            "nao e EscalationObservation",
        ),
        (
            _SERVICE_PATH,
            "\ndef _x(self):\n    self.db.delete(object())\n",
            "escrita nao permitida",
        ),
        (
            _SERVICE_PATH,
            "\nfrom app.services.approval_service import "
            "ApprovalService\n",
            "import fora da allowlist",
        ),
    ],
)
def test_boundary_guard_is_sensitive_to_forbidden_changes(
    relative_path: str, appended: str, expected_fragment: str
) -> None:
    original = (_BACKEND_DIR / relative_path).read_bytes()
    mutated = original + appended.encode("utf-8")

    problems = _boundary_problems(relative_path, mutated)

    assert any(expected_fragment in problem for problem in problems), (
        problems
    )


# ---------------------------------------------------------------------
# 21 -- VALUE-3.4B: repasse nulo do floor no docker-compose.yml (DEV)
# ---------------------------------------------------------------------

_COMPOSE_PATH = _BACKEND_DIR / "docker-compose.yml"


def _compose_floor_problems(text: str) -> list[str]:
    # Sem PyYAML (nao e dependencia do projeto): analise textual dos
    # blocos de servico de primeiro nivel.
    text = text.replace("\r\n", "\n")
    services = re.search(
        r"^services:\n(.*?)(?=^\S)", text, re.DOTALL | re.MULTILINE
    )
    assert services is not None, "docker-compose.yml sem `services:`"
    parts = re.split(
        r"^  (\w[\w-]*):\n", services.group(1), flags=re.MULTILINE
    )
    blocks = {
        parts[index]: parts[index + 1]
        for index in range(1, len(parts), 2)
    }

    problems: list[str] = []
    backend_lines = [
        line
        for line in blocks.get("backend", "").splitlines()
        if _FLOOR_ENV_NAME in line
        and not line.lstrip().startswith("#")
    ]
    if backend_lines != [f"      {_FLOOR_ENV_NAME}:"]:
        problems.append(
            f"servico 'backend' deve declarar exatamente "
            f"'{_FLOOR_ENV_NAME}:' (repasse nulo); achado={backend_lines}"
        )
    for name, block in blocks.items():
        if name == "backend":
            continue
        if any(
            _FLOOR_ENV_NAME in line
            and not line.lstrip().startswith("#")
            for line in block.splitlines()
        ):
            problems.append(
                f"servico '{name}' nao deve repassar o floor"
            )
    return problems


def test_compose_passes_floor_as_null_passthrough_only_to_backend() -> (
    None
):
    assert _compose_floor_problems(
        _COMPOSE_PATH.read_text(encoding="utf-8")
    ) == []


@pytest.mark.parametrize(
    "mutation_name",
    [
        "valor_fixo",
        "default_vazio",
        "servico_migration",
        "removido_do_backend",
    ],
)
def test_compose_floor_guard_is_sensitive_to_forbidden_forms(
    mutation_name: str,
) -> None:
    text = _COMPOSE_PATH.read_text(encoding="utf-8").replace(
        "\r\n", "\n"
    )
    line = f"      {_FLOOR_ENV_NAME}:"
    assert line in text

    mutated = {
        "valor_fixo": text.replace(
            line, f"{line} '2030-01-01T00:00:00+00:00'"
        ),
        "default_vazio": text.replace(
            line, f"{line} ${{{_FLOOR_ENV_NAME}:-}}"
        ),
        "servico_migration": text.replace(
            "      DATABASE_APPLICATION_NAME: auneron-migration\n",
            "      DATABASE_APPLICATION_NAME: auneron-migration\n"
            f"{line}\n",
        ),
        "removido_do_backend": text.replace(line + "\n", ""),
    }[mutation_name]
    assert mutated != text

    assert _compose_floor_problems(mutated) != []
