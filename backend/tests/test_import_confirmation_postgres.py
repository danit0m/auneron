"""REAL PostgreSQL gate. Do not run/collect locally outside the authorized launcher.
Existing conftest truncates 24 tables; this module additionally truncates the
2 import tables before/after each test (26 total). All data is synthetic.
CI runs this module alone with IMPORT_CONFIRMATION_TEST_ENABLED=true, so
app.main registers /imports/confirm (writer_protocol_verified=True) only for
the local disposable auneron_test database; no auth dependency is overridden.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from app.api.routes.import_confirm import create_import_confirm_router
from app.core.account_identity_lock import account_identity_lock_statement
from app.core.account_writers_busy import busy_detail
from app.core.authentication import require_permission
from app.core.security import require_api_key
from app.database.database import SessionLocal, engine
from app.models.account import Account
from app.services import import_preview_parser as parser
from app.services import import_preview_transport as transport
from app.services import import_confirmation_plan as planner
from app.services import import_confirmation_execution as execution
from app.services import import_confirmation_repository as repositories

CSV = b'cliente;valor;vencimento;status\r\nCliente Sintetico Gate;10,00;2026-01-05;aberto'

@pytest.fixture(autouse=True)
def clean_import_tables(clean_database):
    def clean():
        with engine.begin() as connection:
            connection.execute(text('TRUNCATE TABLE import_confirmation_audit_events, import_confirmation_receipts RESTART IDENTITY'))
    clean()
    yield
    clean()


def repo():
    # Test harness repository; app.main also declares True, but only when
    # confirmation_test_is_available() (test env + flag + local auneron_test).
    return repositories.PostgresImportConfirmationRepository(SessionLocal, execution, writer_protocol_verified=True)


def candidate(key=None):
    preview = parser.parse_import_preview('preview.csv', CSV)
    return planner.plan_import_confirmation(parser.parse_import_preview, CSV, 'csv',
        expected_file_sha256=preview.file_sha256, expected_content_sha256=preview.content_sha256,
        idempotency_key=key or str(uuid.uuid4()))


def confirmation_client(authenticated_client):
    app = FastAPI()
    app.include_router(create_import_confirm_router(parser_module=parser, transport_module=transport,
        plan_module=planner, execution_module=execution, repository_module=repositories, repository=repo(),
        require_api_key=require_api_key, require_permission=require_permission))
    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update(dict(authenticated_client.headers))
    client.cookies.update(authenticated_client.cookies)
    return client


def post(client, key):
    preview = parser.parse_import_preview('preview.csv', CSV)
    return client.post('/imports/confirm', content=CSV,
        params={'format':'csv','expected_file_sha256':preview.file_sha256,'expected_content_sha256':preview.content_sha256},
        headers={'Content-Type':'application/octet-stream','Idempotency-Key':key})


def counts():
    with SessionLocal() as session:
        return tuple(session.scalar(select(func.count()).select_from(table)) for table in
            (Account, repositories.receipts_table, repositories.audit_events_table))


def test_real_http_auth_create_and_replay(client, service_client):
    key = str(uuid.uuid4())
    with confirmation_client(service_client) as denied:
        response = post(denied, key)
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'no-store'
    with confirmation_client(client) as confirmed:
        first = post(confirmed, key)
        assert first.status_code == 201
        before = counts()
        assert before == (1,1,2)
        second = post(confirmed, key)
        assert second.status_code == 200
        assert second.json()['replayed'] is True
        assert second.json()['account_references'] == first.json()['account_references']
        assert counts() == before


def test_real_rollback_after_account_creation_on_audit_failure():
    repository = repo()
    original = repositories._UnitOfWork.append_audit_event
    def fail_audit(self, event):
        raise RuntimeError('synthetic audit failure')
    from unittest import mock
    with mock.patch.object(repositories._UnitOfWork, 'append_audit_event', fail_audit):
        with pytest.raises(RuntimeError):
            execution.execute_import_confirmation(repository, candidate(), actor_reference='synthetic:rollback')
    assert repositories._UnitOfWork.append_audit_event is original
    assert counts() == (0,0,0)


def test_receipt_survives_deletion_and_blocks_new_key():
    plan = candidate()
    repository = repo()
    first = execution.execute_import_confirmation(repository, plan, actor_reference='synthetic:history')
    with SessionLocal() as session:
        from app.core.account_identity_lock import lock_account_identity
        lock_account_identity(session)
        for account_id in first.receipt.account_references:
            session.delete(session.get(Account, account_id))
        session.commit()
    replay = execution.execute_import_confirmation(repository, plan, actor_reference='synthetic:history')
    assert replay.replayed
    assert replay.receipt.account_references == first.receipt.account_references
    with pytest.raises(execution.ImportConfirmationExecutionError) as caught:
        execution.execute_import_confirmation(repository, candidate(), actor_reference='synthetic:history')
    assert caught.value.code == 'content_already_imported'
    assert counts() == (0,1,2)


def test_concurrent_confirmation_and_real_manual_create_produce_one_account(client):
    headers, cookies = dict(client.headers), dict(client.cookies)
    def manual():
        from app.main import app
        concurrent = TestClient(app, raise_server_exceptions=False)
        try:
            concurrent.headers.update(headers)
            concurrent.cookies.update(cookies)
            return concurrent.post('/accounts/', json={'cliente':'Cliente Sintetico Gate',
                'valor':'10.00','vencimento':'2026-01-05','status':'aberto'}).status_code
        finally:
            concurrent.close()
    def confirm():
        with confirmation_client(client) as concurrent:
            return post(concurrent, str(uuid.uuid4())).status_code
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(manual)
        second = workers.submit(confirm)
        statuses = sorted((first.result(timeout=20), second.result(timeout=20)))
    assert statuses == [201,409]
    assert counts()[0] == 1


def test_real_manual_update_rejects_identity_collision(client):
    with confirmation_client(client) as confirmed:
        assert post(confirmed, str(uuid.uuid4())).status_code == 201
    created = client.post('/accounts/', json={'cliente':'Outro Cliente Sintetico',
        'valor':'20.00','vencimento':'2026-02-01','status':'aberto'})
    assert created.status_code == 201
    response = client.put('/accounts/'+str(created.json()['id']), json={
        'cliente':'  CLIENTE   SINTETICO GATE ','valor':'10.00','vencimento':'2026-01-05'})
    assert response.status_code == 409
    assert counts()[0] == 2


def test_legacy_upload_observes_same_unicode_identity(client):
    with confirmation_client(client) as confirmed:
        assert post(confirmed, str(uuid.uuid4())).status_code == 201
    duplicate = CSV.replace(b'Cliente Sintetico Gate', b' CLIENTE   SINTETICO GATE ')
    response = client.post('/upload/', files={'file':('synthetic.csv',duplicate,'text/csv')})
    assert response.status_code == 200
    assert response.json()['resultado']['summary']['duplicados'] == 1
    assert counts()[0] == 1


def test_real_unique_violation_is_sanitized_and_rolled_back():
    repository = repo()
    first = execution.execute_import_confirmation(repository, candidate(), actor_reference='synthetic:unique')
    before = counts()
    with pytest.raises(repositories.ImportConfirmationRepositoryError) as caught:
        with repository.transaction() as unit:
            unit.lock_account_writers()
            unit.insert_receipt(first.receipt)
    assert caught.value.code == 'conflict'
    assert caught.value.__context__ is None
    assert counts() == before


def test_real_commit_acknowledgement_failure_is_resolved_by_replay():
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.exc import OperationalError
    class LostAcknowledgement(Session):
        def commit(self):
            super().commit()
            raise OperationalError('synthetic acknowledgement', {}, Exception('synthetic'))
    lost_factory = sessionmaker(bind=engine, class_=LostAcknowledgement)
    repository = repositories.PostgresImportConfirmationRepository(lost_factory, execution, writer_protocol_verified=True)
    plan = candidate()
    with pytest.raises(repositories.ImportConfirmationRepositoryError):
        execution.execute_import_confirmation(repository, plan, actor_reference='synthetic:ack')
    assert counts() == (1,1,2)
    replay = execution.execute_import_confirmation(repo(), plan, actor_reference='synthetic:ack')
    assert replay.replayed
    assert counts() == (1,1,2)


def test_real_viewer_is_forbidden_before_import(service_client):
    from app.core.authentication import hash_password
    from app.models.user import User
    password = 'Senha-Confirmacao-123!'
    with SessionLocal() as session:
        session.add(User(name='Visualizador Sintetico', email='viewer.confirmation@example.com',
            password_hash=hash_password(password), role='viewer', active=True))
        session.commit()
    response = service_client.post('/auth/login', json={'email':'viewer.confirmation@example.com','password':password})
    assert response.status_code == 200
    with confirmation_client(service_client) as denied:
        response = post(denied,str(uuid.uuid4()))
        assert response.status_code == 403
        assert response.headers['cache-control'] == 'no-store'
    assert counts() == (0,0,0)


def test_real_application_exposes_guarded_confirmation_and_capability(client):
    response = client.get('/imports/capabilities')
    assert response.status_code == 200
    assert response.json() == {'confirmation_available':True}
    assert response.headers['cache-control'] == 'no-store'
    first = post(client,str(uuid.uuid4()))
    assert first.status_code == 201
    assert counts() == (1,1,2)


# --- REAL contention: an independent transaction holds the common lock. ---

def account_rows():
    table = Account.__table__
    with SessionLocal() as session:
        return [dict(row) for row in session.execute(select(table).order_by(table.c.id)).mappings()]


@contextmanager
def held_account_writers_lock(lock_timeout, statement_timeout):
    # Session-scoped timeouts are transaction-local (set_config(..., true)) and
    # only reach SessionLocal sessions begun while the blocker holds the lock.
    # Engine defaults (lock_timeout 5000ms) still bound any wait if this fails.
    observed = {'sqlstates': [], 'scoped_transactions': 0}

    def scope(session, transaction, connection):
        connection.execute(text('SELECT set_config(\'lock_timeout\', :lock, true), '
            'set_config(\'statement_timeout\', :statement, true)'),
            {'lock': lock_timeout, 'statement': statement_timeout})
        observed['scoped_transactions'] += 1

    def record(context):
        # Only the SQLSTATE is kept: no SQL, parameters or message.
        orig = getattr(context, 'original_exception', None)
        observed['sqlstates'].append(getattr(orig, 'sqlstate', None) or getattr(orig, 'pgcode', None))

    blocker = engine.connect()
    try:
        transaction = blocker.begin()
        try:
            blocker.execute(account_identity_lock_statement())
            event.listen(SessionLocal, 'after_begin', scope)
            try:
                event.listen(engine, 'handle_error', record)
                try:
                    yield observed
                finally:
                    event.remove(engine, 'handle_error', record)
            finally:
                event.remove(SessionLocal, 'after_begin', scope)
        finally:
            transaction.rollback()
    finally:
        blocker.close()


EXISTING = {'cliente':'Conta Existente Sintetica','valor':'30.00','vencimento':'2026-03-01','status':'aberto'}

WRITERS = {
    'post_account': lambda client, account_id, key: client.post('/accounts/', json={
        'cliente':'Nova Conta Sintetica','valor':'40.00','vencimento':'2026-04-01','status':'aberto'}),
    'put_account': lambda client, account_id, key: client.put('/accounts/'+str(account_id), json={
        'cliente':'Conta Alterada Sintetica','valor':'50.00','vencimento':'2026-05-01'}),
    'delete_account': lambda client, account_id, key: client.delete('/accounts/'+str(account_id)),
    'legacy_upload': lambda client, account_id, key: client.post('/upload/',
        files={'file':('synthetic.csv',CSV,'text/csv')}),
    'confirmation': lambda client, account_id, key: post(client, key),
}


@pytest.mark.parametrize('expected_sqlstate, lock_timeout, statement_timeout', [
    pytest.param('55P03', '250ms', '5000ms', id='lock_timeout_55P03'),
    pytest.param('57014', '5000ms', '250ms', id='statement_timeout_57014'),
])
@pytest.mark.parametrize('writer', sorted(WRITERS))
def test_real_writer_contention_returns_busy_without_writes(client, writer, expected_sqlstate,
                                                            lock_timeout, statement_timeout):
    created = client.post('/accounts/', json=EXISTING)
    assert created.status_code == 201
    account_id = created.json()['id']
    before_counts, before_rows = counts(), account_rows()
    assert before_counts == (1,0,0)
    key = str(uuid.uuid4())

    with held_account_writers_lock(lock_timeout, statement_timeout) as observed:
        response = WRITERS[writer](client, account_id, key)

    assert observed['scoped_transactions'] >= 1
    assert observed['sqlstates'] == [expected_sqlstate]
    assert response.status_code == 503
    assert response.headers['retry-after'] == '5'
    assert response.json() == {'detail': busy_detail()}
    for leaked in ('pg_advisory', 'SELECT', 'lock_key', expected_sqlstate, 'timeout'):
        assert leaked not in response.text
    if writer == 'confirmation':
        assert response.headers['cache-control'] == 'no-store'
    assert counts() == before_counts
    assert account_rows() == before_rows

    if writer == 'confirmation':
        applied = post(client, key)
        assert applied.status_code == 201
        assert applied.json()['replayed'] is False
        assert counts() == (2,1,2)
        replay = post(client, key)
        assert replay.status_code == 200
        assert replay.json()['replayed'] is True
        assert replay.json()['account_references'] == applied.json()['account_references']
        assert counts() == (2,1,2)
        assert [row for row in account_rows() if row['id'] == account_id] == before_rows
