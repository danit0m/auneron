"""
DW-6.4B -- Concorrencia real na declaracao de recommendation_snapshot_id.

Prova, sob concorrencia genuina (duas conexoes/threads Postgres
efetivamente sobrepostas, nao apenas duas chamadas sequenciais), a
propriedade que o Design Freeze exige: quando duas materializacoes do
MESMO episodio (mesma work_key) competem com snapshots DIFERENTES (S1
e S2, ambos validos, ambos recomendando account.mark_overdue para a
mesma conta/episodio), o resultado e sempre:

    exatamente um WorkItem
    exatamente uma ApprovalRequest
    exatamente uma associacao persistida (S1 XOR S2 -- nunca ambos,
      nunca nenhum, nunca sobrescrita)
    a chamada correspondente ao vencedor: sucesso
    a chamada correspondente ao perdedor: HumanMarkOverdueSnapshot-
      ConflictError tipado (409 na camada HTTP)

O teste NAO fixa qual thread vence -- aceita S1 ou S2 como vencedor e
verifica as invariantes resultantes em qualquer dos dois casos. Mesmo
mecanismo de sincronizacao (test-only, zero producao) ja usado em
test_human_account_mark_overdue_execution_concurrency.py: hook em
after_cursor_execute pausando T1 logo apos seu INSERT em work_items
(ainda sem commit), watcher independente confirmando via
pg_stat_activity que T2 esta genuinamente bloqueado na mesma constraint
antes de liberar T1.
"""

import threading
import time
from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.authentication import AuthenticatedSession
from app.database.database import SessionLocal
from app.database.database import engine
from app.models.account import Account
from app.models.approval import ApprovalRequest
from app.models.user import User
from app.models.work import WorkItem
from app.services.human_account_mark_overdue_materialization_service import (
    HumanAccountMarkOverdueMaterializationService,
    HumanMarkOverdueSnapshotConflictError,
)
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)


AUTHENTICATED_EMAIL = "developer.test@example.com"

WATCHER_TIMEOUT_S = 5.0
WATCHER_POLL_INTERVAL_S = 0.02
HOOK_RELEASE_TIMEOUT_S = 10.0


class _FakeSession:
    """Duck-typed AuthSession stand-in: is_session_elevated() so le
    .elevated_until, e account.mark_overdue e mutating (nao exige
    sessao elevada) -- mesmo padrao ja usado em
    test_human_account_mark_overdue_materialization.py."""

    elevated_until = None


def _authenticated_user(db_session: Session) -> User:
    return (
        db_session.query(User)
        .filter(User.email == AUTHENTICATED_EMAIL)
        .one()
    )


