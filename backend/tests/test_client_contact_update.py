"""
Fatia "Contato por cliente": PUT /accounts/client-contact.

Completa e-mail/WhatsApp em todos os titulos de um cliente (nome normalizado
por lower(trim)), com uma linha de auditoria por conta efetivamente alterada.
PostgreSQL real (auneron_test), dados sinteticos.
"""

import os

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.authentication import hash_password
from app.core.security import API_KEY_HEADER_NAME
from app.main import app
from app.models.account import Account
from app.models.account_data_entry_audit_event import AccountDataEntryAuditEvent
from app.models.account_event import AccountEvent
from app.models.user import User


URL = "/accounts/client-contact"
NAME = "Acme Contato Sintetico"
VIEWER_EMAIL = "viewer.contato@example.com"
VIEWER_PASSWORD = "Senha-Viewer-Contato-123!"


def _create(client: TestClient, valor: str, vencimento: str, cliente: str = NAME, **extra):
    payload = {"cliente": cliente, "valor": valor, "vencimento": vencimento}
    payload.update(extra)
    response = client.post("/accounts/", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _three_titles(client: TestClient) -> list[dict]:
    return [
        _create(client, "100.00", "2026-12-01"),
        _create(client, "200.50", "2026-12-15"),
        _create(client, "300.00", "2027-01-10"),
    ]


def _audit_count(db_session: Session, operation: str | None = None) -> int:
    query = select(func.count()).select_from(AccountDataEntryAuditEvent)
    if operation:
        query = query.where(AccountDataEntryAuditEvent.operation == operation)
    value = db_session.scalar(query)
    db_session.rollback()
    return value


def _rows(db_session: Session) -> list[Account]:
    db_session.expire_all()
    rows = list(db_session.execute(select(Account).order_by(Account.id)).scalars())
    db_session.rollback()
    return rows


def _viewer_client(db_session: Session) -> TestClient:
    db_session.add(
        User(
            name="Visualizador Contato",
            email=VIEWER_EMAIL,
            password_hash=hash_password(VIEWER_PASSWORD),
            role="viewer",
            active=True,
        )
    )
    db_session.commit()
    viewer = TestClient(app)
    viewer.headers.update({API_KEY_HEADER_NAME: os.environ["API_KEY"]})
    login = viewer.post(
        "/auth/login", json={"email": VIEWER_EMAIL, "password": VIEWER_PASSWORD}
    )
    assert login.status_code == 200, login.text
    return viewer


def test_updates_all_titles_with_one_audit_row_each(
    client: TestClient, db_session: Session
) -> None:
    _three_titles(client)
    assert _audit_count(db_session, "create") == 3

    response = client.put(
        URL,
        json={"cliente": NAME, "email": "financeiro@acme.example.com", "whatsapp": "11999998888"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"cliente": NAME, "matched": 3, "updated": 3}

    rows = _rows(db_session)
    assert [r.email for r in rows] == ["financeiro@acme.example.com"] * 3
    assert [r.whatsapp for r in rows] == ["11999998888"] * 3
    assert _audit_count(db_session, "update") == 3


def test_repeating_same_call_writes_no_new_audit_rows(
    client: TestClient, db_session: Session
) -> None:
    _three_titles(client)
    body = {"cliente": NAME, "email": "financeiro@acme.example.com", "whatsapp": "+55 (11) 99999-8888"}
    assert client.put(URL, json=body).json()["updated"] == 3
    audit_after_first = _audit_count(db_session)

    again = client.put(URL, json=body)
    assert again.status_code == 200
    assert again.json() == {"cliente": NAME, "matched": 3, "updated": 0}
    assert _audit_count(db_session) == audit_after_first


def test_does_not_touch_valor_vencimento_status_or_events(
    client: TestClient, db_session: Session
) -> None:
    _three_titles(client)
    snapshot_before = [
        (r.id, r.valor, r.vencimento, r.status) for r in _rows(db_session)
    ]
    events_before = db_session.scalar(select(func.count()).select_from(AccountEvent))
    db_session.rollback()

    response = client.put(URL, json={"cliente": NAME, "email": "a@acme.example.com"})
    assert response.status_code == 200, response.text

    assert [(r.id, r.valor, r.vencimento, r.status) for r in _rows(db_session)] == snapshot_before
    events_after = db_session.scalar(select(func.count()).select_from(AccountEvent))
    db_session.rollback()
    assert events_after == events_before


def test_absent_field_does_not_erase_existing_contact(
    client: TestClient, db_session: Session
) -> None:
    _create(client, "10.00", "2026-12-01", email="antigo@acme.example.com", whatsapp="11911112222")

    response = client.put(URL, json={"cliente": NAME, "whatsapp": "11933334444"})
    assert response.status_code == 200, response.text
    [row] = _rows(db_session)
    assert row.email == "antigo@acme.example.com"
    assert row.whatsapp == "11933334444"

    response = client.put(URL, json={"cliente": NAME, "email": "novo@acme.example.com"})
    assert response.json()["updated"] == 1
    [row] = _rows(db_session)
    assert row.email == "novo@acme.example.com"
    assert row.whatsapp == "11933334444"


def test_name_matches_case_and_spaces_but_not_similar_names(
    client: TestClient, db_session: Session
) -> None:
    _create(client, "10.00", "2026-12-01", cliente="Acme Ltda")
    _create(client, "20.00", "2026-12-02", cliente="  ACME LTDA ")
    _create(client, "30.00", "2026-12-03", cliente="Acme")

    response = client.put(
        URL, json={"cliente": "  acme ltda  ", "email": "x@acme.example.com"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["matched"] == 2
    assert response.json()["updated"] == 2

    by_value = {str(r.valor): r for r in _rows(db_session)}
    assert by_value["10.00"].email == "x@acme.example.com"
    assert by_value["20.00"].email == "x@acme.example.com"
    assert by_value["30.00"].email is None


def test_invalid_whatsapp_is_422_and_nothing_is_written(
    client: TestClient, db_session: Session
) -> None:
    _three_titles(client)
    audit_before = _audit_count(db_session)

    for invalid in ("123456789", "1" * 16, "11 9999a8888", "++5511999998888", "55+11999998888", "abc"):
        response = client.put(
            URL, json={"cliente": NAME, "email": "ok@acme.example.com", "whatsapp": invalid}
        )
        assert response.status_code == 422, (invalid, response.text)

    assert all(r.email is None and r.whatsapp is None for r in _rows(db_session))
    assert _audit_count(db_session) == audit_before


def test_without_email_and_whatsapp_is_422(client: TestClient) -> None:
    _three_titles(client)
    assert client.put(URL, json={"cliente": NAME}).status_code == 422
    assert client.put(URL, json={"cliente": NAME, "email": None, "whatsapp": None}).status_code == 422
    assert client.put(URL, json={"cliente": NAME, "whatsapp": "   "}).status_code == 422


def test_invalid_email_and_short_name_are_422(client: TestClient) -> None:
    _three_titles(client)
    assert client.put(URL, json={"cliente": NAME, "email": "nao-e-email"}).status_code == 422
    assert client.put(URL, json={"cliente": "A", "email": "a@acme.example.com"}).status_code == 422


def test_unknown_client_is_404(client: TestClient) -> None:
    response = client.put(
        URL, json={"cliente": "Cliente Inexistente", "email": "a@acme.example.com"}
    )
    assert response.status_code == 404


def test_without_clients_manage_is_403_and_nothing_is_written(
    client: TestClient, db_session: Session
) -> None:
    _three_titles(client)
    audit_before = _audit_count(db_session)
    viewer = _viewer_client(db_session)

    response = viewer.put(URL, json={"cliente": NAME, "email": "a@acme.example.com"})
    assert response.status_code == 403
    assert all(r.email is None for r in _rows(db_session))
    assert _audit_count(db_session) == audit_before


def test_whatsapp_normalization(client: TestClient, db_session: Session) -> None:
    _create(client, "10.00", "2026-12-01")

    cases = {
        "+55 (11) 99999-8888": "+5511999998888",
        "(11) 99999-8888": "11999998888",
        "11999998888": "11999998888",
        " 1199 99-8888 ": "11999998888",
        "+" + "1" * 15: "+" + "1" * 15,
        "1" * 10: "1" * 10,
    }
    for entrada, esperado in cases.items():
        response = client.put(URL, json={"cliente": NAME, "whatsapp": entrada})
        assert response.status_code == 200, (entrada, response.text)
        [row] = _rows(db_session)
        assert row.whatsapp == esperado, entrada


def test_accented_name_matches_with_different_case(
    client: TestClient, db_session: Session
) -> None:
    # Depende do collation/locale do banco de teste: o lower() do Postgres
    # precisa baixar "É"/"Ç"/"Ã" (locale UTF-8 com suporte; em locale C nao
    # baixa letras acentuadas). Se falhar so aqui, suspeitar do collation.
    _create(client, "10.00", "2026-12-01", cliente="JOSÉ AÇÃO Sintetico")
    _create(client, "20.00", "2026-12-02", cliente=" José Ação SINTETICO ")

    response = client.put(
        URL, json={"cliente": "josé ação sintetico", "email": "j@acme.example.com"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["matched"] == 2
    assert response.json()["updated"] == 2
    assert all(r.email == "j@acme.example.com" for r in _rows(db_session))
