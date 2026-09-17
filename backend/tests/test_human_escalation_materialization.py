"""
PR-6A -- Human Escalation Materialization Service V1: corredor humano
de escalate_to_human. Cobre RBAC (work:create, separado de
approval:read), staleness (eligibility recalculada no servidor,
nunca confia em snapshot do cliente), idempotencia (mesmo ator, ator
diferente, e o caso TERMINAL-DUPLICATE que motivou a guarda
fail-closed), e proveniencia (origin_type/origin_reference).
"""

from datetime import date
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.user import User
from app.models.work import WorkItem
from app.services.work_service import WorkActor
from app.services.work_service import WorkManagerService


AUTHENTICATED_EMAIL = "developer.test@example.com"


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
    email: str = "cliente.escalation.materialize@example.com",
) -> Account:
    account = Account(
        cliente="Cliente Escalation Materialization Teste",
        email=email,
        whatsapp=None,
        valor=750,
        vencimento=due_date,
        status="aberto",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _materialize_url(account_id: int, due_date: date) -> str:
    return (
        "/recommendations/human-escalation/accounts/"
        f"{account_id}/episodes/{due_date.isoformat()}/materialize"
    )


def _work_item_count(db_session: Session) -> int:
    return db_session.execute(
        text("SELECT COUNT(*) FROM work_items")
    ).scalar_one()


def test_materialize_creates_work_item_for_eligible_episode(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(db_session, due_date=due_date)

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 201
    payload = response.json()

    assert payload["created"] is True
    assert payload["duplicate"] is False

    work_item = payload["work_item"]
    assert work_item["work_key"] == (
        f"human_escalation:v1:{account.id}:{due_date.isoformat()}"
    )
    assert work_item["scope"]["type"] == "account"
    assert work_item["scope"]["account_id"] == account.id
    assert work_item["work_type"] == "task"
    assert work_item["status"] == "backlog"


def test_materialize_second_call_same_actor_is_idempotent(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=12)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.escalation.same-actor@example.com",
    )

    first = client.post(_materialize_url(account.id, due_date))
    assert first.status_code == 201
    first_id = first.json()["work_item"]["id"]

    second = client.post(_materialize_url(account.id, due_date))
    assert second.status_code == 200
    payload = second.json()

    assert payload["created"] is False
    assert payload["duplicate"] is True
    assert payload["work_item"]["id"] == first_id

    assert _work_item_count(db_session) == 1


def test_materialize_ineligible_account_paid_creates_nothing(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=5)
    account = Account(
        cliente="Cliente Escalation Pago",
        email="cliente.escalation.pago@example.com",
        whatsapp=None,
        valor=750,
        vencimento=due_date,
        status="pago",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    before = _work_item_count(db_session)

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 409
    assert "account_paid" in response.json()["detail"]
    assert _work_item_count(db_session) == before


def test_materialize_ineligible_lifecycle_not_overdue_creates_nothing(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() + timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.escalation.futuro@example.com",
    )

    before = _work_item_count(db_session)

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 409
    assert "lifecycle_not_overdue" in response.json()["detail"]
    assert _work_item_count(db_session) == before


def test_materialize_requires_work_create_permission(
    client: TestClient,
    db_session: Session,
) -> None:
    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.escalation.viewer@example.com",
    )

    _set_role(db_session, "viewer")

    response = client.post(_materialize_url(account.id, due_date))

    assert response.status_code == 403


def test_materialize_allowed_without_approval_read(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    analyst tem work:create mas NAO tem approval:read -- prova a
    separacao congelada: ver a recomendacao (GET, approval:read) e
    materializar trabalho a partir dela (POST, work:create) sao
    autoridades distintas.
    """

    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.escalation.analyst@example.com",
    )

    _set_role(db_session, "analyst")

    get_response = client.get(
        "/recommendations/human-escalation/accounts/"
        f"{account.id}/episodes/{due_date.isoformat()}"
    )
    assert get_response.status_code == 403

    post_response = client.post(
        _materialize_url(account.id, due_date)
    )
    assert post_response.status_code == 201


def test_materialize_returns_404_for_nonexistent_account(
    client: TestClient,
) -> None:
    due_date = date.today().isoformat()

    response = client.post(
        "/recommendations/human-escalation/accounts/999999/"
        f"episodes/{due_date}/materialize"
    )

    assert response.status_code == 404


def test_materialize_origin_is_server_derived_never_from_client(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    origin_type/origin_reference nunca podem vir do que o cliente
    envia -- o endpoint nao aceita corpo algum, entao a unica fonte
    possivel e o servidor recalculando a eligibility.
    """

    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.escalation.origin@example.com",
    )

    response = client.post(
        _materialize_url(account.id, due_date),
        json={"origin_type": "agent", "title": "forjado"},
    )

    assert response.status_code == 201
    work_item = response.json()["work_item"]
    assert work_item["title"] != "forjado"


def test_materialize_terminal_work_item_returns_409_and_creates_nothing(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    TERMINAL-DUPLICATE: depois que o WorkItem canonico de um episodio
    chega a estado terminal e a eligibility volta a ser "eligible"
    para o mesmo episodio, uma nova tentativa de materializacao deve
    falhar fechado (409) -- nunca criar um segundo WorkItem sob a
    mesma work_key, e nunca devolver o item terminal como se fosse
    duplicate=True (sucesso operacional).
    """

    due_date = date.today() - timedelta(days=10)
    account = _make_overdue_account(
        db_session,
        due_date=due_date,
        email="cliente.escalation.terminal@example.com",
    )

    first = client.post(_materialize_url(account.id, due_date))
    assert first.status_code == 201
    work_item_id = first.json()["work_item"]["id"]

    user = _current_user(db_session)
    work_service = WorkManagerService(db_session)
    work_service.transition_status(
        work_item_id,
        expected_version=1,
        actor=WorkActor(
            actor_type="user",
            actor_reference=f"user:{user.id}",
            actor_user_id=user.id,
        ),
        status="cancelled",
        reason="Teste TERMINAL-DUPLICATE -- encerrado sem "
        "resolver o recebivel.",
    )

    before = _work_item_count(db_session)

    second = client.post(_materialize_url(account.id, due_date))

    assert second.status_code == 409
    body = second.json()
    assert "duplicate" not in body or body.get("duplicate") is not True
    assert _work_item_count(db_session) == before

    reloaded = db_session.get(WorkItem, work_item_id)
    assert reloaded.status == "cancelled"
