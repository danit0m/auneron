"""
DW-7.2 R1 -- concorrencia real entre o corredor humano (25O, intocado)
e o novo corredor policy-governed sobre a MESMA Account. Prova que L3
nao degrada a garantia N3 ja provada do corredor humano
(test_human_account_mark_overdue_execution_concurrency.py): o ponto de
contencao compartilhado e o mesmo SELECT ... FOR UPDATE em accounts,
reusado sem nenhuma alteracao no corredor humano.
"""

import os
import threading
import time
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalConsumptionConflictError
from app.core.authentication import hash_password
from app.core.policy_definitions import ACCOUNT_MARK_OVERDUE_POLICY_V1
from app.core.security import API_KEY_HEADER_NAME
from app.database.database import SessionLocal
from app.database.database import engine
from app.main import app
from app.models.account import Account
from app.models.user import User
from app.services.human_account_mark_overdue_execution_service import (
    HumanAccountMarkOverdueExecutionService,
)
from app.services.policy_account_mark_overdue_execution_service import (
    PolicyAccountMarkOverdueExecutionService,
)
from app.services.policy_authority_grant_service import (
    PolicyAuthorityGrantService,
)
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)

AUTHENTICATED_EMAIL = "developer.test@example.com"
APPROVER_EMAIL = "approver.policy-human-concurrency@example.com"
APPROVER_PASSWORD = "Senha-Aprovador-Concorrencia-Policy-123!"
GRANTER_EMAIL = "granter.policy-human-concurrency@example.com"

WATCHER_TIMEOUT_S = 5.0
WATCHER_POLL_INTERVAL_S = 0.02
HOOK_RELEASE_TIMEOUT_S = 10.0


def _create_approver(db_session: Session) -> User:
    user = User(
        name="Aprovador Concorrencia Human x Policy",
        email=APPROVER_EMAIL,
        password_hash=hash_password(APPROVER_PASSWORD),
        role="administrator",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _approver_client(db_session: Session) -> TestClient:
    _create_approver(db_session)
    test_client = TestClient(app)
    test_client.headers.update(
        {API_KEY_HEADER_NAME: os.environ["API_KEY"]}
    )
    login = test_client.post(
        "/auth/login",
        json={"email": APPROVER_EMAIL, "password": APPROVER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    return test_client


def _make_overdue_account(
    db_session: Session, *, due_date: date, email: str
) -> Account:
    account = Account(
        cliente="Cliente Human x Policy",
        email=email,
        whatsapp=None,
        valor=1300,
        vencimento=due_date,
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _setup_human_approved_episode(
    client: TestClient, db_session: Session, *, due_date: date
) -> Account:
    register_account_mark_overdue_skill()
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.human-x-policy@example.com",
    )
    materialize = client.post(
        "/recommendations/mark-overdue/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}/materialize"
    )
    assert materialize.status_code == 201, materialize.text
    request_id = materialize.json()["approval_request"]["request_id"]

    approver = _approver_client(db_session)
    decide = approver.post(
        f"/approvals/{request_id}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text
    return account


def _setup_active_grant(db_session: Session) -> None:
    granter = User(
        name="Policy Granter Human x Policy",
        email=GRANTER_EMAIL,
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


def test_human_and_policy_corridors_racing_same_account_only_one_produces_effect(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=9)
    account = _setup_human_approved_episode(
        client, db_session, due_date=due_date
    )
    _setup_active_grant(db_session)
    authority = (
        db_session.query(User)
        .filter(User.email == AUTHENTICATED_EMAIL)
        .one()
    )

    t1_has_lock = threading.Event()
    release_t1 = threading.Event()
    t1_wants_workitem_relock = threading.Event()
    release_t1_workitem_relock = threading.Event()
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

    def before_hook(conn, cursor, statement, parameters, context, executemany):
        if conn.info.get("role") != "T1":
            return
        upper = statement.upper()
        if "FOR UPDATE" in upper and "WORK_ITEMS" in upper:
            if (
                t1_has_lock.is_set()
                and not t1_wants_workitem_relock.is_set()
            ):
                t1_wants_workitem_relock.set()
                released = release_t1_workitem_relock.wait(
                    timeout=HOOK_RELEASE_TIMEOUT_S
                )
                if not released:
                    raise AssertionError(
                        "release_t1_workitem_relock não sinalizado "
                        "a tempo."
                    )

    event.listen(engine, "after_cursor_execute", after_hook)
    event.listen(engine, "before_cursor_execute", before_hook)

    t1_session = _tagged_session("T1")
    t2_session: Session | None = None

    try:
        pid_t1 = _backend_pid(t1_session)

        def t1_worker() -> None:
            service = HumanAccountMarkOverdueExecutionService(
                t1_session
            )
            try:
                outcome["t1_result"] = service.execute(
                    account_id=account.id,
                    due_date=due_date,
                    authority_user_id=authority.id,
                )
            except Exception as error:  # noqa: BLE001
                t1_session.rollback()
                outcome["t1_error"] = error

        t1_thread = threading.Thread(target=t1_worker)
        t1_thread.start()

        acquired = t1_has_lock.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert acquired, "T1 (humano) não travou Account a tempo."

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
            "Watcher não observou T2 (policy) bloqueada no Account -- "
            "concorrência real não comprovada."
        )

        release_t1.set()

        reached_relock = t1_wants_workitem_relock.wait(
            timeout=HOOK_RELEASE_TIMEOUT_S
        )
        assert reached_relock, (
            "T1 não alcançou a transição final do WorkItem a tempo."
        )

        t2_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t2_thread.is_alive(), "T2 não concluiu a tempo."

        release_t1_workitem_relock.set()

        t1_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t1_thread.is_alive(), "T1 não concluiu a tempo."
    finally:
        event.remove(engine, "after_cursor_execute", after_hook)
        event.remove(engine, "before_cursor_execute", before_hook)
        t1_session.close()
        if t2_session is not None:
            t2_session.close()

    assert "t1_error" not in outcome, outcome.get("t1_error")
    assert outcome["t1_result"].duplicate is False
    assert outcome["t1_result"].output["new_status"] == "atrasado"

    assert "t2_error" in outcome, (
        "T2 (policy) deveria falhar após observar o efeito humano já "
        "commitado."
    )
    assert isinstance(
        outcome["t2_error"], ApprovalConsumptionConflictError
    )

    db_session.expire_all()
    reloaded_account = db_session.get(Account, account.id)
    assert reloaded_account.status == "atrasado"
