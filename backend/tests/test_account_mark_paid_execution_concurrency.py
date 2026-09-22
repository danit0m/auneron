"""
GAM V1 -- N3 Concurrent Evidence (account.mark_paid).

Prova, sob concorrencia real, que duas ApprovalRequests distintas e
independentemente aprovadas (A e B) para o mesmo
{account_id, expected_status} nao podem produzir dois efeitos de
negocio. As duas ApprovalRequests/Decisions coexistirem legitimamente
NAO e a duplicidade sob teste aqui -- authority convergence permanece
fora do escopo de N3 (DEFERRED-CONCURRENT-AUTHORITY). O que N3 exige e
que apenas uma execucao produza o efeito financeiro.

Diferente de mark_overdue, aqui nao ha WorkItem nem commit
intermediario -- o unico ponto de contencao real, desde o inicio, e
SELECT Account ... FOR UPDATE (A e B tem ApprovalRequest.id distintos,
entao lock_request() nao contende entre elas). Por isso o perdedor (B)
pode ser exigido com zero writes absolutos: nenhuma escrita
operacional intermediaria existe neste corredor antes do lock de
Account.

Mecanismo de sincronizacao -- identico em desenho ao usado para
mark_overdue (ver esse arquivo para o racional completo): sessoes
independentes por worker, Connection.info["role"] identificando T1 no
hook do Engine, hook pausando T1 sincronamente dentro do proprio
after_cursor_execute logo apos o lock em accounts ser adquirido,
watcher com conexao autocommit confirmando wait_event_type='Lock' no
PID real de T2 antes de liberar T1, e SET LOCAL lock_timeout como
watchdog (nunca sincronizacao).
"""

import os
import threading
import time
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalConsumptionConflictError
from app.core.authentication import hash_password
from app.core.security import API_KEY_HEADER_NAME
from app.database.database import SessionLocal
from app.database.database import engine
from app.main import app
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalConsumption
from app.models.skill import SkillInvocation
from app.models.user import User
from app.repositories.skill_repository import SkillRepository
from app.services.account_mark_paid_execution_service import (
    AccountMarkPaidExecutionService,
)
from scripts.register_account_mark_paid_skill import (
    main as register_account_mark_paid_skill,
)


AUTHENTICATED_EMAIL = "developer.test@example.com"
APPROVER_EMAIL = "approver.mark-paid.concurrency@example.com"
APPROVER_PASSWORD = "Senha-Aprovador-Concorrencia-123!"

WATCHER_TIMEOUT_S = 5.0
WATCHER_POLL_INTERVAL_S = 0.02
HOOK_RELEASE_TIMEOUT_S = 10.0


