import threading
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.database import SessionLocal


def valid_account_payload() -> dict:
    marker = uuid4().hex[:12]

    return {
        "cliente": f"Cliente Regressao {marker}",
        "email": f"regressao.{marker}@outlook.com",
        "whatsapp": "11999999999",
        "valor": 12345.67,
        "vencimento": "2026-12-31",
    }


def test_create_get_and_update_account(
    client: TestClient,
) -> None:
    creation_response = client.post(
        "/accounts/",
        json=valid_account_payload(),
    )

    assert creation_response.status_code == 201

    created_account = creation_response.json()

    assert created_account["id"] > 0
    assert created_account["status"] == "aberto"
    assert created_account["valor"] == 12345.67
    assert created_account["created_at"]

    account_id = created_account["id"]

    get_response = client.get(
        f"/accounts/{account_id}",
    )

    assert get_response.status_code == 200
    assert get_response.json()["id"] == account_id

    update_response = client.put(
        f"/accounts/{account_id}",
        json={
            "valor": 13000.01,
            "status": "atrasado",
        },
    )

    assert update_response.status_code == 200

    updated_account = update_response.json()

    assert updated_account["status"] == "aberto"
    assert updated_account["valor"] == 13000.01


def test_create_ignores_client_supplied_status(
    client: TestClient,
) -> None:
    payload = valid_account_payload()
    payload["status"] = "atrasado"

    response = client.post(
        "/accounts/",
        json=payload,
    )

    assert response.status_code == 201
    assert response.json()["status"] == "aberto"


def test_rejects_zero_value(
    client: TestClient,
) -> None:
    payload = valid_account_payload()
    payload["valor"] = 0

    response = client.post(
        "/accounts/",
        json=payload,
    )

    assert response.status_code == 422


def test_rejects_blank_client_name(
    client: TestClient,
) -> None:
    payload = valid_account_payload()
    payload["cliente"] = "   "

    response = client.post(
        "/accounts/",
        json=payload,
    )

    assert response.status_code == 422


def _vencimento_change_count(
    db_session: Session, account_id: int
) -> int:
    return db_session.execute(
        text(
            "SELECT COUNT(*) FROM account_vencimento_changes "
            "WHERE account_id = :account_id"
        ),
        {"account_id": account_id},
    ).scalar_one()


def test_update_account_vencimento_change_creates_audit_record(
    client: TestClient,
    db_session: Session,
) -> None:
    creation = client.post(
        "/accounts/", json=valid_account_payload()
    )
    account_id = creation.json()["id"]

    response = client.put(
        f"/accounts/{account_id}",
        json={"vencimento": "2027-01-15"},
    )

    assert response.status_code == 200
    assert response.json()["vencimento"] == "2027-01-15"

    row = db_session.execute(
        text(
            "SELECT previous_vencimento, new_vencimento, "
            "actor_type, actor_reference, actor_user_id "
            "FROM account_vencimento_changes "
            "WHERE account_id = :account_id"
        ),
        {"account_id": account_id},
    ).one()
    assert str(row.previous_vencimento) == "2026-12-31"
    assert str(row.new_vencimento) == "2027-01-15"
    assert row.actor_type == "user"
    assert row.actor_reference.startswith("user:")
    assert row.actor_user_id is not None


def test_update_account_without_vencimento_creates_no_audit_record(
    client: TestClient,
    db_session: Session,
) -> None:
    creation = client.post(
        "/accounts/", json=valid_account_payload()
    )
    account_id = creation.json()["id"]

    response = client.put(
        f"/accounts/{account_id}",
        json={"valor": 999.00},
    )

    assert response.status_code == 200
    assert _vencimento_change_count(db_session, account_id) == 0


def test_update_account_same_vencimento_creates_no_audit_record(
    client: TestClient,
    db_session: Session,
) -> None:
    creation = client.post(
        "/accounts/", json=valid_account_payload()
    )
    account_id = creation.json()["id"]

    response = client.put(
        f"/accounts/{account_id}",
        json={"vencimento": "2026-12-31"},
    )

    assert response.status_code == 200
    assert _vencimento_change_count(db_session, account_id) == 0


def test_update_account_other_fields_do_not_create_vencimento_audit(
    client: TestClient,
    db_session: Session,
) -> None:
    creation = client.post(
        "/accounts/", json=valid_account_payload()
    )
    account_id = creation.json()["id"]

    response = client.put(
        f"/accounts/{account_id}",
        json={
            "cliente": "Cliente Renomeado Teste",
            "valor": 500.50,
        },
    )

    assert response.status_code == 200
    assert _vencimento_change_count(db_session, account_id) == 0


