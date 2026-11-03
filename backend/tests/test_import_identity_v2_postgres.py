"""Native TI2 gate: execute only in authorized disposable CI."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest
from sqlalchemy import text
from app.database.database import SessionLocal, engine
from app.services import import_preview_parser as parser
from app.services import import_confirmation_execution as execution
from app.services import import_confirmation_repository as base
from app.services import import_confirmation_repository_v2 as adapter
from app.services import import_identity_v2 as v2

@pytest.fixture(autouse=True)
def clean_receipts(clean_database):
    def clean():
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE import_confirmation_audit_events, import_confirmation_receipts RESTART IDENTITY"))
    clean()
    yield
    clean()

def repository():
    return adapter.PostgresImportIdentityV2Repository(
        base.PostgresImportConfirmationRepository(SessionLocal, execution, writer_protocol_verified=True),
        base, v2)

def plan(reference="SYNTH-001", key=None, value="10,00"):
    data = ("cliente;valor;vencimento;status;referencia\r\n"
            f"Cliente Sintetico TI2;{value};2026-01-05;aberto;{reference}").encode()
    preview = parser.parse_import_preview("preview.csv", data, identity_version=2, origem="synthetic-ci")
    return v2.plan_import_confirmation_v2(parser.parse_import_preview, data, "csv",
        origem="synthetic-ci", expected_file_sha256=preview.file_sha256,
        expected_content_sha256=preview.content_sha256, idempotency_key=key or str(uuid.uuid4()))

def run(candidate):
    return v2.execute_import_confirmation_v2(repository(), candidate, actor_reference="user:7")

def counts():
    with engine.connect() as connection:
        return tuple(connection.execute(text("SELECT count(*) FROM " + name)).scalar_one()
                     for name in ("accounts", "account_title_references",
                                  "import_confirmation_receipts", "import_confirmation_audit_events"))

def test_native_create_replay_and_new_key_match():
    candidate = plan()
    first = run(candidate)
    before = counts()
    assert before[:3] == (1, 1, 1)
    assert before[3] > 0
    replay = run(candidate)
    assert replay.replayed
    assert replay.receipt.account_references == first.receipt.account_references
    assert counts() == before
    with pytest.raises(v2.ImportIdentityV2Error) as caught:
        run(plan())
    assert counts() == before

def test_native_same_reference_changed_payload_is_conflict():
    run(plan())
    before = counts()
    with pytest.raises(v2.ImportIdentityV2Error) as caught:
        run(plan(value="11,00"))
    assert caught.value.code == "reference_conflict"
    assert counts() == before

def test_native_distinct_references_allow_equal_financial_tuple():
    run(plan())
    run(plan(reference="SYNTH-002"))
    assert counts()[:3] == (2, 2, 2)

def test_native_failure_after_reference_write_rolls_back():
    original = adapter._UnitOfWorkV2.create_title_reference
    def fail_after_write(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("synthetic rollback")
    with mock.patch.object(adapter._UnitOfWorkV2, "create_title_reference", fail_after_write):
        with pytest.raises(RuntimeError, match="synthetic rollback"):
            run(plan())
    assert counts() == (0, 0, 0, 0)

def test_native_concurrent_same_key_replays_once():
    candidate = plan()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, candidate) for _ in range(2)]
        results = [future.result(timeout=25) for future in futures]
    assert sorted(result.replayed for result in results) == [False, True]
    assert counts()[:3] == (1, 1, 1)

def test_native_concurrent_reference_conflict_creates_one():
    candidates = [plan(), plan(value="11,00")]
    def attempt(candidate):
        try:
            run(candidate)
            return "created"
        except v2.ImportIdentityV2Error as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, candidate) for candidate in candidates]
        outcomes = sorted(future.result(timeout=25) for future in futures)
    assert outcomes == ["created", "reference_conflict"]
    assert counts()[:3] == (1, 1, 1)
