"""
VALUE-3.4D-2b -- scripts/evidence_provenance_report.py (auditoria).

ESTRITAMENTE somente leitura: recomputa `epc1`, verifica integridade do
contexto, conta legado, apresenta revisao esperada x real e diagnostica a
proveniencia. NAO corrige, NAO faz backfill, NAO valida constraint, NAO
altera floor, NAO ativa worker, NAO migra banco.
"""

from __future__ import annotations

import ast
import io
import json
import re
import uuid
from contextlib import redirect_stdout
from datetime import date
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.orm import Session

import scripts.evidence_provenance_report as report_cli
from app.database.database import engine
from app.services import evidence_provenance_service as eps

from evidence_provenance_helpers import make_binding
from evidence_provenance_helpers import purge_contexts
from test_escalation_observation_model import _account
from test_escalation_observation_model import _account_event
from test_escalation_observation_model import _escalation_work_item
from test_escalation_observation_model import _user


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "evidence_provenance_report.py"
)

@pytest.fixture(autouse=True)
def _purge_provenance_contexts():
    purge_contexts()
    yield
    purge_contexts()


OBSERVED_FACT = text(
    "INSERT INTO escalation_observations (escalation_work_item_id, "
    "observation_type, linked_account_event_id, observed_at, "
    "idempotency_key, provenance_context_id, producer_pass_id) VALUES "
    "(:w, 'observed_fact', :e, now(), :k, :c, :p)"
)


def run_main(*argv: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = report_cli.main(list(argv))
    return code, buffer.getvalue()


def new_observation(db_session: Session, binding) -> int:
    due = date.today() - timedelta(days=10)
    tag = uuid.uuid4().hex[:10]
    account = _account(
        db_session, email=f"report-{tag}@example.com", vencimento=due
    )
    actor = _user(db_session, email=f"report-actor-{tag}@example.com")
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due, actor_user=actor
    )
    event_row = _account_event(
        db_session,
        account=account,
        occurred_at=work_item.created_at + timedelta(hours=1),
    )
    with engine.begin() as connection:
        connection.execute(
            OBSERVED_FACT,
            {
                "w": work_item.id,
                "e": event_row.id,
                "k": f"report-{tag}",
                "c": binding.context_id,
                "p": str(binding.pass_id),
            },
        )
    return work_item.id


def test_report_lists_contexts_with_integrity_and_counts(
    db_session: Session,
) -> None:
    binding = make_binding()
    new_observation(db_session, binding)

    code, output = run_main("report", "--limit", "50")

    assert code == report_cli.EXIT_OK
    report = json.loads(output)
    assert report["database"] == "auneron_test"
    assert report["integrity_failures"] == 0
    entry = next(
        c for c in report["contexts"] if c["id"] == binding.context_id
    )
    assert entry["integrity_ok"] is True
    assert entry["observation_count"] == 1
    assert entry["pass_count"] == 1
    assert entry["context_digest_algorithm"] == "epc1"
    assert entry["producer_spec"] == "escalation_payment_observation:v1"
    assert entry["git_dirty"] is False
    assert report["observations"]["observed_fact_with_provenance"] >= 1
    assert (
        report["observations"]["human_assessment_with_provenance_VIOLATION"]
        == 0
    )


def test_report_shows_expected_and_actual_revisions_and_the_producer() -> None:
    code, output = run_main("report", "--limit", "1")

    report = json.loads(output)
    schema = report["schema"]
    assert set(schema) == {
        "expected_schema_revision",
        "actual_database_revision",
        "match",
        "code",
    }
    assert schema["expected_schema_revision"] is not None
    assert schema["actual_database_revision"] is not None
    assert schema["match"] is True
    assert schema["code"] is None
    producer = report["producer"]
    assert producer["producer_spec"] == "escalation_payment_observation:v1"
    assert producer["state"] == "valid"
    assert producer["measured_fingerprint"] == producer["pinned_fingerprint"]
    assert code == report_cli.EXIT_OK


