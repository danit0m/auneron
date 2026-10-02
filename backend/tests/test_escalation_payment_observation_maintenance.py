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

import inspect
import threading
import time
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import main as main_module
from app.core.authentication import hash_password
from app.core.config import Settings
from app.core.config import settings as global_settings
from app.core.escalation_payment_observation_maintenance import (
    run_escalation_payment_observation_recovery,
)
from app.database.database import SessionLocal
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


TEST_URL = (
    "postgresql+psycopg://"
    "auneron:test_password"
    "@localhost:5432/auneron_test"
)

_COUNTER = {"n": 0}


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
    with pytest.raises(ValueError):
        run_escalation_payment_observation_recovery(
            activation_floor=datetime(2026, 10, 2)
        )


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
