"""
GAM V1 -- N3 Concurrent Evidence (account.mark_overdue).

Prova, sob concorrencia real (duas conexoes/threads/transacoes Postgres
genuinamente sobrepostas, nao apenas duas chamadas sequenciais), que
duas tentativas de execucao do mesmo episodio nao produzem dois
business effects.

Achado da microverificacao que motiva este desenho: o lock inicial no
WorkItem (find_by_key(for_update=True)) NAO serializa o fluxo inteiro
-- transition_status() (ready->in_progress) commita internamente
(work_service.py:_mutate(), self.db.commit()), liberando esse lock
antes do efeito de negocio. O ponto de contencao real e definitivo e o
mesmo dos dois corredores: SELECT Account ... FOR UPDATE. Por isso T2
nao retorna duplicate=True (esse caminho so se aplica a uma terceira
chamada feita DEPOIS do receipt existir) -- T2 atravessa toda a
revalidacao legitima, chega ao mesmo lock de Account, bloqueia
genuinamente nele, e ao ser liberado le o estado fresco (atrasado) e
falha fechado com ApprovalConsumptionConflictError.

Mecanismo de sincronizacao (exclusivamente test-only, zero producao):
  - Cada worker (T1/T2/watcher) cria/usa/fecha sua PROPRIA SessionLocal()
    dentro da propria thread.
  - Connection.info["role"] identifica T1 dentro do hook compartilhado
    do Engine (after_cursor_execute), que so pausa a execucao de T1 --
    nunca a de T2 -- exatamente apos o SELECT ... FOR UPDATE em
    accounts, ainda dentro da mesma transacao/lock.
  - T2 so e iniciada DEPOIS que o hook confirma que T1 detem o lock --
    elimina a possibilidade de T2 vencer a corrida acidentalmente.
  - Um watcher com conexao independente (autocommit) faz polling em
    pg_stat_activity confirmando wait_event_type='Lock' no PID real de
    T2 -- prova causal de bloqueio genuino, nao inferencia por tempo.
    Se o watcher nao observar o bloqueio dentro do timeout de
    seguranca, o teste FALHA (nunca degrada para prova por tempo).
  - T2 aplica SET LOCAL lock_timeout='30s' -- watchdog de seguranca
    contra o lock_timeout de producao (5000ms), nunca mecanismo de
    sincronizacao.
"""

import os
import threading
import time
from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

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
from app.models.work import WorkEvent
from app.services.human_account_mark_overdue_execution_service import (
    HumanAccountMarkOverdueExecutionService,
)
from app.core.approval_errors import ApprovalConsumptionConflictError
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)


AUTHENTICATED_EMAIL = "developer.test@example.com"
APPROVER_EMAIL = "approver.mark-overdue.concurrency@example.com"
APPROVER_PASSWORD = "Senha-Aprovador-Concorrencia-123!"

WATCHER_TIMEOUT_S = 5.0
WATCHER_POLL_INTERVAL_S = 0.02
HOOK_RELEASE_TIMEOUT_S = 10.0


