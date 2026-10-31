"""
AREA2 TASK045 -- auditoria duravel de entrada de dados em accounts.

POST/PUT/DELETE /accounts e o upload CSV legado gravam uma linha em
account_data_entry_audit_events na MESMA transacao da escrita de negocio,
com o ator humano da sessao autenticada. PostgreSQL real (auneron_test),
dados sinteticos. Os testes estaticos no fim nao tocam o banco.

Escopo: digests sao SHA-256 sem chave; provam igualdade/mudanca, NAO sao
anonimos nem cifrados. TRUNCATE e dono/superusuario ficam fora da garantia
append-only.
"""

import ast
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData
from sqlalchemy import Table
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateColumn
from sqlalchemy.schema import CreateTable

from app.api.routes import accounts as accounts_routes
from app.core.authentication import hash_password
from app.core.security import API_KEY_HEADER_NAME
from app.main import app
from app.models.account import Account
from app.models.account_data_entry_audit_event import AccountDataEntryAuditEvent
from app.models.account_vencimento_change import AccountVencimentoChange
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services import account_data_entry_audit as audit
from app.services import import_service


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "migrations/versions/c3a8f1e6d204_add_account_data_entry_audit_events.py"
TABLE = AccountDataEntryAuditEvent.__table__

AUTHENTICATED_EMAIL = "developer.test@example.com"
NAME = "Fulana Auditoria Sintetica"
EMAIL = "fulana.auditoria@example.com"
PHONE = "11955554444"
NEW_EMAIL = "fulana.nova@example.com"
VIEWER_EMAIL = "viewer.audit@example.com"
VIEWER_PASSWORD = "Senha-Viewer-Auditoria-123!"


# ------------------------------------------------------------- helpers


def _create(client: TestClient, **overrides):
    payload = {
        "cliente": NAME,
        "email": EMAIL,
        "whatsapp": PHONE,
        "valor": "1234.56",
        "vencimento": "2026-11-30",
    }
    payload.update(overrides)
    return client.post("/accounts/", json=payload)


def _events(db_session: Session, **criteria) -> list:
    db_session.expire_all()
    query = select(AccountDataEntryAuditEvent).order_by(AccountDataEntryAuditEvent.id)
    for name, value in criteria.items():
        query = query.where(getattr(AccountDataEntryAuditEvent, name) == value)
    rows = list(db_session.execute(query).scalars())
    db_session.rollback()
    return rows


def _count(db_session: Session, model) -> int:
    value = db_session.scalar(select(func.count()).select_from(model))
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


def _raw_rows(db_session: Session) -> list[dict]:
    rows = [dict(row) for row in db_session.execute(select(TABLE)).mappings()]
    db_session.rollback()
    return rows


def _assert_no_plaintext(db_session: Session, *secrets: str) -> None:
    for row in _raw_rows(db_session):
        for value in row.values():
            for secret in secrets:
                assert secret.casefold() not in str(value).casefold()


