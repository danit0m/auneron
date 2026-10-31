"""
AREA2 TASK041 -- DELETE /accounts/{id} nao apaga historico governado.

account_events e account_vencimento_changes tem FK ON DELETE CASCADE e
approval_requests guarda target_account_id sem FK: um hard delete apagaria
ou orfanaria a cadeia governada. A rota agora segura o lock de identidade,
trava a linha da Account (FOR UPDATE) e devolve 409 antes de qualquer
delete/commit se houver historico. Conta sem historico continua 204;
conta inexistente continua 404.

Todo historico e criado pelos caminhos reais (corredor mark_paid via
HTTP, PUT de vencimento, solicitacao/decisao de aprovacao via HTTP); nada
de AccountEvent fabricado. O teste de concorrencia usa PostgreSQL real e o
servico governado real; o unico controle e segurar o commit do executor
ate o DELETE estar bloqueado na linha.

Escopo: protege historico existente. NAO afirma auditoria de CRUD manual
(POST / edicao de identidade / upload legado continuam sem trilha propria).
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import true
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.core.authentication import hash_password
from app.core.security import API_KEY_HEADER_NAME
from app.database.database import engine
from app.main import app
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.account_vencimento_change import AccountVencimentoChange
from app.models.approval import ApprovalConsumption
from app.models.approval import ApprovalDecision
from app.models.approval import ApprovalRequest
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
APPROVER_EMAIL = "approver.delete-guard@example.com"
APPROVER_PASSWORD = "Senha-Aprovador-DeleteGuard-123!"
HISTORY_CONFLICT = "Conta possui histórico governado e não pode ser excluída."


def _create_account(client: TestClient, marker: str) -> int:
    response = client.post(
        "/accounts/",
        json={
            "cliente": f"Cliente Delete Guard {marker}",
            "email": f"delete.guard.{marker}@outlook.com",
            "valor": 1234.56,
            "vencimento": "2026-11-30",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _approver_client(db_session: Session) -> TestClient:
    db_session.add(
        User(
            name="Aprovador Delete Guard",
            email=APPROVER_EMAIL,
            password_hash=hash_password(APPROVER_PASSWORD),
            role="manager",
            active=True,
        )
    )
    db_session.commit()
    approver = TestClient(app)
    approver.headers.update({API_KEY_HEADER_NAME: os.environ["API_KEY"]})
    login = approver.post(
        "/auth/login",
        json={"email": APPROVER_EMAIL, "password": APPROVER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    return approver


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


def _request_mark_paid(
    client: TestClient, db_session: Session, account_id: int, key: str
) -> int:
    version_id = _mark_paid_version_id(db_session)
    created = client.post(
        f"/approvals/skill-executions/{version_id}",
        json={
            "input_payload": {
                "account_id": account_id,
                "expected_status": "aberto",
            }
        },
        headers={"Idempotency-Key": key},
    )
    assert created.status_code == 201, created.text
    return created.json()["request"]["request_id"]


def _decide(approver: TestClient, request_id: int, decision: str) -> None:
    response = approver.post(
        f"/approvals/{request_id}/decision",
        json={"decision": decision},
    )
    assert response.status_code == 200, response.text


def _governed_rows(db_session: Session, account_id: int) -> dict:
    db_session.expire_all()

    def count(model, *criteria):
        return db_session.execute(
            select(func.count()).select_from(model).filter(true(), *criteria)
        ).scalar_one()

    return {
        "account": count(Account, Account.id == account_id),
        "events": count(
            AccountEvent, AccountEvent.account_id == account_id
        ),
        "vencimento_changes": count(
            AccountVencimentoChange,
            AccountVencimentoChange.account_id == account_id,
        ),
        "approval_requests": count(
            ApprovalRequest,
            ApprovalRequest.target_account_id == account_id,
        ),
        "decisions": count(ApprovalDecision),
        "consumptions": count(ApprovalConsumption),
        "invocations": count(SkillInvocation),
    }


def _assert_delete_blocked(
    client: TestClient, db_session: Session, account_id: int
) -> None:
    before = _governed_rows(db_session, account_id)
    before_status = db_session.get(Account, account_id).status

    response = client.delete(f"/accounts/{account_id}")

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": HISTORY_CONFLICT}
    assert _governed_rows(db_session, account_id) == before
    assert db_session.get(Account, account_id).status == before_status


def test_delete_account_without_history_returns_204(
    client: TestClient,
    db_session: Session,
) -> None:
    account_id = _create_account(client, "sem-historico")
    assert _governed_rows(db_session, account_id)["account"] == 1

    response = client.delete(f"/accounts/{account_id}")

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert _governed_rows(db_session, account_id)["account"] == 0
    assert client.get(f"/accounts/{account_id}").status_code == 404


def test_delete_missing_account_returns_404(client: TestClient) -> None:
    response = client.delete("/accounts/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Conta não encontrada."}


def test_delete_blocked_by_governed_status_event(
    client: TestClient,
    db_session: Session,
) -> None:
    account_id = _create_account(client, "evento-governado")
    request_id = _request_mark_paid(
        client, db_session, account_id, "delete-guard-event-1"
    )
    _decide(_approver_client(db_session), request_id, "approved")
    executed = client.post(
        f"/accounts/{account_id}/execute-mark-paid",
        json={"approval_request_id": request_id, "expected_status": "aberto"},
    )
    assert executed.status_code == 200, executed.text
    rows = _governed_rows(db_session, account_id)
    assert rows["events"] == 1
    assert rows["consumptions"] == 1

    _assert_delete_blocked(client, db_session, account_id)
    assert db_session.get(Account, account_id).status == "pago"


def test_delete_blocked_by_vencimento_history(
    client: TestClient,
    db_session: Session,
) -> None:
    account_id = _create_account(client, "historico-vencimento")
    updated = client.put(
        f"/accounts/{account_id}",
        json={"vencimento": "2026-12-15"},
    )
    assert updated.status_code == 200, updated.text
    assert _governed_rows(db_session, account_id)["vencimento_changes"] == 1

    _assert_delete_blocked(client, db_session, account_id)


def test_delete_blocked_by_pending_approval(
    client: TestClient,
    db_session: Session,
) -> None:
    account_id = _create_account(client, "aprovacao-pendente")
    request_id = _request_mark_paid(
        client, db_session, account_id, "delete-guard-pending-1"
    )

    _assert_delete_blocked(client, db_session, account_id)
    request = db_session.get(ApprovalRequest, request_id)
    assert request.status == "pending"
    assert request.target_account_id == account_id


def test_delete_blocked_by_rejected_approval_chain(
    client: TestClient,
    db_session: Session,
) -> None:
    account_id = _create_account(client, "aprovacao-rejeitada")
    request_id = _request_mark_paid(
        client, db_session, account_id, "delete-guard-rejected-1"
    )
    _decide(_approver_client(db_session), request_id, "rejected")
    assert _governed_rows(db_session, account_id)["decisions"] == 1

    _assert_delete_blocked(client, db_session, account_id)
    assert db_session.get(ApprovalRequest, request_id).status == "rejected"


def _wait_for_lock_waiter(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            waiting = observer.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock' "
                    "AND pid <> pg_backend_pid()"
                )
            ).scalar_one()
            observer.rollback()
            if waiting >= 1:
                return True
            time.sleep(0.05)
    return False


def test_delete_waiting_on_governed_writer_sees_new_history(
    client: TestClient,
    db_session: Session,
) -> None:
    # REAL PostgreSQL. O executor governado segura a linha da Account
    # (FOR UPDATE) e para antes do commit; o DELETE tem de esperar nessa
    # linha e, depois do commit, enxergar o AccountEvent novo e responder
    # 409. Sem o guard o DELETE esperaria no proprio DELETE e o CASCADE
    # apagaria o evento recem-gravado (204).
    account_id = _create_account(client, "concorrencia")
    executor_id = (
        db_session.query(User).filter(User.email == AUTHENTICATED_EMAIL).one().id
    )
    request_id = _request_mark_paid(
        client, db_session, account_id, "delete-guard-race-1"
    )
    _decide(_approver_client(db_session), request_id, "approved")

    holding = threading.Event()
    release = threading.Event()

    class GatedCommitSession(Session):
        def commit(self):
            holding.set()
            if not release.wait(timeout=10):
                raise RuntimeError("synthetic gate was not released")
            super().commit()

    gated_factory = sessionmaker(bind=engine, class_=GatedCommitSession)

    def governed_writer():
        with gated_factory() as session:
            return AccountMarkPaidExecutionService(session).execute(
                account_id=account_id,
                approval_request_id=request_id,
                expected_status="aberto",
                authority_user_id=executor_id,
            )

    headers, cookies = dict(client.headers), dict(client.cookies)

    def manual_delete():
        concurrent = TestClient(app, raise_server_exceptions=False)
        try:
            concurrent.headers.update(headers)
            concurrent.cookies.update(cookies)
            return concurrent.delete(f"/accounts/{account_id}")
        finally:
            concurrent.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        writer = workers.submit(governed_writer)
        assert holding.wait(timeout=10)
        deleter = workers.submit(manual_delete)
        try:
            delete_was_blocked = _wait_for_lock_waiter(timeout=3)
        finally:
            release.set()
        result = writer.result(timeout=20)
        response = deleter.result(timeout=20)

    assert delete_was_blocked
    assert result.duplicate is False
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": HISTORY_CONFLICT}
    rows = _governed_rows(db_session, account_id)
    assert rows["account"] == 1
    assert rows["events"] == 1
    assert rows["consumptions"] == 1
    assert db_session.get(Account, account_id).status == "pago"
