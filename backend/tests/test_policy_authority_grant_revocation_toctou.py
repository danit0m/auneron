"""
DW-7.2 B2 -- fecha a janela TOCTOU entre revogacao e execucao via ordem
de lock fixa: Grant FOR UPDATE sempre antes de Account FOR UPDATE, nos
dois fluxos que tocam ambos (so a execucao toca os dois; a revogacao so
toca Grant). Isso torna a ordem livre de deadlock por construcao --
provado aqui tambem por um teste de estresse com muitas tentativas
concorrentes reais.
"""

import threading
import time
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy import event
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalStateError
from app.core.policy_definitions import ACCOUNT_MARK_OVERDUE_POLICY_V1
from app.database.database import SessionLocal
from app.database.database import engine
from app.models.account import Account
from app.models.policy_authority_grant import PolicyAuthorityGrant
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


def _setup(
    db_session: Session, *, due_date: date
) -> tuple[Account, PolicyAuthorityGrant, User]:
    register_account_mark_overdue_skill()
    granter = User(
        name="TOCTOU Granter",
        email="toctou-granter@example.com",
        password_hash="not-used",
        role="administrator",
        active=True,
    )
    revoker = User(
        name="TOCTOU Revoker",
        email="toctou-revoker@example.com",
        password_hash="not-used",
        role="administrator",
        active=True,
    )
    db_session.add_all([granter, revoker])
    db_session.commit()
    db_session.refresh(granter)
    db_session.refresh(revoker)

    grant_result = PolicyAuthorityGrantService(
        db_session
    ).create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )

    account = Account(
        cliente="Cliente TOCTOU",
        email="cliente-toctou@example.com",
        whatsapp=None,
        valor=800,
        vencimento=due_date,
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    return account, grant_result.grant, revoker


def test_revocation_blocks_until_execution_transaction_completes(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=4)
    account, grant, revoker = _setup(db_session, due_date=due_date)

    t1_has_lock = threading.Event()
    release_t1 = threading.Event()
    outcome: dict[str, object] = {}

    def after_hook(conn, cursor, statement, parameters, context, executemany):
        if conn.info.get("role") != "T1":
            return
        upper = statement.upper()
        if "FOR UPDATE" in upper and "POLICY_AUTHORITY_GRANTS" in upper:
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
        assert acquired, "T1 não travou o Grant a tempo."

        t2_session = _tagged_session("T2")
        pid_t2 = _backend_pid(t2_session)
        t2_session.execute(text("SET LOCAL lock_timeout = '30s'"))

        def t2_worker() -> None:
            service = PolicyAuthorityGrantService(t2_session)
            try:
                outcome["t2_result"] = service.revoke_grant(
                    grant.id, revoked_by_user_id=revoker.id
                )
            except Exception as error:  # noqa: BLE001
                t2_session.rollback()
                outcome["t2_error"] = error

        t2_thread = threading.Thread(target=t2_worker)
        t2_thread.start()

        blocked = _watcher_confirms_blocked(pid_t2)
        assert blocked, (
            "Watcher não observou T2 (revogação) bloqueada no lock "
            "do Grant -- concorrência real não comprovada."
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

    # Execução já estava além do lock do Grant quando a revogação
    # chegou -- seu efeito, legitimamente serializado antes da
    # revogação, permanece válido (não é desfeito retroativamente).
    assert "t1_error" not in outcome, outcome.get("t1_error")
    assert outcome["t1_result"].duplicate is False

    assert "t2_error" not in outcome, outcome.get("t2_error")
    assert outcome["t2_result"].grant.state == "revoked"

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "atrasado"
    reloaded_grant = db_session.get(PolicyAuthorityGrant, grant.id)
    assert reloaded_grant.state == "revoked"


def test_execution_after_revocation_commits_sees_revoked_and_fails_closed(
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=4)
    account, grant, revoker = _setup(db_session, due_date=due_date)

    PolicyAuthorityGrantService(db_session).revoke_grant(
        grant.id, revoked_by_user_id=revoker.id
    )

    service = PolicyAccountMarkOverdueExecutionService(db_session)
    with pytest.raises(ApprovalAuthorizationError):
        service.execute(account_id=account.id, due_date=due_date)

    db_session.expire_all()
    assert db_session.get(Account, account.id).status == "aberto"


def test_grant_then_account_lock_order_produces_no_deadlock(
    db_session: Session,
) -> None:
    """
    Estresse leve: muitas tentativas reais e concorrentes de
    execute()/revoke_grant() sobre o MESMO grant. A ordem fixa
    Grant->Account (execução) e Grant-somente (revogação) torna
    deadlock estruturalmente impossível -- nenhuma chamada deve
    levantar um erro de deadlock do Postgres, e todas devem terminar
    dentro do timeout.
    """
    due_date = date.today() - timedelta(days=4)
    account, grant, revoker = _setup(db_session, due_date=due_date)

    outcomes: list[BaseException | None] = [None] * 6
    sessions = [SessionLocal() for _ in range(6)]

    def execute_worker(index: int, session: Session) -> None:
        try:
            PolicyAccountMarkOverdueExecutionService(
                session
            ).execute(account_id=account.id, due_date=due_date)
        except BaseException as error:  # noqa: BLE001
            session.rollback()
            outcomes[index] = error

    def revoke_worker(index: int, session: Session) -> None:
        try:
            PolicyAuthorityGrantService(session).revoke_grant(
                grant.id, revoked_by_user_id=revoker.id
            )
        except BaseException as error:  # noqa: BLE001
            session.rollback()
            outcomes[index] = error

    threads = []
    for i in range(3):
        threads.append(
            threading.Thread(
                target=execute_worker, args=(i, sessions[i])
            )
        )
    for i in range(3, 6):
        threads.append(
            threading.Thread(
                target=revoke_worker, args=(i, sessions[i])
            )
        )

    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
            assert not thread.is_alive(), (
                "Thread não concluiu a tempo -- possível deadlock."
            )
    finally:
        for session in sessions:
            session.close()

    for error in outcomes:
        if error is not None:
            assert "deadlock" not in str(error).lower()
