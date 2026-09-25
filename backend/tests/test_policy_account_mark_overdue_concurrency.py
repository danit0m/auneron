"""
DW-7.3 -- duas execucoes policy-governed concorrentes sobre o MESMO
episodio nao podem produzir dois efeitos. Mesmo padrao de sincronizacao
test-only (hook em after_cursor_execute + watcher pg_stat_activity) ja
estabelecido em test_human_account_mark_overdue_execution_concurrency.py
-- aqui o ponto de contencao real e o SELECT ... FOR UPDATE em accounts,
alcancado depois que ambas as transacoes ja travaram o mesmo
PolicyAuthorityGrant (B2: Grant sempre antes de Account).
"""

import threading
import time
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalConsumptionConflictError
from app.core.policy_definitions import ACCOUNT_MARK_OVERDUE_POLICY_V1
from app.database.database import SessionLocal
from app.database.database import engine
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.policy_authority_consumption import (
    PolicyAuthorityConsumption,
)
from app.models.user import User
from app.services.policy_account_mark_overdue_execution_service import (
    PolicyAccountMarkOverdueExecutionService,
)
from app.services.policy_authority_grant_service import (
    PolicyAuthorityGrantService,
)
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)

WATCHER_TIMEOUT_S = 5.0
WATCHER_POLL_INTERVAL_S = 0.02
HOOK_RELEASE_TIMEOUT_S = 10.0


def _tagged_session(role: str) -> Session:
    session = SessionLocal()
    session.connection().info["role"] = role
    return session


def _backend_pid(session: Session) -> int:
    return session.execute(
        text("SELECT pg_backend_pid()")
    ).scalar_one()


def _watcher_confirms_blocked(pid: int) -> bool:
    connection = engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    )
    try:
        deadline = time.monotonic() + WATCHER_TIMEOUT_S
        while time.monotonic() < deadline:
            row = connection.execute(
                text(
                    "SELECT wait_event_type FROM pg_stat_activity "
                    "WHERE pid = :pid"
                ),
                {"pid": pid},
            ).first()
            if row is not None and row[0] == "Lock":
                return True
            time.sleep(WATCHER_POLL_INTERVAL_S)
        return False
    finally:
        connection.close()


