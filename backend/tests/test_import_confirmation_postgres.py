"""REAL PostgreSQL gate. Do not run/collect locally outside the authorized launcher.
Existing conftest truncates 24 tables; this module additionally truncates the
2 import tables before/after each test (26 total). All data is synthetic.
No application route is enabled and no authentication dependency is overridden.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from app.api.routes.import_confirm import create_import_confirm_router
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
    # Only this isolated test harness declares True, never app.main.
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