def _viewer_client(db_session: Session) -> TestClient:
    db_session.add(
        User(
            name="Visualizador Auditoria",
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


def _upload(client: TestClient, content: bytes):
    return client.post(
        "/upload/", files={"file": ("synthetic.csv", content, "text/csv")}
    )


# ------------------------------------------------------- T1/T2 POST


def test_t1_post_records_one_create_row_with_session_actor_and_digests(
    client: TestClient, db_session: Session
) -> None:
    response = client.post(
        "/accounts/",
        json={
            "cliente": NAME,
            "email": EMAIL,
            "whatsapp": PHONE,
            "valor": "1234.56",
            "vencimento": "2026-11-30",
        },
        headers={"X-Request-ID": "audit-t1-request"},
    )
    assert response.status_code == 201, response.text
    account_id = response.json()["id"]
    user_id, session_id = _actor(db_session)

    [event] = _events(db_session)
    assert (event.operation, event.channel) == ("create", "manual_api")
    assert event.account_reference == account_id
    assert (event.actor_type, event.actor_user_id) == ("user", user_id)
    assert event.actor_reference == f"user:{user_id}"
    assert event.auth_session_id == session_id
    assert event.request_id == "audit-t1-request"
    assert event.changed_fields == ["cliente", "email", "valor", "vencimento", "whatsapp"]
    assert event.status_at_operation == "aberto"
    assert str(event.after_valor) == "1234.56"
    assert event.after_vencimento.isoformat() == "2026-11-30"
    assert event.after_cliente_sha256 == audit.field_digest("cliente", NAME)
    assert event.after_email_sha256 == audit.field_digest("email", EMAIL)
    assert event.after_whatsapp_sha256 == audit.field_digest("whatsapp", PHONE)
    assert event.before_valor is None and event.before_cliente_sha256 is None
    assert (event.source_sha256, event.source_row) == (None, None)
    # Digest com separacao de dominio: nao e o SHA-256 cru do valor.
    assert event.after_email_sha256 != hashlib.sha256(EMAIL.encode()).hexdigest()
    _assert_no_plaintext(db_session, NAME, EMAIL, PHONE)


def test_t2_post_identity_collision_writes_no_audit_row(
    client: TestClient, db_session: Session
) -> None:
    assert _create(client).status_code == 201
    collision = _create(client, cliente="  FULANA   auditoria SINTETICA ")

    assert collision.status_code == 409
    assert len(_events(db_session)) == 1
    assert _count(db_session, Account) == 1


# ------------------------------------------------------- T3-T5 PUT


def test_t3_put_valor_and_email_records_before_after(
    client: TestClient, db_session: Session
) -> None:
    account_id = _create(client).json()["id"]

    response = client.put(
        f"/accounts/{account_id}", json={"valor": "2000.00", "email": NEW_EMAIL}
    )
    assert response.status_code == 200, response.text

    [event] = _events(db_session, operation="update")
    assert event.channel == "manual_api"
    assert event.account_reference == account_id
    assert event.changed_fields == ["email", "valor"]
    assert (str(event.before_valor), str(event.after_valor)) == ("1234.56", "2000.00")
    assert event.before_vencimento == event.after_vencimento
    assert event.before_email_sha256 == audit.field_digest("email", EMAIL)
    assert event.after_email_sha256 == audit.field_digest("email", NEW_EMAIL)
    assert event.before_cliente_sha256 == event.after_cliente_sha256
    _assert_no_plaintext(db_session, NAME, EMAIL, NEW_EMAIL, PHONE)


def test_t4_put_noop_and_unknown_status_key_write_no_audit_row(
    client: TestClient, db_session: Session
) -> None:
    account_id = _create(client).json()["id"]

    same = client.put(
        f"/accounts/{account_id}",
        json={"cliente": NAME, "valor": "1234.56", "email": EMAIL},
    )
    status_only = client.put(f"/accounts/{account_id}", json={"status": "pago"})

    assert same.status_code == 200, same.text
    assert status_only.status_code == 200, status_only.text
    assert status_only.json()["status"] == "aberto"
    assert _events(db_session, operation="update") == []


def test_t5_put_vencimento_records_audit_and_vencimento_change(
    client: TestClient, db_session: Session
) -> None:
    account_id = _create(client).json()["id"]

    response = client.put(f"/accounts/{account_id}", json={"vencimento": "2026-12-15"})
    assert response.status_code == 200, response.text

    [event] = _events(db_session, operation="update")
    assert event.changed_fields == ["vencimento"]
    assert event.before_vencimento.isoformat() == "2026-11-30"
    assert event.after_vencimento.isoformat() == "2026-12-15"
    assert _count(db_session, AccountVencimentoChange) == 1


# ------------------------------------------------------- T6/T7 DELETE


def test_t6_delete_records_before_snapshot_and_retry_is_404_without_row(
    client: TestClient, db_session: Session
) -> None:
    account_id = _create(client).json()["id"]
    user_id, _ = _actor(db_session)

    first = client.delete(f"/accounts/{account_id}")
    second = client.delete(f"/accounts/{account_id}")

    assert first.status_code == 204, first.text
    assert second.status_code == 404
    create, delete = _events(db_session)
    assert create.operation == "create"
    assert (delete.operation, delete.channel) == ("delete", "manual_api")
    assert delete.account_reference == account_id
    assert delete.actor_user_id == user_id
    assert delete.status_at_operation == "aberto"
    assert delete.changed_fields == []
    assert str(delete.before_valor) == "1234.56"
    assert delete.before_cliente_sha256 == audit.field_digest("cliente", NAME)
    assert delete.after_valor is None and delete.after_cliente_sha256 is None
    assert _count(db_session, Account) == 0


def test_t7_delete_blocked_by_governed_history_writes_no_delete_row(
    client: TestClient, db_session: Session
) -> None:
    account_id = _create(client).json()["id"]
    assert client.put(
        f"/accounts/{account_id}", json={"vencimento": "2026-12-15"}
    ).status_code == 200

    response = client.delete(f"/accounts/{account_id}")

    assert response.status_code == 409
    assert _events(db_session, operation="delete") == []
    assert _count(db_session, Account) == 1


def test_audit_table_is_not_part_of_the_delete_history_guard(
    client: TestClient, db_session: Session
) -> None:
    # Com create + update auditados e sem historico governado, a conta
    # continua excluivel (204).
    account_id = _create(client).json()["id"]
    assert client.put(f"/accounts/{account_id}", json={"valor": "99.00"}).status_code == 200
    assert len(_events(db_session)) == 2

    assert client.delete(f"/accounts/{account_id}").status_code == 204
    tables = {column.table.name for column in accounts_routes.GOVERNED_HISTORY_COLUMNS}
    assert TABLE.name not in tables


# ------------------------------------------------------- T8 rollback


def _violating(real):
    def wrapper(*args, **kwargs):
        event = real(*args, **kwargs)
        event.actor_reference = "user:0"  # viola o CHECK actor_reference
        return event
    return wrapper


def test_t8_audit_failure_on_post_rolls_back_the_account(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    monkeypatch.setattr(accounts_routes, "record_create", _violating(audit.record_create))

    with pytest.raises(IntegrityError):
        _create(client)

    assert _count(db_session, Account) == 0
    assert _events(db_session) == []


def test_t8_audit_failure_on_delete_keeps_the_account(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    account_id = _create(client).json()["id"]
    monkeypatch.setattr(accounts_routes, "record_delete", _violating(audit.record_delete))

    with pytest.raises(IntegrityError):
        client.delete(f"/accounts/{account_id}")

    assert _count(db_session, Account) == 1
    assert [event.operation for event in _events(db_session)] == ["create"]


# ------------------------------------------------------- T9/T10 legacy


def test_t9_legacy_upload_audits_only_inserted_rows(
    client: TestClient, db_session: Session
) -> None:
    assert _create(client, cliente="Conta Existente Sintetica", email=None,
                   whatsapp=None, valor="30.00", vencimento="2026-03-01").status_code == 201
    content = (
        "cliente,email,valor,vencimento,status\n"
        f"{NAME},{EMAIL},1500.00,2026-12-31,aberto\n"
        "Conta Existente Sintetica,,30.00,2026-03-01,aberto\n"
        "Conta Invalida Sintetica,,10.00,2026-12-31,pendente\n"
    ).encode("utf-8")
    user_id, session_id = _actor(db_session)

    response = _upload(client, content)

    assert response.status_code == 200, response.text
    result = response.json()["resultado"]
    assert set(result) == {"message", "summary", "detalhes_erros"}
    summary = result["summary"]
    assert (summary["importados"], summary["duplicados"], summary["erros"]) == (1, 1, 1)
    assert summary["valor_total"] == pytest.approx(1500.0)
    [event] = _events(db_session, channel="legacy_csv_upload")
    assert event.operation == "create"
    assert (event.actor_user_id, event.auth_session_id) == (user_id, session_id)
    assert event.source_sha256 == hashlib.sha256(content).hexdigest()
    assert event.source_row == 2
    assert event.after_email_sha256 == audit.field_digest("email", EMAIL)
    _assert_no_plaintext(db_session, NAME, EMAIL)


def test_t10_legacy_db_check_failure_at_flush_rolls_back_whole_file(
    client: TestClient, db_session: Session
) -> None:
    content = (
        "cliente,valor,vencimento,status\n"
        "Conta Valida Sintetica,10.00,2026-12-31,aberto\n"
        "X,20.00,2026-12-31,aberto\n"
    ).encode("utf-8")

    with pytest.raises(IntegrityError):
        _upload(client, content)

    assert _count(db_session, Account) == 0
    assert _events(db_session) == []


# ------------------------------------------------------- T11 append-only


def test_t11_audit_rows_reject_update_and_delete_but_truncate_works(
    client: TestClient, db_session: Session
) -> None:
    assert _create(client).status_code == 201

    for statement in (
        "UPDATE account_data_entry_audit_events SET request_id = 'x'",
        "DELETE FROM account_data_entry_audit_events",
    ):
        with pytest.raises(DBAPIError) as caught:
            db_session.execute(text(statement))
        assert "append-only" in str(caught.value)
        db_session.rollback()
    assert len(_events(db_session)) == 1

    # TRUNCATE nao e bloqueado (limpeza de teste); limite declarado.
    db_session.execute(text("TRUNCATE TABLE account_data_entry_audit_events"))
    db_session.commit()
    assert _events(db_session) == []


# ------------------------------------------------------- T12 actor


class _RecordingDb:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError("db must not be touched")
        return record


def test_t12_direct_service_call_without_actor_fails_before_any_db_access(tmp_path) -> None:
    path = tmp_path / "synthetic.csv"
    path.write_bytes(b"cliente,valor,vencimento,status\nConta Sintetica,1.00,2026-01-01,\n")

    db = _RecordingDb()
    with pytest.raises(TypeError):
        import_service.import_clients(str(path), db)
    for invalid in (None, 0, -1, True, False, "7", 7.0):
        with pytest.raises(audit.AuditActorRequired):
            import_service.import_clients(str(path), db, actor_user_id=invalid)
    with pytest.raises(audit.AuditActorRequired):
        import_service.import_clients(str(path), db, actor_user_id=7, auth_session_id=True)
    assert db.calls == []
    # Nao e ValueError: upload.py mapearia ValueError para 400.
    assert not issubclass(audit.AuditActorRequired, ValueError)


def test_t12_audit_actor_requires_a_real_session_user() -> None:
    from types import SimpleNamespace

    valid = SimpleNamespace(user=SimpleNamespace(id=5), session=SimpleNamespace(id=9))
    assert audit.audit_actor(valid) == audit.AuditActor(user_id=5, auth_session_id=9)
    for broken in (
        None,
        SimpleNamespace(user=None, session=None),
        SimpleNamespace(user=SimpleNamespace(id=None), session=SimpleNamespace(id=9)),
        SimpleNamespace(user=SimpleNamespace(id=True), session=SimpleNamespace(id=9)),
    ):
        with pytest.raises(audit.AuditActorRequired):
            audit.audit_actor(broken)


# ------------------------------------------------------- RBAC


def test_rbac_viewer_is_forbidden_on_every_audited_writer_and_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    account_id = _create(client).json()["id"]
    viewer = _viewer_client(db_session)
    try:
        responses = [
            _create(viewer, cliente="Outra Conta Sintetica"),
            viewer.put(f"/accounts/{account_id}", json={"valor": "1.00"}),
            viewer.delete(f"/accounts/{account_id}"),
            _upload(viewer, b"cliente,valor,vencimento,status\nConta Viewer,1.00,2026-01-01,\n"),
        ]
    finally:
        viewer.close()

    assert [response.status_code for response in responses] == [403, 403, 403, 403]
    assert [event.operation for event in _events(db_session)] == ["create"]
    assert _count(db_session, Account) == 1


# ------------------------------------------------------- static contract


def _load_migration():
    spec = importlib.util.spec_from_file_location("candidate_audit_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder:
    def __init__(self):
        self.metadata = MetaData()
        self.calls = []

    def create_table(self, name, *columns, **options):
        self.calls.append(("create_table", name))
        return Table(name, self.metadata, *columns, **options)

    def create_index(self, name, table, columns):
        self.calls.append(("create_index", name, table, tuple(columns)))

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


def test_migration_chain_metadata_and_exact_model_match() -> None:
    migration = _load_migration()
    assert (migration.revision, migration.down_revision) == ("c3a8f1e6d204", "b428d1a6c903")

    recorder = _Recorder()
    migration.op = recorder
    migration.upgrade()
    created = recorder.metadata.tables["account_data_entry_audit_events"]
    assert _ddl(created) == _ddl(TABLE)
    assert not created.foreign_keys and not TABLE.foreign_keys
    assert ("create_index", "ix_account_data_entry_audit_account_occurred",
            TABLE.name, ("account_reference", "occurred_at")) in recorder.calls
    sql = " ".join(call[1] for call in recorder.calls if call[0] == "execute")
    assert "BEFORE UPDATE OR DELETE" in sql and "FOR EACH ROW" in sql
    assert "TRUNCATE" not in sql.upper()
    assert "actor_user_id::text" in " ".join(_ddl(TABLE)[1])

    recorder = _Recorder()
    migration.op = recorder
    migration.downgrade()
    assert [call[0] for call in recorder.calls] == [
        "execute", "execute", "drop_index", "drop_table",
    ]


def test_migration_source_has_no_foreign_key_or_cascade() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for forbidden in ("ForeignKey", "CASCADE", "SET NULL", "ondelete"):
        assert forbidden not in source


def test_helper_only_adds_and_never_flushes_commits_or_queries() -> None:
    tree = ast.parse((BACKEND / "app/services/account_data_entry_audit.py").read_text(encoding="utf-8"))
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "add" in called
    assert not called & {"flush", "commit", "rollback", "execute", "query", "delete", "refresh"}


def test_f1_contract_legacy_flushes_once_after_loop_and_audits_before_commit() -> None:
    tree = ast.parse((BACKEND / "app/services/import_service.py").read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "import_clients")
    keyword_only = [arg.arg for arg in function.args.kwonlyargs]
    assert keyword_only == ["actor_user_id", "auth_session_id"]
    assert function.args.kw_defaults[0] is None  # actor_user_id sem default

    def calls(node, attr):
        return [n.lineno for n in ast.walk(node) if isinstance(n, ast.Call)
                and getattr(n.func, "attr", getattr(n.func, "id", None)) == attr]

    loop = next(n for n in ast.walk(function) if isinstance(n, ast.For))
    flushes, audits, commits = calls(function, "flush"), calls(function, "record_create"), calls(function, "commit")
    assert len(flushes) == 1 and len(commits) == 1 and len(audits) == 1
    assert loop.end_lineno < flushes[0] < audits[0] < commits[0]
    # Nada de auditoria, flush ou add dentro do try por linha.
    row_try = next(n for n in loop.body if isinstance(n, ast.Try))
    for attr in ("record_create", "flush", "add", "add_all", "query"):
        assert calls(row_try, attr) == []