def _make_overdue_account(
    db_session: Session, *, due_date: date, email: str
) -> Account:
    account = Account(
        cliente="Cliente Materialize Race",
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


def _create_snapshot(
    client: TestClient, *, account_id: int, due_date: date
) -> int:
    response = client.get(
        "/recommendations/next-best-action/accounts/"
        f"{account_id}/episodes/{due_date.isoformat()}"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert (
        "account.mark_overdue"
        in payload["decision"]["selected_actions"]
    )
    return payload["recommendation_snapshot_id"]


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


def test_concurrent_materialize_with_different_snapshots_never_overwrites_association(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.materialize-race@example.com",
    )

    # Ambos os snapshots sao gerados ANTES da corrida -- cada GET real
    # ja materializa uma ocorrencia propria e distinta (DW-6.4A: nunca
    # deduplicada), exatamente o cenario que o Freeze pediu.
    snapshot_1 = _create_snapshot(
        client, account_id=account.id, due_date=due_date
    )
    snapshot_2 = _create_snapshot(
        client, account_id=account.id, due_date=due_date
    )
    assert snapshot_1 != snapshot_2

    authority = _authenticated_user(db_session)

    t1_has_inserted = threading.Event()
    release_t1 = threading.Event()
    outcome: dict[str, object] = {}

    def after_hook(
        conn, cursor, statement, parameters, context, executemany
    ):
        if conn.info.get("role") != "T1":
            return
        upper = statement.upper()
        if "INSERT INTO" in upper and "WORK_ITEMS" in upper:
            if not t1_has_inserted.is_set():
                t1_has_inserted.set()
                released = release_t1.wait(
                    timeout=HOOK_RELEASE_TIMEOUT_S
                )
                if not released:
                    raise AssertionError(
                        "release_t1 nao foi sinalizado a tempo."
                    )

    event.listen(engine, "after_cursor_execute", after_hook)

    t1_session = _tagged_session("T1")
    t2_session: Session | None = None

    try:
        pid_t1 = _backend_pid(t1_session)

        def t1_worker() -> None:
            service = HumanAccountMarkOverdueMaterializationService(
                t1_session
            )
            try:
                result = service.materialize(
                    account=account,
                    due_date=due_date,
                    authenticated=AuthenticatedSession(
                        user=authority, session=_FakeSession()
                    ),
                    recommendation_snapshot_id=snapshot_1,
                )
                outcome["t1_result"] = result
            except Exception as error:  # noqa: BLE001
                t1_session.rollback()
                outcome["t1_error"] = error

        t1_thread = threading.Thread(target=t1_worker)
        t1_thread.start()

        acquired = t1_has_inserted.wait(
            timeout=HOOK_RELEASE_TIMEOUT_S
        )
        assert acquired, (
            "T1 nao alcancou o INSERT de work_items a tempo."
        )

        t2_session = _tagged_session("T2")
        pid_t2 = _backend_pid(t2_session)
        assert pid_t1 != pid_t2
        t2_session.execute(
            text("SET LOCAL lock_timeout = '30s'")
        )

        def t2_worker() -> None:
            service = HumanAccountMarkOverdueMaterializationService(
                t2_session
            )
            try:
                result = service.materialize(
                    account=account,
                    due_date=due_date,
                    authenticated=AuthenticatedSession(
                        user=authority, session=_FakeSession()
                    ),
                    recommendation_snapshot_id=snapshot_2,
                )
                outcome["t2_result"] = result
            except Exception as error:  # noqa: BLE001
                t2_session.rollback()
                outcome["t2_error"] = error

        t2_thread = threading.Thread(target=t2_worker)
        t2_thread.start()

        blocked = _watcher_confirms_blocked(pid_t2)
        assert blocked, (
            "Watcher nao observou wait_event_type='Lock' para T2 -- "
            "concorrencia real na criacao do WorkItem nao foi "
            "comprovada; o teste falha em vez de inferir por tempo."
        )

        release_t1.set()

        t1_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t1_thread.is_alive(), "T1 nao concluiu a tempo."
        t2_thread.join(timeout=HOOK_RELEASE_TIMEOUT_S)
        assert not t2_thread.is_alive(), "T2 nao concluiu a tempo."
    finally:
        event.remove(engine, "after_cursor_execute", after_hook)
        t1_session.close()
        if t2_session is not None:
            t2_session.close()

    # Exatamente um dos dois teve sucesso real; o outro recebeu o
    # conflito tipado -- nao fixamos qual, aceitamos qualquer um.
    t1_succeeded = "t1_result" in outcome
    t2_succeeded = "t2_result" in outcome
    assert t1_succeeded != t2_succeeded, (
        "Exatamente uma das duas chamadas deveria ter sucesso; "
        f"outcome={outcome}"
    )

    if t1_succeeded:
        winner_result = outcome["t1_result"]
        winner_snapshot = snapshot_1
        loser_error = outcome.get("t2_error")
    else:
        winner_result = outcome["t2_result"]
        winner_snapshot = snapshot_2
        loser_error = outcome.get("t1_error")

    assert winner_result.created is True
    assert winner_result.duplicate is False
    assert isinstance(
        loser_error, HumanMarkOverdueSnapshotConflictError
    ), (
        f"perdedor deveria falhar com conflito tipado, obteve: "
        f"{loser_error!r}"
    )

    db_session.expire_all()
    work_items = (
        db_session.query(WorkItem)
        .filter(WorkItem.account_id == account.id)
        .all()
    )
    assert len(work_items) == 1
    assert (
        work_items[0].context_data["recommendation_snapshot_id"]
        == winner_snapshot
    )

    approval_requests = (
        db_session.query(ApprovalRequest)
        .filter(ApprovalRequest.target_account_id == account.id)
        .all()
    )
    assert len(approval_requests) == 1