def test_report_counts_legacy_observed_facts(db_session: Session) -> None:
    due = date.today() - timedelta(days=10)
    tag = uuid.uuid4().hex[:10]
    account = _account(
        db_session, email=f"legacy-{tag}@example.com", vencimento=due
    )
    actor = _user(db_session, email=f"legacy-actor-{tag}@example.com")
    work_item = _escalation_work_item(
        db_session, account=account, due_date=due, actor_user=actor
    )
    event_row = _account_event(
        db_session,
        account=account,
        occurred_at=work_item.created_at + timedelta(hours=1),
    )
    constraint = "ck_escalation_observations_provenance_by_type"
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text(
                f"ALTER TABLE escalation_observations "
                f"DROP CONSTRAINT {constraint}"
            )
        )
        connection.execute(
            OBSERVED_FACT,
            {
                "w": work_item.id,
                "e": event_row.id,
                "k": f"legacy-{tag}",
                "c": None,
                "p": None,
            },
        )
        connection.execute(
            text(
                f"ALTER TABLE escalation_observations ADD CONSTRAINT "
                f"{constraint} CHECK ((observation_type = 'observed_fact' "
                "AND provenance_context_id IS NOT NULL AND "
                "producer_pass_id IS NOT NULL) OR (observation_type = "
                "'human_assessment' AND provenance_context_id IS NULL AND "
                "producer_pass_id IS NULL)) NOT VALID"
            )
        )
        connection.execute(text("SET TRANSACTION READ ONLY"))
        report = report_cli.build_report(connection, limit=5)
    finally:
        transaction.rollback()
        connection.close()

    assert (
        report["observations"]["observed_fact_legacy_without_provenance"]
        >= 1
    )
    # legado e informativo: nao e falha de integridade
    assert report["integrity_failures"] == 0


def test_report_exits_2_when_a_context_fails_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_binding()
    monkeypatch.setattr(eps, "verify_row_integrity", lambda row: False)

    code, output = run_main("report", "--limit", "3")

    report = json.loads(output)
    assert code == report_cli.EXIT_INTEGRITY
    assert report["integrity_failures"] >= 1
    assert any(c["integrity_ok"] is False for c in report["contexts"])


def test_report_exits_3_when_the_database_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(limit):
        raise report_cli.DatabaseUnavailableError("database_unavailable:X")

    monkeypatch.setattr(report_cli, "_connect_and_report", unavailable)

    code, output = run_main("report")

    assert code == report_cli.EXIT_UNAVAILABLE
    assert "UNAVAILABLE" in output
    assert "Traceback" not in output


@pytest.mark.parametrize("limit", ["0", "1001", "-5"])
def test_report_rejects_an_invalid_limit(limit: str) -> None:
    code, output = run_main("report", "--limit", limit)

    assert code == report_cli.EXIT_INTEGRITY
    assert "invalid_limit" in output


def test_report_emits_only_read_statements() -> None:
    statements: list[str] = []

    def listener(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        run_main("report", "--limit", "3")
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert statements
    for statement in statements:
        assert re.match(
            r"^\s*(SELECT|SET TRANSACTION READ ONLY)\b", statement, re.I
        ), statement


def test_report_does_not_leak_connection_secrets() -> None:
    _, output = run_main("report", "--limit", "3")

    assert "password" not in output.lower()
    assert "postgresql" not in output.lower()
    assert "test_password" not in output


def test_script_is_read_only_by_construction() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }

    # `sys.path.insert(0, ...)` e a unica ocorrencia legitima de `insert`
    sys_path_inserts = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "insert"
        and ast.unparse(node.func.value) == "sys.path"
    )
    insert_calls = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "insert"
    )
    assert insert_calls == sys_path_inserts

    assert not calls & {
        "add",
        "add_all",
        "commit",
        "delete",
        "merge",
        "flush",
        "bulk_save_objects",
        "update",
        "upgrade",
        "downgrade",
        "stamp",
    }
    code = "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    for forbidden in (
        "INSERT",
        "UPDATE ",
        "DELETE",
        "ALTER ",
        "CREATE ",
        "DROP ",
        "TRUNCATE",
        "VALIDATE",
        "backfill(",
        "activation_floor =",
        "MAINTENANCE_ENABLED",
    ):
        assert forbidden not in code, forbidden
    assert "SET TRANSACTION READ ONLY" in code
