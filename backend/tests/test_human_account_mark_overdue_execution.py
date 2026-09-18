"""
P1.2B -- Human Account Mark-Overdue Execution Service V1: corredor
humano de account.mark_overdue. Cobre o fluxo completo materialize ->
decide -> execute, dupla revalidação imediatamente antes da mutação,
rejeição (sem mutação, WorkItem encerrado coerentemente), idempotência
via recibo (WorkEvent), e a identidade de auditoria congelada:
ApprovalConsumption.consumer_actor_type == "system" (nunca "agent",
nunca "user" -- CHECK constraint exclui "user"), autoridade humana
real em authority_user_id/authority_reference/authority_role, e
AccountEvent.actor_type == "user" com o id do humano real. Nenhum ator
"agent:..." é fabricado para atravessar os corredores 25M/25O/
GovernedSkillExecutionService, que permanecem intocados.
"""

import os
from datetime import date
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.security import API_KEY_HEADER_NAME
from app.main import app
from app.models.account import Account
from app.models.account_event import AccountEvent
from app.models.approval import ApprovalConsumption
from app.models.user import User
from app.models.work import WorkItem
from scripts.register_account_mark_overdue_skill import (
    main as register_account_mark_overdue_skill,
)


AUTHENTICATED_EMAIL = "developer.test@example.com"

# account.mark_overdue e risk_level="high" com requester humano --
# ApprovalService.decide() exige (deliberadamente) que decisor e
# solicitante sejam pessoas diferentes para acoes de risco alto/
# critico (separacao de deveres). Por isso todo teste que precisa de
# uma decisao "approved" real usa um segundo usuario/cliente para
# decidir -- nunca o mesmo client autenticado que materializou.
APPROVER_EMAIL = "approver.mark-overdue@example.com"
APPROVER_PASSWORD = "Senha-Aprovador-Teste-123!"


def _create_approver(db_session: Session) -> User:
    user = User(
        name="Aprovador Teste P1.2B",
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
        json={
            "email": APPROVER_EMAIL,
            "password": APPROVER_PASSWORD,
        },
    )
    assert login.status_code == 200, login.text
    return test_client


def _current_user(db_session: Session) -> User:
    return (
        db_session.query(User)
        .filter(User.email == AUTHENTICATED_EMAIL)
        .one()
    )