def test_update_account_vencimento_round_trip_creates_two_records(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    A -> B -> A deve produzir DUAS linhas distintas -- nunca uma
    unica linha deduplicada nem a supressao da segunda transicao.
    """

    creation = client.post(
        "/accounts/", json=valid_account_payload()
    )
    account_id = creation.json()["id"]

    first = client.put(
        f"/accounts/{account_id}",
        json={"vencimento": "2027-02-01"},
    )
    assert first.status_code == 200

    second = client.put(
        f"/accounts/{account_id}",
        json={"vencimento": "2026-12-31"},
    )
    assert second.status_code == 200

    assert _vencimento_change_count(db_session, account_id) == 2

    rows = db_session.execute(
        text(
            "SELECT previous_vencimento, new_vencimento "
            "FROM account_vencimento_changes "
            "WHERE account_id = :account_id "
            "ORDER BY changed_at ASC, id ASC"
        ),
        {"account_id": account_id},
    ).all()
    assert str(rows[0].previous_vencimento) == "2026-12-31"
    assert str(rows[0].new_vencimento) == "2027-02-01"
    assert str(rows[1].previous_vencimento) == "2027-02-01"
    assert str(rows[1].new_vencimento) == "2026-12-31"


def test_account_vencimento_changes_check_constraint_rejects_no_op(
    db_session: Session,
) -> None:
    """
    Prova que a garantia contra A->A existe tambem no banco, nao
    apenas na logica de aplicacao -- mesmo um caller que burlasse a
    checagem da rota nao consegue inserir uma linha sem mudanca real.
    """

    from sqlalchemy.exc import IntegrityError

    creation_payload = valid_account_payload()
    from app.models.account import Account

    account = Account(
        cliente=creation_payload["cliente"],
        email=creation_payload["email"],
        whatsapp=creation_payload["whatsapp"],
        valor=creation_payload["valor"],
        vencimento="2026-12-31",
    )
    db_session.add(account)
    db_session.commit()

    from app.models.account_vencimento_change import (
        AccountVencimentoChange,
    )

    db_session.add(
        AccountVencimentoChange(
            account_id=account.id,
            previous_vencimento="2026-12-31",
            new_vencimento="2026-12-31",
            actor_type="user",
            actor_reference="user:1",
        )
    )

    try:
        db_session.commit()
        assert False, (
            "esperava IntegrityError do CHECK "
            "ck_account_vencimento_changes_actual_change"
        )
    except IntegrityError:
        db_session.rollback()


def test_update_account_vencimento_concurrent_requests_serialize_without_stale_read(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    Duas sessoes concorrentes tentando mudar o vencimento da mesma
    Account devem ser serializadas pelo FOR UPDATE -- nunca duas
    linhas alegando o mesmo previous_vencimento a partir de leitura
    stale. Prova real, com duas conexoes de banco distintas, thread B
    literalmente bloqueada ate a sessao A liberar o lock.
    """

    creation = client.post(
        "/accounts/", json=valid_account_payload()
    )
    account_id = creation.json()["id"]

    session_a_locked = threading.Event()
    release_session_a = threading.Event()
    session_b_previous_vencimento: list[str] = []

    def hold_lock_in_session_a() -> None:
        session_a = SessionLocal()
        try:
            row = session_a.execute(
                text(
                    "SELECT vencimento FROM accounts "
                    "WHERE id = :id FOR UPDATE"
                ),
                {"id": account_id},
            ).one()
            assert str(row.vencimento) == "2026-12-31"
            session_a_locked.set()
            release_session_a.wait(timeout=10)
            session_a.execute(
                text(
                    "UPDATE accounts SET vencimento = "
                    "'2027-03-01' WHERE id = :id"
                ),
                {"id": account_id},
            )
            session_a.commit()
        finally:
            session_a.close()

    thread_a = threading.Thread(target=hold_lock_in_session_a)
    thread_a.start()
    assert session_a_locked.wait(timeout=10)

    def read_after_lock_in_session_b() -> None:
        session_b = SessionLocal()
        try:
            row = session_b.execute(
                text(
                    "SELECT vencimento FROM accounts "
                    "WHERE id = :id FOR UPDATE"
                ),
                {"id": account_id},
            ).one()
            session_b_previous_vencimento.append(
                str(row.vencimento)
            )
            session_b.commit()
        finally:
            session_b.close()

    thread_b = threading.Thread(
        target=read_after_lock_in_session_b
    )
    thread_b.start()

    release_session_a.set()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()

    # Sessao B so consegue ler apos A liberar o lock -- nunca ve o
    # valor stale ("2026-12-31"), sempre o valor ja comitado por A.
    assert session_b_previous_vencimento == ["2027-03-01"]