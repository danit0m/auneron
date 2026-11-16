"""INTELLIGENCE S2 -- local-model identification migration (offline).

No database: the migration is executed against a recording `op`, and
its CHECK text is compared with the SQLAlchemy model so the two cannot
drift. The real upgrade/downgrade on PostgreSQL is covered by
test_intelligence_model_identification.py (needs TEST_DATABASE_URL).
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint

from app.models.intelligence_budget import IntelligenceModelCall


BACKEND = Path(__file__).resolve().parents[1]
VERSION = (
    BACKEND
    / "migrations"
    / "versions"
    / "c8e2a5f1d704_add_model_call_local_identification.py"
)
CALLS = "intelligence_model_calls"


@pytest.fixture(autouse=True)
def clean_database():
    """Overrides the global DB-cleaning fixture: nothing here uses it."""
    yield


def load():
    spec = importlib.util.spec_from_file_location("s2_migration", VERSION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Bind:
    def __init__(self, ollama_rows: int) -> None:
        self.ollama_rows = ollama_rows
        self.statements: list[str] = []

    def execute(self, statement):
        self.statements.append(str(statement))
        rows = self.ollama_rows

        class Result:
            def scalar(self_inner):
                return rows

        return Result()


class Recorder:
    def __init__(self, ollama_rows: int = 0) -> None:
        self.ops: list[tuple] = []
        self.bind = Bind(ollama_rows)

    def get_bind(self):
        return self.bind

    def add_column(self, table, column):
        self.ops.append(("add_column", table, column.name, column.nullable))

    def drop_column(self, table, name):
        self.ops.append(("drop_column", table, name))

    def drop_constraint(self, name, table, type_=None):
        self.ops.append(("drop_constraint", name, table))

    def create_check_constraint(self, name, table, condition):
        self.ops.append(("create_check", name, table, condition))


def model_checks() -> dict[str, str]:
    return {
        c.name: str(c.sqltext)
        for c in IntelligenceModelCall.__table__.constraints
        if isinstance(c, CheckConstraint)
    }


def test_revision_chain_is_linear_on_the_previous_head() -> None:
    migration = load()

    assert migration.revision == "c8e2a5f1d704"
    assert migration.down_revision == "d5b8e2f4a671"
    assert migration.branch_labels is None


def test_upgrade_adds_only_nullable_columns_and_the_expected_checks() -> None:
    migration = load()
    recorder = Recorder()
    migration.op = recorder
    migration.upgrade()

    added = [op for op in recorder.ops if op[0] == "add_column"]
    assert sorted(op[2] for op in added) == [
        "latency_ms",
        "model_digest",
        "provider_reported_model",
        "runtime_version",
    ]
    assert all(op[1] == CALLS and op[3] is True for op in added)

    created = {op[1]: op[3] for op in recorder.ops if op[0] == "create_check"}
    assert set(created) == {
        "ck_intelligence_model_calls_provider_valid",
        "ck_intelligence_model_calls_model_identification_format",
        "ck_intelligence_model_calls_ollama_settled_identified",
    }
    assert created[
        "ck_intelligence_model_calls_provider_valid"
    ] == "provider IN ('fake', 'ollama')"
    assert recorder.ops.index(
        ("drop_constraint", "ck_intelligence_model_calls_provider_valid", CALLS)
    ) < next(
        i
        for i, op in enumerate(recorder.ops)
        if op[:2] == ("create_check", "ck_intelligence_model_calls_provider_valid")
    )


def test_migration_check_text_equals_the_model() -> None:
    migration = load()
    recorder = Recorder()
    migration.op = recorder
    migration.upgrade()
    model = model_checks()

    for op in recorder.ops:
        if op[0] == "create_check":
            assert " ".join(model[op[1]].split()) == " ".join(
                op[3].split()
            ), op[1]


def test_downgrade_reverses_the_upgrade_when_no_ollama_rows() -> None:
    migration = load()
    recorder = Recorder(ollama_rows=0)
    migration.op = recorder
    migration.downgrade()

    dropped_checks = [op[1] for op in recorder.ops if op[0] == "drop_constraint"]
    assert set(dropped_checks) == {
        "ck_intelligence_model_calls_model_identification_format",
        "ck_intelligence_model_calls_ollama_settled_identified",
        "ck_intelligence_model_calls_provider_valid",
    }
    restored = [op for op in recorder.ops if op[0] == "create_check"]
    assert restored == [
        (
            "create_check",
            "ck_intelligence_model_calls_provider_valid",
            CALLS,
            "provider IN ('fake')",
        )
    ]
    assert sorted(op[2] for op in recorder.ops if op[0] == "drop_column") == [
        "latency_ms",
        "model_digest",
        "provider_reported_model",
        "runtime_version",
    ]
    # constraints are dropped before the columns they reference
    last_constraint = max(
        i for i, op in enumerate(recorder.ops) if op[0] == "drop_constraint"
    )
    first_column = min(
        i for i, op in enumerate(recorder.ops) if op[0] == "drop_column"
    )
    assert last_constraint < first_column


def test_downgrade_refuses_without_touching_anything_when_ollama_rows_exist() -> None:
    migration = load()
    recorder = Recorder(ollama_rows=3)
    migration.op = recorder

    with pytest.raises(RuntimeError, match="Downgrade refused"):
        migration.downgrade()

    assert recorder.ops == []  # nothing dropped, no row deleted
    assert all(
        "DELETE" not in s.upper() for s in recorder.bind.statements
    )