def _create_approver(db_session: Session) -> User:
    user = User(
        name="Aprovador Concorrencia N3",
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
        cliente="Cliente Concorrencia N3",
        email=email,
        whatsapp=None,
        valor=1200,
        vencimento=due_date,
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _setup_approved_episode(
    client: TestClient, db_session: Session, *, due_date: date
) -> tuple[Account, int]:
    register_account_mark_overdue_skill()
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.concurrency@example.com",
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
    return account, request_id


def _tagged_session(role: str) -> Session:
    session = SessionLocal()
    session.connection().info["role"] = role
    return session


def _backend_pid(session: Session) -> int:
    return session.execute(text("SELECT pg_backend_pid()")).scalar_one()


def _watcher_confirms_blocked(pid: int) -> bool:
    """
    Conexao independente, autocommit, exclusiva para polling -- nunca
    participa da transacao critica. Confirma objetivamente que `pid`
    esta em wait_event_type='Lock', nao apenas lento.
    """

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


def test_concurrent_execution_produces_single_business_effect(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account, _request_id = _setup_approved_episode(
        client, db_session, due_date=due_date
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
    before_receipts = db_session.execute(
        select(func.count(WorkEvent.id)).where(
            WorkEvent.event_type == "system_note"
        )
    ).scalar_one()

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
                        "release_t1 nao foi sinalizado a tempo -- "
                        "possivel falha do watcher/coordenador."
                    )

    def before_hook(conn, cursor, statement, parameters, context, executemany):
        if conn.info.get("role") != "T1":
            return
        upper = statement.upper()
        if "FOR UPDATE" in upper and "WORK_ITEMS" in upper:
            # Achado do diagnostico: apos commitar o efeito de negocio,
            # execute() reaquire o lock do WorkItem para a transicao
            # final in_progress->completed (fresh_work =
            # self.works.lock_by_id(...)). Enquanto T2 ainda detem o
            # lock do WorkItem (adquirido no inicio de sua propria
            # chamada, retido durante todo o bloqueio no Account), essa
            # segunda tentativa de T1 bloquearia no proprio Postgres --
            # e um hook em after_cursor_execute NUNCA dispararia nesse
            # caso, porque a instrucao so retorna depois de resolvida
            # (ou apos deadlock). Por isso pausamos aqui em
            # before_cursor_execute -- ANTES de a instrucao ser
            # sequer enviada -- garantindo que T1 nunca chega a
            # competir de verdade pelo lock do WorkItem antes de T2 ja
            # o ter liberado (via seu proprio rollback).
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
                        "release_t1_workitem_relock nao foi sinalizado "
                        "a tempo -- possivel falha do coordenador."
                    )

    event.listen(engine, "after_cursor_execute", after_hook)
    event.listen(engine, "before_cursor_execute", before_hook)

    t1_session = _tagged_session("T1")
    t2_session: Session | None = None

    try:
        pid_t1 = _backend_pid(t1_session)

        def t1_worker() -> None:
            service = HumanAccountMarkOverdueExecutionService(t1_session)
            try:
                result = service.execute(
                    account_id=account.id,
                    due_date=due_date,
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
            "T1 nao alcancou/adquiriu o Account FOR UPDATE a tempo."
        )

        t2_session = _tagged_session("T2")
        pid_t2 = _backend_pid(t2_session)
        assert pid_t1 != pid_t2, (
            "PIDs de T1/T2 devem ser distintos -- conexoes independentes."
        )
        t2_session.execute(text("SET LOCAL lock_timeout = '30s'"))

        def t2_worker() -> None:
            service = HumanAccountMarkOverdueExecutionService(t2_session)
            try:
                result = service.execute(
                    account_id=account.id,
                    due_date=due_date,
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
            "T2 dentro do timeout de seguranca -- concorrencia real nao "
            "foi comprovada; o teste falha em vez de inferir por tempo."
        )

        release_t1.set()

        reached_relock = t1_wants_workitem_relock.wait(
            timeout=HOOK_RELEASE_TIMEOUT_S
        )
        assert reached_relock, (
            "T1 nao alcancou a re-tentativa de lock do WorkItem "
            "(transicao para completed) a tempo."
        )

        # So agora deixamos T2 terminar (ela ja estava desbloqueada no
        # Account desde o commit de T1; o que faltava era o tempo para
        # ler o estado fresco, falhar fechado e liberar seu proprio
        # lock do WorkItem via rollback).
        t2_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t2_thread.is_alive(), "T2 nao concluiu a tempo."

        release_t1_workitem_relock.set()

        t1_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t1_thread.is_alive(), "T1 nao concluiu a tempo."
    finally:
        event.remove(engine, "after_cursor_execute", after_hook)
        event.remove(engine, "before_cursor_execute", before_hook)
        t1_session.close()
        if t2_session is not None:
            t2_session.close()

    assert "t1_error" not in outcome, (
        f"T1 nao deveria falhar: {outcome.get('t1_error')}"
    )
    t1_result = outcome["t1_result"]
    assert t1_result.duplicate is False
    assert t1_result.output["new_status"] == "atrasado"

    assert "t2_error" in outcome, (
        "T2 deveria falhar apos observar o estado fresco pos-T1."
    )
    assert isinstance(
        outcome["t2_error"], ApprovalConsumptionConflictError
    )

    db_session.expire_all()
    reloaded_account = db_session.get(Account, account.id)
    assert reloaded_account.status == "atrasado"

    after_consumptions = db_session.execute(
        select(func.count(ApprovalConsumption.id))
    ).scalar_one()
    after_invocations = db_session.execute(
        select(func.count(SkillInvocation.id))
    ).scalar_one()
    after_events = db_session.execute(
        select(func.count(AccountEvent.id))
    ).scalar_one()
    after_receipts = db_session.execute(
        select(func.count(WorkEvent.id)).where(
            WorkEvent.event_type == "system_note"
        )
    ).scalar_one()

    assert after_consumptions == before_consumptions + 1
    assert after_invocations == before_invocations + 1
    assert after_events == before_events + 1
    assert after_receipts == before_receipts + 1