def _setup_episode(db_session: Session, *, due_date: date) -> Account:
    register_account_mark_overdue_skill()
    granter = User(
        name="Policy Concurrency Granter",
        email="policy-concurrency-granter@example.com",
        password_hash="not-used",
        role="administrator",
        active=True,
    )
    db_session.add(granter)
    db_session.commit()
    db_session.refresh(granter)

    PolicyAuthorityGrantService(db_session).create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )

    account = Account(
        cliente="Cliente Policy Concorrencia",
        email="cliente-policy-concurrency@example.com",
        whatsapp=None,
        valor=1100,
        vencimento=due_date,
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_two_concurrent_executions_same_episode_only_one_succeeds(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=7)
    account = _setup_episode(db_session, due_date=due_date)

    t1_has_lock = threading.Event()
    release_t1 = threading.Event()
    outcome: dict[str, object] = {}

    def after_hook(conn, cursor, statement, parameters, context, executemany):
        if conn.info.get("role") != "T1":
            return
        upper = statement.upper()
        if "FOR UPDATE" in upper and "ACCOUNTS" in upper:
            if not t1_has_lock.is_set():
                t1_has_lock.set()
                released = release_t1.wait(
                    timeout=HOOK_RELEASE_TIMEOUT_S
                )
                if not released:
                    raise AssertionError(
                        "release_t1 não sinalizado a tempo."
                    )

    event.listen(engine, "after_cursor_execute", after_hook)

    t1_session = _tagged_session("T1")
    t2_session: Session | None = None

    try:
        pid_t1 = _backend_pid(t1_session)

        def t1_worker() -> None:
            service = PolicyAccountMarkOverdueExecutionService(
                t1_session
            )
            try:
                outcome["t1_result"] = service.execute(
                    account_id=account.id, due_date=due_date
                )
            except Exception as error:  # noqa: BLE001
                t1_session.rollback()
                outcome["t1_error"] = error

        t1_thread = threading.Thread(target=t1_worker)
        t1_thread.start()

        acquired = t1_has_lock.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert acquired, "T1 não alcançou o Account FOR UPDATE a tempo."

        t2_session = _tagged_session("T2")
        pid_t2 = _backend_pid(t2_session)
        assert pid_t1 != pid_t2
        t2_session.execute(text("SET LOCAL lock_timeout = '30s'"))

        def t2_worker() -> None:
            service = PolicyAccountMarkOverdueExecutionService(
                t2_session
            )
            try:
                outcome["t2_result"] = service.execute(
                    account_id=account.id, due_date=due_date
                )
            except Exception as error:  # noqa: BLE001
                t2_session.rollback()
                outcome["t2_error"] = error

        t2_thread = threading.Thread(target=t2_worker)
        t2_thread.start()

        blocked = _watcher_confirms_blocked(pid_t2)
        assert blocked, (
            "Watcher não observou wait_event_type='Lock' para T2 -- "
            "concorrência real não comprovada."
        )

        release_t1.set()
        t1_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t1_thread.is_alive()
        t2_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t2_thread.is_alive()
    finally:
        event.remove(engine, "after_cursor_execute", after_hook)
        t1_session.close()
        if t2_session is not None:
            t2_session.close()

    assert "t1_error" not in outcome, outcome.get("t1_error")
    assert outcome["t1_result"].duplicate is False

    assert "t2_error" in outcome, (
        "T2 deveria falhar após observar o estado fresco pós-T1."
    )
    assert isinstance(
        outcome["t2_error"], ApprovalConsumptionConflictError
    )

    db_session.expire_all()
    reloaded_account = db_session.get(Account, account.id)
    assert reloaded_account.status == "atrasado"

    consumption_count = db_session.execute(
        select(func.count(PolicyAuthorityConsumption.id))
    ).scalar_one()
    assert consumption_count == 1

    event_count = db_session.execute(
        select(func.count(AccountEvent.id))
    ).scalar_one()
    assert event_count == 1


def test_concurrent_executions_different_episodes_both_succeed_independently(
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    granter = User(
        name="Policy Concurrency Granter 2",
        email="policy-concurrency-granter-2@example.com",
        password_hash="not-used",
        role="administrator",
        active=True,
    )
    db_session.add(granter)
    db_session.commit()
    db_session.refresh(granter)
    PolicyAuthorityGrantService(db_session).create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )

    due_date = date.today() - timedelta(days=6)
    account_a = Account(
        cliente="Cliente Policy Concorrencia A",
        email="cliente-policy-concurrency-a@example.com",
        whatsapp=None,
        valor=500,
        vencimento=due_date,
        status="aberto",
    )
    account_b = Account(
        cliente="Cliente Policy Concorrencia B",
        email="cliente-policy-concurrency-b@example.com",
        whatsapp=None,
        valor=700,
        vencimento=due_date,
        status="aberto",
    )
    db_session.add_all([account_a, account_b])
    db_session.commit()
    db_session.refresh(account_a)
    db_session.refresh(account_b)

    session_a = SessionLocal()
    session_b = SessionLocal()
    outcome: dict[str, object] = {}
    try:
        def worker_a() -> None:
            service = PolicyAccountMarkOverdueExecutionService(
                session_a
            )
            outcome["a"] = service.execute(
                account_id=account_a.id, due_date=due_date
            )

        def worker_b() -> None:
            service = PolicyAccountMarkOverdueExecutionService(
                session_b
            )
            outcome["b"] = service.execute(
                account_id=account_b.id, due_date=due_date
            )

        thread_a = threading.Thread(target=worker_a)
        thread_b = threading.Thread(target=worker_b)
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        thread_b.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not thread_a.is_alive()
        assert not thread_b.is_alive()
    finally:
        session_a.close()
        session_b.close()

    assert outcome["a"].duplicate is False
    assert outcome["b"].duplicate is False

    db_session.expire_all()
    assert db_session.get(Account, account_a.id).status == "atrasado"
    assert db_session.get(Account, account_b.id).status == "atrasado"