def _set_role(db_session: Session, role: str) -> User:
    user = _current_user(db_session)
    user.role = role
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_overdue_account(
    db_session: Session,
    *,
    due_date: date,
    email: str = "cliente.mark-overdue.execute@example.com",
) -> Account:
    account = Account(
        cliente="Cliente Mark Overdue Execution Teste",
        email=email,
        whatsapp=None,
        valor=1100,
        vencimento=due_date,
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _materialize_url(account_id: int, due_date: date) -> str:
    return (
        "/recommendations/mark-overdue/accounts/"
        f"{account_id}/episodes/{due_date.isoformat()}/materialize"
    )


def _execute_url(account_id: int, due_date: date) -> str:
    return (
        "/recommendations/mark-overdue/accounts/"
        f"{account_id}/episodes/{due_date.isoformat()}/execute"
    )


def _decide_url(request_id: int) -> str:
    return f"/approvals/{request_id}/decision"


def _materialize(
    client: TestClient, account_id: int, due_date: date
):
    response = client.post(_materialize_url(account_id, due_date))
    assert response.status_code == 201
    return response.json()


def _decide(
    approver_client: TestClient, request_id: int, decision: str
):
    response = approver_client.post(
        _decide_url(request_id), json={"decision": decision}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _setup_approved_episode(
    client: TestClient, db_session: Session, *, due_date: date, email: str
):
    register_account_mark_overdue_skill()
    account = _make_overdue_account(
        db_session, due_date=due_date, email=email
    )
    materialization = _materialize(client, account.id, due_date)
    request_id = materialization["approval_request"]["request_id"]
    approver = _approver_client(db_session)
    _decide(approver, request_id, "approved")
    return account, request_id


def test_execute_happy_path_mutates_account_and_preserves_human_identity(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account, request_id = _setup_approved_episode(
        client,
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.exec-happy@example.com",
    )
    authority = _current_user(db_session)

    response = client.post(_execute_url(account.id, due_date))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["duplicate"] is False
    assert payload["approval_request_id"] == request_id
    assert payload["invocation_status"] == "succeeded"
    assert payload["output"]["new_status"] == "atrasado"

    db_session.expire_all()
    reloaded_account = db_session.get(Account, account.id)
    assert reloaded_account.status == "atrasado"

    consumption = (
        db_session.query(ApprovalConsumption)
        .filter(
            ApprovalConsumption.approval_request_id == request_id
        )
        .one()
    )
    assert consumption.consumer_actor_type == "system"
    assert consumption.consumer_reference == (
        "system:human_account_mark_overdue_execution"
    )
    assert consumption.authority_user_id == authority.id
    assert (
        consumption.authority_reference == f"user:{authority.id}"
    )
    assert consumption.authority_role == authority.role

    account_event = (
        db_session.query(AccountEvent)
        .filter(AccountEvent.account_id == account.id)
        .one()
    )
    assert account_event.actor_type == "user"
    assert account_event.actor_user_id == authority.id
    assert (
        account_event.actor_reference == f"user:{authority.id}"
    )
    assert account_event.previous_status == "aberto"
    assert account_event.new_status == "atrasado"

    work_item = db_session.execute(
        text(
            "SELECT status FROM work_items WHERE work_key = :key"
        ),
        {
            "key": (
                f"account_mark_overdue:v1:{account.id}:"
                f"{due_date.isoformat()}"
            )
        },
    ).scalar_one()
    assert work_item == "completed"


def test_execute_second_call_is_idempotent(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=9)
    account, _request_id = _setup_approved_episode(
        client,
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.exec-idempotent@example.com",
    )

    first = client.post(_execute_url(account.id, due_date))
    assert first.status_code == 200
    assert first.json()["duplicate"] is False

    second = client.post(_execute_url(account.id, due_date))
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert (
        second.json()["invocation_id"]
        == first.json()["invocation_id"]
    )

    consumption_count = db_session.execute(
        text("SELECT COUNT(*) FROM approval_consumptions")
    ).scalar_one()
    assert consumption_count == 1


def test_execute_pending_approval_is_rejected_with_no_mutation(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=6)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.exec-pending@example.com",
    )
    _materialize(client, account.id, due_date)

    response = client.post(_execute_url(account.id, due_date))

    assert response.status_code == 409
    db_session.expire_all()
    reloaded = db_session.get(Account, account.id)
    assert reloaded.status == "aberto"


def test_execute_rejected_approval_cancels_work_item_without_mutation(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=7)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.exec-rejected@example.com",
    )
    materialization = _materialize(client, account.id, due_date)
    request_id = materialization["approval_request"]["request_id"]
    work_item_id = materialization["work_item"]["id"]
    approver = _approver_client(db_session)
    _decide(approver, request_id, "rejected")

    response = client.post(_execute_url(account.id, due_date))

    assert response.status_code == 409
    db_session.expire_all()
    reloaded_account = db_session.get(Account, account.id)
    assert reloaded_account.status == "aberto"

    reloaded_work_item = db_session.get(WorkItem, work_item_id)
    assert reloaded_work_item.status == "cancelled"

    consumption_count = db_session.execute(
        text("SELECT COUNT(*) FROM approval_consumptions")
    ).scalar_one()
    assert consumption_count == 0


def test_execute_returns_404_when_never_materialized(
    client: TestClient,
    db_session: Session,
) -> None:
    register_account_mark_overdue_skill()
    due_date = date.today() - timedelta(days=3)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.exec-never-materialized@"
        "example.com",
    )

    response = client.post(_execute_url(account.id, due_date))

    assert response.status_code == 404


def test_execute_revalidates_account_state_immediately_before_mutation(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    A aprovação aprovada não congela para sempre a elegibilidade --
    se a conta foi paga entre a aprovação e a execução, a revalidação
    imediatamente antes da mutação (sob FOR UPDATE) deve bloquear,
    fail-closed, sem marcar a conta como atrasada.
    """

    due_date = date.today() - timedelta(days=10)
    account, _request_id = _setup_approved_episode(
        client,
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.exec-stale@example.com",
    )

    stale_account = db_session.get(Account, account.id)
    stale_account.status = "pago"
    db_session.commit()

    response = client.post(_execute_url(account.id, due_date))

    assert response.status_code == 409
    db_session.expire_all()
    reloaded = db_session.get(Account, account.id)
    assert reloaded.status == "pago"


def test_execute_requires_skill_execute_permission(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account, _request_id = _setup_approved_episode(
        client,
        db_session,
        due_date=due_date,
        email="cliente.mark-overdue.exec-viewer@example.com",
    )

    _set_role(db_session, "viewer")

    response = client.post(_execute_url(account.id, due_date))

    assert response.status_code == 403
    db_session.expire_all()
    reloaded = db_session.get(Account, account.id)
    assert reloaded.status == "aberto"


def test_human_execution_service_never_impersonates_agent() -> None:
    """
    Contrato de fonte: o executor humano nunca fabrica
    actor_reference="agent:..." nem chama nenhuma das peças
    agent-only protegidas pelo Architecture Freeze do P1.2B.
    """

    source = Path(
        "app/services/human_account_mark_overdue_execution_service.py"
    ).read_text(encoding="utf-8")

    assert 'actor_reference="agent:' not in source
    assert "startswith(\"agent:\")" not in source
    assert (
        "from app.services.governed_skill_execution import"
        not in source
    )
    assert "configure_with_existing_approval" not in source
    assert (
        "from app.services.account_mark_overdue_execution_service "
        "import" not in source
    )
    assert 'CONSUMER_ACTOR_TYPE = "system"' in source
    assert "self.db.commit()" in source