def _create_approver(db_session: Session) -> User:
    user = User(
        name="Aprovador Concorrencia N3 (mark_paid)",
        email=APPROVER_EMAIL,
        password_hash=hash_password(APPROVER_PASSWORD),
        role="manager",
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


def _make_account(db_session: Session, *, email: str) -> Account:
    account = Account(
        cliente="Cliente Concorrencia N3 (mark_paid)",
        email=email,
        whatsapp=None,
        valor=900,
        vencimento=date(2026, 6, 15),
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _mark_paid_version_id(db_session: Session) -> int:
    register_account_mark_paid_skill()
    repository = SkillRepository(db_session)
    skill = repository.find_skill_by_key("account.mark_paid")
    versions = [
        version
        for version in repository.list_versions(skill.id)
        if version.status == "published"
    ]
    assert len(versions) == 1
    return versions[0].id


def _setup_two_approved_authorities(
    client: TestClient, db_session: Session
) -> tuple[Account, int, int]:
    account = _make_account(
        db_session, email="cliente.mark-paid.concurrency@example.com"
    )
    version_id = _mark_paid_version_id(db_session)
    approver = _approver_client(db_session)

    request_a = client.post(
        f"/approvals/skill-executions/{version_id}",
        json={
            "input_payload": {
                "account_id": account.id,
                "expected_status": "aberto",
            }
        },
        headers={"Idempotency-Key": "mark-paid-concurrency-a"},
    )
    assert request_a.status_code == 201, request_a.text
    request_a_id = request_a.json()["request"]["request_id"]

    request_b = client.post(
        f"/approvals/skill-executions/{version_id}",
        json={
            "input_payload": {
                "account_id": account.id,
                "expected_status": "aberto",
            }
        },
        headers={"Idempotency-Key": "mark-paid-concurrency-b"},
    )
    assert request_b.status_code == 201, request_b.text
    request_b_id = request_b.json()["request"]["request_id"]
    assert request_b_id != request_a_id

    decide_a = approver.post(
        f"/approvals/{request_a_id}/decision",
        json={"decision": "approved"},
    )
    assert decide_a.status_code == 200, decide_a.text
    decide_b = approver.post(
        f"/approvals/{request_b_id}/decision",
        json={"decision": "approved"},
    )
    assert decide_b.status_code == 200, decide_b.text

    return account, request_a_id, request_b_id


def _tagged_session(role: str) -> Session:
    session = SessionLocal()
    session.connection().info["role"] = role
    return session


def _backend_pid(session: Session) -> int:
    return session.execute(text("SELECT pg_backend_pid()")).scalar_one()


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


def test_concurrent_distinct_authorities_produce_single_business_effect(
    client: TestClient,
    db_session: Session,
) -> None:
    account, request_a_id, request_b_id = _setup_two_approved_authorities(
        client, db_session
    )
    authority = (
        db_session.query(User)
        .filter(User.email == AUTHENTICATED_EMAIL)
        .one()
    )

    before_consumptions = db_session.execute(
        select(func.count(ApprovalConsumption.id))
    ).scalar_one()
    before_invocations = db_session.execute(
        select(func.count(SkillInvocation.id))
    ).scalar_one()
    before_events = db_session.execute(
        select(func.count(AccountEvent.id))
    ).scalar_one()

    t1_has_lock = threading.Event()
    release_t1 = threading.Event()
    outcome: dict[str, object] = {}

    def hook(conn, cursor, statement, parameters, context, executemany):
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
                        "release_t1 nao foi sinalizado a tempo -- "
                        "possivel falha do watcher/coordenador."
                    )

    event.listen(engine, "after_cursor_execute", hook)

    t1_session = _tagged_session("T1")
    t2_session: Session | None = None

    try:
        pid_t1 = _backend_pid(t1_session)

        def t1_worker() -> None:
            service = AccountMarkPaidExecutionService(t1_session)
            try:
                result = service.execute(
                    account_id=account.id,
                    approval_request_id=request_a_id,
                    expected_status="aberto",
                    authority_user_id=authority.id,
                )
                outcome["t1_result"] = result
            except Exception as error:  # noqa: BLE001
                # Replica o rollback automatico que get_db() faz no
                # ciclo de vida real da requisicao (database.py:137-141)
                # -- aqui chamamos o service diretamente, sem essa rede
                # de seguranca, entao precisamos fazer o mesmo
                # manualmente para nao reter locks indevidamente.
                t1_session.rollback()
                outcome["t1_error"] = error

        t1_thread = threading.Thread(target=t1_worker)
        t1_thread.start()

        acquired = t1_has_lock.wait(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert acquired, (
            "T1 (autoridade A) nao alcancou/adquiriu o Account FOR "
            "UPDATE a tempo."
        )

        t2_session = _tagged_session("T2")
        pid_t2 = _backend_pid(t2_session)
        assert pid_t1 != pid_t2, (
            "PIDs de T1/T2 devem ser distintos -- conexoes independentes."
        )
        t2_session.execute(text("SET LOCAL lock_timeout = '30s'"))

        def t2_worker() -> None:
            service = AccountMarkPaidExecutionService(t2_session)
            try:
                result = service.execute(
                    account_id=account.id,
                    approval_request_id=request_b_id,
                    expected_status="aberto",
                    authority_user_id=authority.id,
                )
                outcome["t2_result"] = result
            except Exception as error:  # noqa: BLE001
                t2_session.rollback()
                outcome["t2_error"] = error

        t2_thread = threading.Thread(target=t2_worker)
        t2_thread.start()

        blocked = _watcher_confirms_blocked(pid_t2)
        assert blocked, (
            "Watcher nao observou wait_event_type='Lock' para o PID de "
            "T2 (autoridade B) dentro do timeout de seguranca -- "
            "concorrencia real nao foi comprovada; o teste falha em vez "
            "de inferir por tempo."
        )

        release_t1.set()

        t1_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t1_thread.is_alive(), "T1 nao concluiu a tempo."
        t2_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t2_thread.is_alive(), "T2 nao concluiu a tempo."
    finally:
        event.remove(engine, "after_cursor_execute", hook)
        t1_session.close()
        if t2_session is not None:
            t2_session.close()

    assert "t1_error" not in outcome, (
        f"T1 (autoridade A) nao deveria falhar: {outcome.get('t1_error')}"
    )
    t1_result = outcome["t1_result"]
    assert t1_result.duplicate is False
    assert t1_result.output["new_status"] == "pago"

    assert "t2_error" in outcome, (
        "T2 (autoridade B) deveria falhar apos observar o estado "
        "fresco pos-T1."
    )
    assert isinstance(
        outcome["t2_error"], ApprovalConsumptionConflictError
    )

    db_session.expire_all()
    reloaded_account = db_session.get(Account, account.id)
    assert reloaded_account.status == "pago"

    after_consumptions = db_session.execute(
        select(func.count(ApprovalConsumption.id))
    ).scalar_one()
    after_invocations = db_session.execute(
        select(func.count(SkillInvocation.id))
    ).scalar_one()
    after_events = db_session.execute(
        select(func.count(AccountEvent.id))
    ).scalar_one()

    assert after_consumptions == before_consumptions + 1
    assert after_invocations == before_invocations + 1
    assert after_events == before_events + 1

    consumption_a = (
        db_session.query(ApprovalConsumption)
        .filter(ApprovalConsumption.approval_request_id == request_a_id)
        .one_or_none()
    )
    assert consumption_a is not None

    consumption_b = (
        db_session.query(ApprovalConsumption)
        .filter(ApprovalConsumption.approval_request_id == request_b_id)
        .one_or_none()
    )
    assert consumption_b is None
