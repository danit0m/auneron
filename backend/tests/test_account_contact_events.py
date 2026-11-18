"""
A5: contato e promessa de pagamento por titulo.

PostgreSQL real (auneron_test), dados sinteticos. Cobre as rotas
POST/GET /accounts/{id}/contact-events, a permissao account:contact, a
idempotencia (mesma chave: mesmo registro; conteudo diferente: 409), os
CHECKs do banco, o agregado em GET /accounts (sem N+1) e a regra de
promessa vencida com datas de fronteira.
"""

import os
from datetime import date
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.exc import DataError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.receivable_lifecycle import business_today
from app.core.security import API_KEY_HEADER_NAME
from app.database.database import engine
from app.main import app
from app.models.account import Account
from app.models.account_contact_event import AccountContactEvent
from app.models.user import User


PASSWORD = "Senha-Contato-Promessa-123!"


def _login_as(db_session: Session, email: str, role: str) -> TestClient:
    user = User(
        name=f"Usuario {email}",
        email=email,
        password_hash=hash_password(PASSWORD),
        role=role,
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    test_client = TestClient(app)
    test_client.headers.update({API_KEY_HEADER_NAME: os.environ["API_KEY"]})
    login = test_client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    test_client.user_id = user.id
    return test_client


def _manager(db_session: Session) -> TestClient:
    return _login_as(db_session, "gerente.contato@example.com", "manager")


def _make_account(
    db_session: Session,
    *,
    email: str = "contato.ok@example.com",
    status: str = "atrasado",
    vencimento: date | None = None,
) -> Account:
    account = Account(
        cliente="Cliente Contato Teste",
        email=email,
        whatsapp=None,
        valor=300,
        vencimento=vencimento or (business_today() - timedelta(days=10)),
        status=status,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _post(client, account_id, body, key="ce-1"):
    return client.post(
        f"/accounts/{account_id}/contact-events",
        json=body,
        headers={"Idempotency-Key": key},
    )


def _row(client, account_id):
    listed = client.get("/accounts/", params={"limit": 200})
    assert listed.status_code == 200, listed.text
    return next(r for r in listed.json() if r["id"] == account_id)


def _insert_event(db_session, account, event_type, promised_date=None, key=None, user_id=None):
    """Insercao direta para fixar datas de fronteira sem passar pela rota."""
    if user_id is None:
        user_id = db_session.execute(select(User.id).limit(1)).scalar_one()
    db_session.add(
        AccountContactEvent(
            account_id=account.id,
            event_type=event_type,
            promised_date=promised_date,
            actor_user_id=user_id,
            idempotency_key=key or f"direct-{event_type}-{promised_date}-{account.id}",
        )
    )
    db_session.commit()


# ------------------------------------------------------------- permissao


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_roles_without_account_contact_are_denied(db_session, role) -> None:
    account = _make_account(db_session)
    client = _login_as(db_session, f"{role}.contato@example.com", role)

    response = _post(client, account.id, {"event_type": "contato"})

    assert response.status_code == 403, response.text
    assert db_session.execute(select(func.count()).select_from(AccountContactEvent)).scalar_one() == 0


def test_unauthenticated_is_refused(unauthenticated_client, db_session) -> None:
    account = _make_account(db_session)
    response = _post(unauthenticated_client, account.id, {"event_type": "contato"})
    assert response.status_code in (401, 403)


@pytest.mark.parametrize("role", ["manager", "executive", "administrator", "developer"])
def test_roles_with_account_contact_can_register(db_session, role) -> None:
    account = _make_account(db_session)
    client = _login_as(db_session, f"{role}.contato@example.com", role)

    response = _post(client, account.id, {"event_type": "contato", "note": "ligou"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["event_type"] == "contato"
    assert body["note"] == "ligou"
    assert body["actor_user_id"] == client.user_id


# ----------------------------------------------------------- validacao


def test_unknown_account_is_404(db_session) -> None:
    client = _manager(db_session)
    assert _post(client, 999999, {"event_type": "contato"}).status_code == 404
    assert client.get("/accounts/999999/contact-events").status_code == 404


def test_promise_requires_a_date_and_contact_forbids_it(db_session) -> None:
    account = _make_account(db_session)
    client = _manager(db_session)

    assert _post(client, account.id, {"event_type": "promessa"}, "k1").status_code == 422
    assert _post(
        client,
        account.id,
        {"event_type": "contato", "promised_date": "2026-12-01"},
        "k2",
    ).status_code == 422
    assert _post(
        client, account.id, {"event_type": "contato", "note": "x" * 281}, "k3"
    ).status_code == 422
    assert _post(client, account.id, {"event_type": "invalido"}, "k4").status_code == 422
    missing_key = client.post(
        f"/accounts/{account.id}/contact-events", json={"event_type": "contato"}
    )
    assert missing_key.status_code == 422
    assert db_session.execute(select(func.count()).select_from(AccountContactEvent)).scalar_one() == 0


# ------------------------------------------------------------ idempotencia


def test_same_key_same_content_returns_the_same_record(db_session) -> None:
    account = _make_account(db_session)
    client = _manager(db_session)
    body = {"event_type": "promessa", "promised_date": "2030-01-10", "note": "paga dia 10"}

    first = _post(client, account.id, body, "idem-1")
    second = _post(client, account.id, body, "idem-1")

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    db_session.expire_all()
    assert db_session.execute(select(func.count()).select_from(AccountContactEvent)).scalar_one() == 1


def test_same_key_different_content_is_409(db_session) -> None:
    account = _make_account(db_session)
    other = _make_account(db_session, email="outro.contato@example.com")
    client = _manager(db_session)

    assert _post(client, account.id, {"event_type": "contato", "note": "a"}, "idem-2").status_code == 201
    assert _post(client, account.id, {"event_type": "contato", "note": "b"}, "idem-2").status_code == 409
    assert _post(client, account.id, {"event_type": "encerramento"}, "idem-2").status_code == 409
    # mesma chave em outro titulo tambem e conflito
    assert _post(client, other.id, {"event_type": "contato", "note": "a"}, "idem-2").status_code == 409
    db_session.expire_all()
    assert db_session.execute(select(func.count()).select_from(AccountContactEvent)).scalar_one() == 1


# ---------------------------------------------------------------- historico


def test_history_is_most_recent_first(db_session) -> None:
    account = _make_account(db_session)
    client = _manager(db_session)
    for index, body in enumerate(
        (
            {"event_type": "contato"},
            {"event_type": "promessa", "promised_date": "2030-02-01"},
            {"event_type": "remarcacao", "promised_date": "2030-03-01"},
        )
    ):
        assert _post(client, account.id, body, f"hist-{index}").status_code == 201

    history = client.get(f"/accounts/{account.id}/contact-events")

    assert history.status_code == 200, history.text
    assert [e["event_type"] for e in history.json()] == ["remarcacao", "promessa", "contato"]


def test_there_is_no_update_or_delete_route(db_session) -> None:
    account = _make_account(db_session)
    client = _manager(db_session)
    created = _post(client, account.id, {"event_type": "contato"}).json()
    url = f"/accounts/{account.id}/contact-events/{created['id']}"

    assert client.put(url, json={"event_type": "contato"}).status_code in (404, 405)
    assert client.patch(url, json={}).status_code in (404, 405)
    assert client.delete(url).status_code in (404, 405)
    assert client.put(f"/accounts/{account.id}/contact-events", json={}).status_code == 405
    assert client.delete(f"/accounts/{account.id}/contact-events").status_code == 405


# ------------------------------------------------------------------ agregado


def test_aggregate_in_list_and_single_with_boundary_dates(db_session) -> None:
    today = business_today()
    client = _manager(db_session)
    expectations = {}
    for offset, label in ((-1, "ontem"), (0, "hoje"), (1, "amanha")):
        account = _make_account(db_session, email=f"{label}@example.com")
        response = _post(
            client,
            account.id,
            {"event_type": "promessa", "promised_date": (today + timedelta(days=offset)).isoformat()},
            f"agg-{label}",
        )
        assert response.status_code == 201, response.text
        expectations[account.id] = (today + timedelta(days=offset), offset < 0)

    for account_id, (promised, overdue) in expectations.items():
        row = _row(client, account_id)
        assert row["promessa_vigente"] == promised.isoformat()
        assert row["promessa_vencida"] is overdue
        assert row["ultimo_contato_em"] is not None
        single = client.get(f"/accounts/{account_id}").json()
        assert single["promessa_vencida"] is overdue


def test_account_without_events_has_empty_aggregate(db_session) -> None:
    account = _make_account(db_session)
    client = _manager(db_session)

    row = _row(client, account.id)

    assert row["ultimo_contato_em"] is None
    assert row["promessa_vigente"] is None
    assert row["promessa_vencida"] is False


def test_encerramento_removes_the_overdue_alert(db_session) -> None:
    today = business_today()
    account = _make_account(db_session)
    client = _manager(db_session)
    _post(
        client,
        account.id,
        {"event_type": "promessa", "promised_date": (today - timedelta(days=2)).isoformat()},
        "enc-1",
    )
    assert _row(client, account.id)["promessa_vencida"] is True

    assert _post(client, account.id, {"event_type": "encerramento"}, "enc-2").status_code == 201

    row = _row(client, account.id)
    assert row["promessa_vencida"] is False
    assert row["promessa_vigente"] is None


def test_remarcacao_moves_the_promise_forward(db_session) -> None:
    today = business_today()
    account = _make_account(db_session)
    client = _manager(db_session)
    _post(
        client,
        account.id,
        {"event_type": "promessa", "promised_date": (today - timedelta(days=2)).isoformat()},
        "rem-1",
    )
    new_date = today + timedelta(days=5)
    _post(
        client,
        account.id,
        {"event_type": "remarcacao", "promised_date": new_date.isoformat()},
        "rem-2",
    )

    row = _row(client, account.id)
    assert row["promessa_vigente"] == new_date.isoformat()
    assert row["promessa_vencida"] is False


def test_paying_the_title_removes_the_alert(db_session) -> None:
    today = business_today()
    account = _make_account(db_session)
    client = _manager(db_session)
    _post(
        client,
        account.id,
        {"event_type": "promessa", "promised_date": (today - timedelta(days=1)).isoformat()},
        "pay-1",
    )
    assert _row(client, account.id)["promessa_vencida"] is True

    # pagamento (efeito do corredor governado): status do titulo muda
    db_session.expire_all()
    db_session.get(Account, account.id).status = "pago"
    db_session.commit()

    assert _row(client, account.id)["promessa_vencida"] is False


def test_aggregate_does_not_grow_queries_with_the_number_of_titles(db_session) -> None:
    today = business_today()
    client = _manager(db_session)

    def make_titles(count: int, prefix: str) -> None:
        for index in range(count):
            account = _make_account(db_session, email=f"{prefix}{index}@example.com")
            _insert_event(
                db_session,
                account,
                "promessa",
                today - timedelta(days=1),
                key=f"{prefix}-{index}",
                user_id=client.user_id,
            )

    def count_event_selects() -> int:
        statements: list[str] = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            if "account_contact_events" in statement and statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            assert client.get("/accounts/", params={"limit": 200}).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        return len(statements)

    make_titles(2, "pequeno")
    small = count_event_selects()
    make_titles(10, "grande")
    large = count_event_selects()

    assert small == large == 1


# ------------------------------------------------------------ CHECKs do banco


def _raw_insert(db_session, account, **values):
    user_id = db_session.execute(select(User.id).limit(1)).scalar_one()
    params = {
        "account_id": account.id,
        "event_type": "contato",
        "promised_date": None,
        "note": None,
        "actor_user_id": user_id,
        "idempotency_key": "raw-1",
    }
    params.update(values)
    db_session.execute(
        text(
            "INSERT INTO account_contact_events "
            "(account_id, event_type, promised_date, note, actor_user_id, idempotency_key) "
            "VALUES (:account_id, :event_type, :promised_date, :note, :actor_user_id, :idempotency_key)"
        ),
        params,
    )


@pytest.mark.parametrize(
    "values",
    [
        {"event_type": "outro"},
        {"event_type": "promessa", "promised_date": None},
        {"event_type": "remarcacao", "promised_date": None},
        {"event_type": "contato", "promised_date": date(2030, 1, 1)},
        {"event_type": "encerramento", "promised_date": date(2030, 1, 1)},
        {"note": ""},
        {"note": "   "},
        {"idempotency_key": ""},
        {"idempotency_key": "   "},
    ],
)
def test_database_checks_reject_invalid_rows(db_session, values) -> None:
    account = _make_account(db_session)
    _manager(db_session)

    with pytest.raises(IntegrityError):
        _raw_insert(db_session, account, **values)
        db_session.flush()
    db_session.rollback()


def test_database_rejects_note_above_the_column_size(db_session) -> None:
    # VARCHAR(280) recusa antes do CHECK de tamanho: DataError, nao IntegrityError.
    account = _make_account(db_session)
    _manager(db_session)

    with pytest.raises(DataError):
        _raw_insert(db_session, account, note="x" * 281)
    db_session.rollback()


def test_database_accepts_valid_rows_and_unique_key(db_session) -> None:
    account = _make_account(db_session)
    _manager(db_session)

    _raw_insert(db_session, account, event_type="promessa", promised_date=date(2030, 1, 1), note="x" * 280)
    db_session.flush()
    with pytest.raises(IntegrityError):
        _raw_insert(db_session, account)  # mesma idempotency_key
        db_session.flush()
    db_session.rollback()


def test_account_with_contact_history_cannot_be_deleted(db_session) -> None:
    account = _make_account(db_session)
    client = _manager(db_session)
    _post(client, account.id, {"event_type": "contato"})

    response = client.delete(f"/accounts/{account.id}")

    assert response.status_code == 409, response.text
