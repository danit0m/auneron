"""
AREA2 TASK054 (TI1) -- referencia estavel de origem de titulo.

Atribuicao manual POST /accounts/{id}/title-reference a um titulo legado:
linha lateral insert-only em account_title_references + uma linha TASK045
``update`` (so nomes de campo) no MESMO commit. PostgreSQL real
(auneron_test), dados sinteticos. Os testes estaticos no topo nao tocam o
banco. PREPARADO: nenhum destes testes foi executado ao preparar o lote.

A referencia e tratada como potencial dado pessoal: nunca aparece em
auditoria, log ou corpo de erro.
"""

import importlib.util
import logging
import os
import threading
import time
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import Table
from sqlalchemy import func
from sqlalchemy import insert
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateColumn
from sqlalchemy.schema import CreateTable

from app.api.routes import accounts as accounts_routes
from app.core.account_identity_lock import lock_account_identity
from app.core.account_writers_busy import AccountWritersBusy
from app.core.authentication import hash_password
from app.core.security import API_KEY_HEADER_NAME
from app.database.database import Base
from app.database.database import SessionLocal
from app.import_confirmation_schema import title_references_table
from app.main import app
from app.models.account import Account
from app.models.account_data_entry_audit_event import AccountDataEntryAuditEvent
from app.models.auth_session import AuthSession
from app.models.import_confirmation import account_title_references
from app.models.user import User
from app.schemas.account import AccountCreate
from app.schemas.account import AccountUpdate
from app.services import account_data_entry_audit as audit
from app.services import account_title_reference as titles


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "migrations/versions/e9a4c7d2b135_add_account_title_references.py"
REFS = account_title_references
AUDIT = AccountDataEntryAuditEvent.__table__

AUTHENTICATED_EMAIL = "developer.test@example.com"
VIEWER_EMAIL = "viewer.title@example.com"
VIEWER_PASSWORD = "Senha-Viewer-Titulo-123!"
NAME = "Fulana Titulo Sintetica"
SOURCE = "erp.sintetico"
# Sintetico, com cara de documento: nao pode vazar para auditoria/log/erro.
REFERENCE = "NF-000123/2026-PARCELA-01"
DECOMPOSED = unicodedata.normalize("NFD", "Título-Ação-77")


# ------------------------------------------------------------- helpers


def _create(client: TestClient, **overrides) -> int:
    payload = {"cliente": NAME, "valor": "1234.56", "vencimento": "2026-11-30"}
    payload.update(overrides)
    response = client.post("/accounts/", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _assign(client: TestClient, account_id: int, source=SOURCE, reference=REFERENCE, **extra):
    body = {"title_source": source, "title_reference": reference, **extra}
    return client.post(f"/accounts/{account_id}/title-reference", json=body)


def _rows(db_session: Session, table) -> list[dict]:
    db_session.expire_all()
    rows = [dict(row) for row in db_session.execute(select(table).order_by(table.c.id)).mappings()]
    db_session.rollback()
    return rows


def _count(db_session: Session, table) -> int:
    value = db_session.scalar(select(func.count()).select_from(table))
    db_session.rollback()
    return value


def _actor(db_session: Session) -> tuple[int, int]:
    user = db_session.execute(
        select(User).where(User.email == AUTHENTICATED_EMAIL)
    ).scalar_one()
    session = db_session.execute(
        select(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .order_by(AuthSession.id.desc())
    ).scalars().first()
    db_session.rollback()
    return user.id, session.id


def _legacy_account(db_session: Session, cliente=NAME, valor="1234.56", vencimento="2026-11-30") -> int:
    # Simula linha anterior ao TASK045 (o POST de hoje recusaria a tupla).
    account = Account(cliente=cliente, valor=valor, vencimento=vencimento, status="aberto")
    db_session.add(account)
    db_session.commit()
    return account.id


def _seed_reference(db_session: Session, account_id: int, source=SOURCE, reference=REFERENCE) -> None:
    # Simula uma referencia vinda do import v2 (TI2), sem a rota.
    db_session.execute(insert(REFS).values(
        account_id=account_id, title_source=source, title_reference=reference,
        assigned_via="import_v2", actor_user_id=1, import_idempotency_key="k" * 36,
        import_content_sha256="0" * 64,
    ))
    db_session.commit()


def _viewer_client(db_session: Session) -> TestClient:
    db_session.add(
        User(
            name="Visualizador Titulo",
            email=VIEWER_EMAIL,
            password_hash=hash_password(VIEWER_PASSWORD),
            role="viewer",
            active=True,
        )
    )
    db_session.commit()
    viewer = TestClient(app)
    viewer.headers.update({API_KEY_HEADER_NAME: os.environ["API_KEY"]})
    login = viewer.post("/auth/login", json={"email": VIEWER_EMAIL, "password": VIEWER_PASSWORD})
    assert login.status_code == 200, login.text
    return viewer


def _assert_not_leaked(text_value: str, *secrets: str) -> None:
    for secret in secrets:
        assert secret.casefold() not in text_value.casefold()


def _assert_no_reference_in_audit(db_session: Session, *secrets: str) -> None:
    for row in _rows(db_session, AUDIT):
        _assert_not_leaked(repr(row), *secrets)


# ------------------------------------------------------------- static contract (sem banco)


def _load_migration():
    spec = importlib.util.spec_from_file_location("candidate_title_reference_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder:
    def __init__(self):
        self.metadata = MetaData()
        Table("accounts", self.metadata, Column("id", Integer, primary_key=True))
        self.calls = []

    def create_table(self, name, *columns, **options):
        self.calls.append(("create_table", name))
        return Table(name, self.metadata, *columns, **options)

    def create_index(self, name, table, columns, unique=False):
        self.calls.append(("create_index", name, table, tuple(columns), unique))

    def execute(self, sql):
        self.calls.append(("execute", sql))

    def drop_index(self, name, table_name):
        self.calls.append(("drop_index", name, table_name))

    def drop_table(self, name):
        self.calls.append(("drop_table", name))


def _ddl(table):
    dialect = postgresql.dialect()
    compiler = dialect.ddl_compiler(dialect, CreateTable(table))
    columns = tuple(" ".join(str(CreateColumn(c).compile(dialect=dialect)).split())
                    for c in table.columns)
    constraints = tuple(sorted(" ".join(compiler.process(c).split())
                               for c in table.constraints))
    return columns, constraints


def _indexes(table):
    return {(index.name, index.unique, tuple(c.name for c in index.columns)) for index in table.indexes}


def test_m1_migration_chain_ddl_matches_the_single_table_definition() -> None:
    migration = _load_migration()
    assert (migration.revision, migration.down_revision) == ("e9a4c7d2b135", "c3a8f1e6d204")

    recorder = _Recorder()
    migration.op = recorder
    migration.upgrade()
    created = recorder.metadata.tables["account_title_references"]
    assert _ddl(created) == _ddl(title_references_table) == _ddl(REFS)
    (fk,) = created.foreign_keys
    assert (fk.target_fullname, fk.ondelete, fk.name) == (
        "accounts.id", "RESTRICT", "fk_account_title_references_account_id")
    assert ("create_index", "uq_account_title_references_source_ref", REFS.name,
            ("title_source", "title_reference"), True) in recorder.calls
    assert _indexes(title_references_table) == _indexes(REFS) == {
        ("uq_account_title_references_source_ref", True, ("title_source", "title_reference"))}
    assert [call[0] for call in recorder.calls] == ["create_table", "create_index", "execute", "execute"]
    sql = " ".join(call[1] for call in recorder.calls if call[0] == "execute")
    assert "BEFORE UPDATE OR DELETE" in sql and "FOR EACH ROW" in sql
    assert "ON account_title_references " in sql
    assert "TRUNCATE" not in sql.upper()
    constraints = " ".join(_ddl(REFS)[1])
    assert " IN (" not in constraints.upper()          # sem lista IN: sem re-renderizacao (TASK051)

    recorder = _Recorder()
    migration.op = recorder
    migration.downgrade()
    assert [call[0] for call in recorder.calls] == ["execute", "execute", "drop_index", "drop_table"]


def test_m4_isolated_definition_is_registered_once_in_base_metadata() -> None:
    import app.models  # noqa: F401 - registra todos os modelos

    assert Base.metadata.tables["account_title_references"] is REFS
    assert REFS is not title_references_table
    assert REFS.metadata is Base.metadata
    # accounts, o espelho v1 e a auditoria TASK045 nao ganharam colunas.
    assert "title_source" not in Account.__table__.c and "title_reference" not in Account.__table__.c
    assert len(AUDIT.c) == 24


def test_create_and_update_reject_only_title_reference_keys() -> None:
    base = {"cliente": NAME, "valor": "10.00", "vencimento": "2026-11-30"}
    for key in ("title_source", "title_reference"):
        with pytest.raises(ValueError):
            AccountCreate.model_validate({**base, key: "x"})
        with pytest.raises(ValueError):
            AccountUpdate.model_validate({key: "x"})
    # Outras chaves desconhecidas continuam ignoradas (comportamento v1).
    assert "status" not in AccountCreate.model_validate({**base, "status": "pago"}).model_dump()
    assert AccountUpdate.model_validate({"status": "pago"}).model_dump(exclude_unset=True) == {}


def test_normalization_strip_nfc_and_safe_validation() -> None:
    composed = unicodedata.normalize("NFC", DECOMPOSED)
    assert titles.normalize_title_reference(f"  {DECOMPOSED}\t") == composed
    assert titles.normalize_title_source(" erp.sintetico ") == "erp.sintetico"
    for bad in ("", "   ", "x" * 129, "a\x00b", "a\nb", "a\x85b", "a​b", "a‮b", "a b", 7, None):
        with pytest.raises(titles.TitleReferenceError) as caught:
            titles.normalize_title_reference(bad)
        assert caught.value.detail() == {"code": "invalid_title_reference"}
    for bad in ("", "ERP", "-erp", "erp sintetico", "e" * 65, "érp", 1):
        with pytest.raises(titles.TitleReferenceError) as caught:
            titles.normalize_title_source(bad)
        assert caught.value.detail() == {"code": "invalid_title_source"}
    assert titles.normalize_title_reference("x" * 128) == "x" * 128


def test_errors_and_results_never_carry_the_reference() -> None:
    error = titles.TitleReferenceError("reference_exists", account_id=7)
    assert error.detail() == {"code": "reference_exists", "account_id": 7}
    assert repr(error) == "TitleReferenceError(code='reference_exists')"
    with pytest.raises(ValueError):
        titles.TitleReferenceError("anything")
    result = titles.AssignedTitleReference(1, SOURCE, REFERENCE, ())
    assert REFERENCE not in repr(result)
    assert str(titles.TitleReferenceStorageError()) == "title_reference_insert_failed"


def test_service_never_commits_logs_or_prints() -> None:
    import ast

    tree = ast.parse((BACKEND / "app/services/account_title_reference.py").read_text(encoding="utf-8"))
    called = {getattr(node.func, "attr", getattr(node.func, "id", None))
              for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not called & {"commit", "rollback", "flush", "print", "exception", "info", "warning", "error", "debug"}
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names}
    assert "logging" not in imported


# ------------------------------------------------------------- M2 catalogo migrado (PG)


def test_m2_migrated_catalog_fk_restrict_uniques_and_enabled_trigger(db_session: Session) -> None:
    oid = db_session.scalar(text("SELECT to_regclass('public.account_title_references')::oid"))
    assert oid is not None
    keys = dict(db_session.execute(text(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = :oid AND contype IN ('f', 'u')"), {"oid": oid}).all())
    assert keys == {
        "fk_account_title_references_account_id":
            "FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT",
        "uq_account_title_references_account": "UNIQUE (account_id)",
    }
    index = db_session.scalar(text(
        "SELECT indexdef FROM pg_indexes WHERE tablename = 'account_title_references' "
        "AND indexname = 'uq_account_title_references_source_ref'"))
    assert index.startswith("CREATE UNIQUE INDEX") and index.endswith("(title_source, title_reference)")
    trigger = db_session.execute(text(
        "SELECT tgenabled::text, tgtype::int FROM pg_trigger WHERE tgrelid = :oid AND NOT tgisinternal"),
        {"oid": oid}).all()
    assert trigger == [("O", 1 | 2 | 8 | 16)]
    checks = sorted(db_session.execute(text(
        "SELECT conname FROM pg_constraint WHERE conrelid = :oid AND contype = 'c'"), {"oid": oid}).scalars())
    assert checks == sorted(c.name for c in REFS.constraints if c.name and c.name.startswith("ck_"))
    db_session.rollback()


def test_m2_database_checks_reject_unsafe_values_even_without_the_api(db_session: Session) -> None:
    account_id = _legacy_account(db_session)
    for values in (
        {"title_source": "ERP"},
        {"title_reference": " pad "},
        {"title_reference": "a\x01b"},
        {"title_reference": ""},
        {"assigned_via": "other"},
        {"import_content_sha256": "0" * 64},               # manual com proveniencia de import
    ):
        row = {"account_id": account_id, "title_source": SOURCE, "title_reference": REFERENCE,
               "assigned_via": "manual_assign", "actor_user_id": 1, **values}
        with pytest.raises(IntegrityError):
            db_session.execute(insert(REFS).values(**row))
        db_session.rollback()
    assert _count(db_session, REFS) == 0


# ------------------------------------------------------------- A1 sucesso


def test_a1_assign_writes_one_side_row_and_one_field_names_only_audit_row(
    client: TestClient, db_session: Session, caplog
) -> None:
    account_id = _create(client)
    before_account = _rows(db_session, Account.__table__)
    user_id, session_id = _actor(db_session)

    # Loggers da aplicacao; o log de SQL do SQLAlchemy (echo) fica desligado
    # por configuracao e nao e o objeto deste teste.
    with caplog.at_level(logging.DEBUG, logger="auneron"):
        response = client.post(
            f"/accounts/{account_id}/title-reference",
            json={"title_source": SOURCE, "title_reference": REFERENCE},
            headers={"X-Request-ID": "title-a1-request"},
        )

    assert response.status_code == 201, response.text
    assert response.json() == {"account_id": account_id, "title_source": SOURCE,
                               "title_reference": REFERENCE, "warnings": []}
    [ref] = _rows(db_session, REFS)
    assert (ref["account_id"], ref["title_source"], ref["title_reference"]) == (account_id, SOURCE, REFERENCE)
    assert (ref["assigned_via"], ref["actor_user_id"], ref["auth_session_id"]) == ("manual_assign", user_id, session_id)
    assert ref["request_id"] == "title-a1-request"
    assert (ref["import_idempotency_key"], ref["import_content_sha256"]) == (None, None)

    create, update = _rows(db_session, AUDIT)
    assert create["operation"] == "create"
    assert (update["operation"], update["channel"], update["account_reference"]) == ("update", "manual_api", account_id)
    assert update["changed_fields"] == ["title_reference", "title_source"]
    assert (update["actor_user_id"], update["auth_session_id"]) == (user_id, session_id)
    for field in ("valor", "vencimento", "cliente_sha256", "email_sha256", "whatsapp_sha256"):
        assert update[f"before_{field}"] == update[f"after_{field}"]
    # accounts nao muda: o binding v1 de mark-paid continua o mesmo.
    assert _rows(db_session, Account.__table__) == before_account
    _assert_no_reference_in_audit(db_session, REFERENCE)
    _assert_not_leaked(" ".join(record.getMessage() for record in caplog.records), REFERENCE)


# ------------------------------------------------------------- A2 imutavel


def test_a2_second_assign_is_409_and_rows_are_immutable_in_the_database(
    client: TestClient, db_session: Session
) -> None:
    account_id = _create(client)
    assert _assign(client, account_id).status_code == 201

    retry = _assign(client, account_id)
    other = _assign(client, account_id, reference="NF-999")

    for response in (retry, other):
        assert response.status_code == 409
        assert response.json()["detail"] == {"code": "title_reference_already_assigned"}
    assert _count(db_session, REFS) == 1
    assert [row["operation"] for row in _rows(db_session, AUDIT)] == ["create", "update"]

    for statement in (
        "UPDATE account_title_references SET title_reference = 'outra'",
        "DELETE FROM account_title_references",
    ):
        with pytest.raises(DBAPIError) as caught:
            db_session.execute(text(statement))
        assert "append-only" in str(caught.value)
        db_session.rollback()
    assert [row["title_reference"] for row in _rows(db_session, REFS)] == [REFERENCE]


# ------------------------------------------------------------- A3 unicidade


def test_a3_reference_owned_by_another_account_is_409_with_its_id(
    client: TestClient, db_session: Session
) -> None:
    first = _create(client)
    second = _create(client, cliente="Outra Conta Sintetica")
    assert _assign(client, first).status_code == 201

    response = _assign(client, second)

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "reference_exists", "account_id": first}
    _assert_not_leaked(response.text, REFERENCE)
    assert _count(db_session, REFS) == 1
    assert len(_rows(db_session, AUDIT)) == 3  # 2 create + 1 update
    # Mesma referencia em outra fonte e outra identidade.
    assert _assign(client, second, source="erp.outro").status_code == 201


# ------------------------------------------------------------- A4 tupla


def test_a4_unreferenced_legacy_duplicate_blocks_and_referenced_coincidence_warns(
    client: TestClient, db_session: Session
) -> None:
    target = _legacy_account(db_session)
    legacy = _legacy_account(db_session, cliente="  fulana   TITULO sintetica ")

    blocked = _assign(client, target)

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == {"code": "legacy_duplicate_review", "account_ids": [legacy]}
    assert _count(db_session, REFS) == 0
    assert _rows(db_session, AUDIT) == []

    _seed_reference(db_session, legacy, reference="NF-LEGADA")   # a outra passa a ser referenciada
    allowed = _assign(client, target)

    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["warnings"] == [{"code": "coincident_referenced_tuple", "account_ids": [legacy]}]
    assert _count(db_session, REFS) == 2


def test_a4_manual_create_against_a_referenced_tuple_keeps_the_v1_409(
    client: TestClient, db_session: Session
) -> None:
    account_id = _create(client)
    assert _assign(client, account_id).status_code == 201

    response = client.post("/accounts/", json={"cliente": NAME, "valor": "1234.56", "vencimento": "2026-11-30"})

    assert response.status_code == 409
    assert response.json() == {"detail": "Ja existe conta com este cliente, valor e vencimento."}


# ------------------------------------------------------------- A5 validacao / NFC


def test_a5_invalid_input_is_422_with_fixed_code_and_never_echoed(
    client: TestClient, db_session: Session
) -> None:
    account_id = _create(client)
    secret = "CPF-123.456.789-00"
    cases = [
        ({"title_source": SOURCE, "title_reference": secret + "\x07"}, "invalid_title_reference"),
        ({"title_source": SOURCE, "title_reference": secret * 10}, "invalid_title_reference"),
        ({"title_source": SOURCE, "title_reference": "   "}, "invalid_title_reference"),
        ({"title_source": "ERP " + secret, "title_reference": REFERENCE}, "invalid_title_source"),
        ({"title_source": SOURCE, "title_reference": secret, "extra": secret}, "invalid_title_reference_request"),
        ({"title_source": SOURCE, "title_reference": 123456789}, "invalid_title_reference_request"),
        ({"title_reference": secret}, "invalid_title_reference_request"),
        ([secret], "invalid_title_reference_request"),
    ]
    for body, code in cases:
        response = client.post(f"/accounts/{account_id}/title-reference", json=body)
        assert response.status_code == 422, (code, response.text)
        assert response.json()["detail"] == {"code": code}
        _assert_not_leaked(response.text, secret, "123456789")
    assert _count(db_session, REFS) == 0
    assert [row["operation"] for row in _rows(db_session, AUDIT)] == ["create"]


def test_a5_nfc_and_nfd_spellings_are_one_identity(client: TestClient, db_session: Session) -> None:
    first = _create(client)
    second = _create(client, cliente="Outra Conta Sintetica")

    created = _assign(client, first, reference=DECOMPOSED)
    duplicate = _assign(client, second, reference=unicodedata.normalize("NFC", DECOMPOSED))

    assert created.status_code == 201
    assert created.json()["title_reference"] == unicodedata.normalize("NFC", DECOMPOSED)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == {"code": "reference_exists", "account_id": first}
    [row] = _rows(db_session, REFS)
    assert row["title_reference"] == unicodedata.normalize("NFC", DECOMPOSED)


# ------------------------------------------------------------- A6 RBAC / 404 / 503


def test_a6_rbac_missing_account_and_busy_lock_write_nothing(
    client: TestClient, unauthenticated_client: TestClient, db_session: Session, monkeypatch
) -> None:
    account_id = _create(client)
    viewer = _viewer_client(db_session)
    try:
        forbidden = _assign(viewer, account_id)
    finally:
        viewer.close()
    unauthenticated = _assign(unauthenticated_client, account_id)
    missing = _assign(client, account_id + 1000)

    def busy(db):
        raise AccountWritersBusy()
    monkeypatch.setattr(accounts_routes, "lock_account_identity", busy)
    contended = _assign(client, account_id)

    assert forbidden.status_code == 403
    assert unauthenticated.status_code in (401, 403)
    assert missing.status_code == 404
    assert contended.status_code == 503
    assert "Retry-After" in contended.headers
    assert _count(db_session, REFS) == 0
    assert [row["operation"] for row in _rows(db_session, AUDIT)] == ["create"]


# ------------------------------------------------------------- A7 rollback conjunto


def test_a7_audit_failure_rolls_back_the_side_row(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    account_id = _create(client)

    def violating(db, account, actor):
        event = audit.record_title_reference_assigned(db, account, actor)
        event.actor_reference = "user:0"  # viola o CHECK actor_reference no commit
        return event
    monkeypatch.setattr(titles, "record_title_reference_assigned", violating)

    with pytest.raises(IntegrityError) as caught:
        _assign(client, account_id)

    _assert_not_leaked(str(caught.value), REFERENCE)
    assert _count(db_session, REFS) == 0
    assert [row["operation"] for row in _rows(db_session, AUDIT)] == ["create"]


def test_a7_side_row_failure_writes_no_audit_and_raises_without_values(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    account_id = _create(client)
    monkeypatch.setattr(titles, "get_request_id", lambda: "r" * 200)  # estoura VARCHAR(128)

    with pytest.raises(titles.TitleReferenceStorageError) as caught:
        _assign(client, account_id)

    assert caught.value.__context__ is None and caught.value.__cause__ is None
    _assert_not_leaked(str(caught.value), REFERENCE)
    assert _count(db_session, REFS) == 0
    assert [row["operation"] for row in _rows(db_session, AUDIT)] == ["create"]


# ------------------------------------------------------------- A8 concorrencia


def _service_assign(account_id: int, reference: str, *, locked: bool, hold=None, row_lock=True):
    session = SessionLocal()
    try:
        if locked:
            assert lock_account_identity(session) is True
        statement = select(Account).where(Account.id == account_id)
        account = session.execute(
            statement.with_for_update() if row_lock else statement
        ).scalar_one()
        actor = audit.audit_actor_from_ids(990000001)
        result = titles.assign_title_reference(session, account, SOURCE, reference, actor)
        if hold is not None:
            hold()
        session.commit()
        return result
    except titles.TitleReferenceError as error:
        session.rollback()
        return error
    finally:
        session.close()


def _run_concurrently(first, second):
    outcomes = {}
    inserted = threading.Event()

    def hold():
        inserted.set()
        time.sleep(0.5)  # a segunda transacao fica bloqueada enquanto isso

    def one():
        outcomes["first"] = first(hold)

    def two():
        assert inserted.wait(5)
        outcomes["second"] = second()

    threads = [threading.Thread(target=one), threading.Thread(target=two)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert not any(thread.is_alive() for thread in threads)
    return outcomes


def test_a8_concurrent_assigns_under_the_lock_create_exactly_one_row(db_session: Session) -> None:
    first, second = _legacy_account(db_session), _legacy_account(db_session, cliente="Outra Conta Sintetica")

    outcomes = _run_concurrently(
        lambda hold: _service_assign(first, REFERENCE, locked=True, hold=hold),
        lambda: _service_assign(second, REFERENCE, locked=True),
    )

    assert isinstance(outcomes["first"], titles.AssignedTitleReference)
    assert outcomes["second"].detail() == {"code": "reference_exists", "account_id": first}
    assert [row["account_id"] for row in _rows(db_session, REFS)] == [first]
    assert len(_rows(db_session, AUDIT)) == 1


def test_a8_unique_index_backstop_without_the_lock_maps_to_409(db_session: Session) -> None:
    # Escritor que pula o lock: o indice unico bloqueia ate o commit e responde 23505.
    first, second = _legacy_account(db_session), _legacy_account(db_session, cliente="Outra Conta Sintetica")

    outcomes = _run_concurrently(
        lambda hold: _service_assign(first, REFERENCE, locked=False, hold=hold),
        lambda: _service_assign(second, REFERENCE, locked=False),
    )

    assert isinstance(outcomes["first"], titles.AssignedTitleReference)
    assert outcomes["second"].detail() == {"code": "reference_exists"}
    assert _count(db_session, REFS) == 1
    assert len(_rows(db_session, AUDIT)) == 1

    # UNIQUE(account_id) tambem e backstop: duas referencias para a mesma conta,
    # a segunda sem lock de identidade nem FOR UPDATE (chega ao indice).
    third = _legacy_account(db_session, cliente="Terceira Conta Sintetica")
    outcomes = _run_concurrently(
        lambda hold: _service_assign(third, "NF-A", locked=False, hold=hold),
        lambda: _service_assign(third, "NF-B", locked=False, row_lock=False),
    )
    assert outcomes["second"].detail() == {"code": "title_reference_already_assigned"}
    assert [row["title_reference"] for row in _rows(db_session, REFS) if row["account_id"] == third] == ["NF-A"]


# ------------------------------------------------------------- A9 DELETE


def test_a9_referenced_account_delete_is_409_and_fk_restricts_direct_sql(
    client: TestClient, db_session: Session
) -> None:
    referenced = _create(client)
    plain = _create(client, cliente="Conta Sem Referencia Sintetica")
    assert _assign(client, referenced).status_code == 201

    blocked = client.delete(f"/accounts/{referenced}")
    allowed = client.delete(f"/accounts/{plain}")

    assert blocked.status_code == 409
    assert blocked.json() == {"detail": "Conta possui histórico governado e não pode ser excluída."}
    assert allowed.status_code == 204
    assert [row["operation"] for row in _rows(db_session, AUDIT) if row["account_reference"] == referenced] == [
        "create", "update"]
    tables = {column.table.name for column in accounts_routes.GOVERNED_HISTORY_COLUMNS}
    assert "account_title_references" in tables
    assert "account_data_entry_audit_events" not in tables

    with pytest.raises(IntegrityError) as caught:
        db_session.execute(text("DELETE FROM accounts WHERE id = :id"), {"id": referenced})
        db_session.flush()
    assert getattr(caught.value.orig, "sqlstate", None) == "23503"
    db_session.rollback()
    assert _count(db_session, REFS) == 1


# ------------------------------------------------------------- A10 POST/PUT


def test_a10_post_and_put_reject_title_keys_and_keep_other_unknown_keys(
    client: TestClient, db_session: Session
) -> None:
    account_id = _create(client)

    post = client.post("/accounts/", json={"cliente": "Nova Conta Sintetica", "valor": "1.00",
                                           "vencimento": "2026-12-01", "title_reference": REFERENCE})
    put_source = client.put(f"/accounts/{account_id}", json={"title_source": SOURCE})
    put_both = client.put(f"/accounts/{account_id}", json={"valor": "2.00", "title_source": SOURCE,
                                                          "title_reference": REFERENCE})
    put_status = client.put(f"/accounts/{account_id}", json={"status": "pago"})

    assert (post.status_code, put_source.status_code, put_both.status_code) == (422, 422, 422)
    assert put_status.status_code == 200 and put_status.json()["status"] == "aberto"
    assert _count(db_session, Account.__table__) == 1
    assert _count(db_session, REFS) == 0
    assert [row["operation"] for row in _rows(db_session, AUDIT)] == ["create"]
    [account] = _rows(db_session, Account.__table__)
    assert str(account["valor"]) == "1234.56"
