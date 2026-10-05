"""
VALUE-3.4D-2b -- guardas estaticas da proveniencia persistente.

* a proveniencia e METADADO de evidencia: nenhum consumidor de decisao
  (NBA, policy, L3, aprovacao) a importa nem a le;
* o contexto so e escrito pelo servico de proveniencia;
* a migration e aditiva, NOT VALID, sem backfill, sem `VALIDATE`, com
  triggers reversiveis; o modelo espelha exatamente o schema congelado;
* `conftest.py`, GET/schemas/UI e compose/Docker/workflow ficam intactos.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.schema_identity import expected_schema_revision
from app.models.escalation_observation import EscalationObservation
from app.models.evidence_provenance_context import EvidenceProvenanceContext


BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"
REPO_DIR = BACKEND_DIR.parent
MIGRATION = (
    BACKEND_DIR
    / "migrations"
    / "versions"
    / "5c1e7a90d2b4_add_evidence_provenance.py"
)

PROVENANCE_MODULES = (
    "app.services.evidence_provenance_service",
    "app.core.evidence_producer_spec",
    "app.core.schema_identity",
)

PRODUCTION_IMPORTERS_ALLOWED = {
    "app/services/evidence_provenance_service.py",
    "app/services/escalation_observation_service.py",
    "app/core/escalation_payment_observation_maintenance.py",
    "app/main.py",
}


def app_sources() -> dict[str, str]:
    return {
        path.relative_to(BACKEND_DIR).as_posix(): path.read_text(
            encoding="utf-8"
        )
        for path in APP_DIR.rglob("*.py")
    }


def imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


# ---------------------------------------------------------------------
# 1. nenhum consumidor de decisao
# ---------------------------------------------------------------------


def test_only_the_evidence_path_imports_the_provenance_modules() -> None:
    importers = {
        path
        for path, source in app_sources().items()
        if imported_modules(source) & set(PROVENANCE_MODULES)
    }

    assert importers <= PRODUCTION_IMPORTERS_ALLOWED, sorted(
        importers - PRODUCTION_IMPORTERS_ALLOWED
    )


@pytest.mark.parametrize(
    "fragment",
    [
        "nba",
        "policy",
        "approval",
        "orchestrator",
        "risk_agent",
        "agents/",
        "decision",
        "authority",
    ],
)
def test_no_decision_module_touches_the_provenance(fragment: str) -> None:
    offenders = []
    for path, source in app_sources().items():
        if fragment not in path:
            continue
        if imported_modules(source) & set(PROVENANCE_MODULES):
            offenders.append(path)
        if re.search(
            r"provenance_context_id|producer_pass_id|EvidenceProvenance",
            source,
        ):
            offenders.append(path)

    assert offenders == []


def test_provenance_columns_are_read_only_by_the_evidence_path() -> None:
    allowed = {
        "app/models/escalation_observation.py",
        "app/models/evidence_provenance_context.py",
        "app/models/__init__.py",
        "app/services/escalation_observation_service.py",
        "app/services/evidence_provenance_service.py",
        # resumo do pass: `provenance_context_id` (diagnostico)
        "app/core/escalation_payment_observation_maintenance.py",
    }
    pattern = re.compile(
        r"provenance_context_id|producer_pass_id|EvidenceProvenanceContext"
    )
    users = {
        path for path, source in app_sources().items() if pattern.search(source)
    }

    assert users <= allowed, sorted(users - allowed)


def test_the_context_model_is_only_imported_by_the_service_and_models() -> None:
    importers = {
        path
        for path, source in app_sources().items()
        if "app.models.evidence_provenance_context" in imported_modules(source)
    }

    assert importers == {
        "app/models/__init__.py",
        "app/services/evidence_provenance_service.py",
    }


def test_the_context_is_written_only_by_the_provenance_service() -> None:
    offenders = []
    for path, source in app_sources().items():
        if path in (
            "app/services/evidence_provenance_service.py",
            "app/models/evidence_provenance_context.py",
            # FK da coluna de proveniencia (modelo da observation)
            "app/models/escalation_observation.py",
        ):
            continue
        if re.search(r"evidence_provenance_contexts", source):
            offenders.append(path)

    assert offenders == []


def test_the_service_has_no_update_or_delete_path_for_the_context() -> None:
    source = (
        APP_DIR / "services" / "evidence_provenance_service.py"
    ).read_text(encoding="utf-8")
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }

    assert not calls & {
        "delete",
        "merge",
        "update",
        "bulk_update_mappings",
        "bulk_save_objects",
        "add_all",
    }
    assert "postgresql_insert" in calls
    assert "on_conflict_do_nothing" in calls


# ---------------------------------------------------------------------
# 2. migration
# ---------------------------------------------------------------------


def test_migration_is_the_single_head_and_follows_the_previous_head() -> None:
    text = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "5c1e7a90d2b4"' in text
    assert 'down_revision = "7432a1c2dd66"' in text
    assert expected_schema_revision() == "5c1e7a90d2b4"


def test_migration_is_additive_not_valid_and_has_no_backfill() -> None:
    text = MIGRATION.read_text(encoding="utf-8")

    assert text.count("NOT VALID") >= 1
    assert "VALIDATE" not in text.upper().replace("NOT VALID", "")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert (
        re.search(
            r"INSERT INTO|UPDATE\s+\w+\s+SET|DELETE FROM", code, re.I
        )
        is None
    )
    assert "backfill" not in code.lower()


def test_migration_is_safe_for_text_and_psycopg_placeholders() -> None:
    text = MIGRATION.read_text(encoding="utf-8")

    # `:v` num CHECK viraria bind em `text()`; `%` quebra o psycopg
    assert "[:]v" in text
    assert not re.search(r":v\[", text)
    assert "%" not in text.replace("%s", "")


def test_migration_downgrade_removes_every_new_object() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    downgrade = text[text.index("def downgrade()") :]

    for fragment in (
        "DROP TRIGGER",
        "DROP FUNCTION",
        "drop_constraint",
        "drop_index",
        "drop_column",
        "drop_table",
    ):
        assert fragment in downgrade, fragment
    assert downgrade.count("drop_column") == 2


def _alembic_sql(*arguments: str) -> str:
    environment = dict(os.environ)
    environment.update(
        APP_ENV="test",
        DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/auneron_test",
        API_KEY="auneron-offline-sql-render-key-000000000",
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments, "--sql"],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-1500:]
    return result.stdout


def test_offline_upgrade_sql_is_the_frozen_set_of_statements() -> None:
    sql = _alembic_sql("upgrade", "7432a1c2dd66:5c1e7a90d2b4")

    statements = [
        line
        for line in sql.splitlines()
        if re.match(r"^(CREATE|ALTER)\b", line)
    ]
    assert len(statements) == 11
    assert sum("CREATE TABLE evidence_provenance_contexts" in s for s in statements) == 1
    assert sum("ADD COLUMN provenance_context_id BIGINT" in s for s in statements) == 1
    assert sum("ADD COLUMN producer_pass_id UUID" in s for s in statements) == 1
    assert sum("ON DELETE RESTRICT" in s for s in statements) == 1
    assert sum(s.startswith("CREATE INDEX") for s in statements) == 2
    assert sum("NOT VALID" in s for s in statements) == 1
    assert sum(s.startswith("CREATE FUNCTION") for s in statements) == 2
    assert sum(s.startswith("CREATE TRIGGER") for s in statements) == 2
    assert "WHERE" not in " ".join(
        s for s in statements if s.startswith("CREATE INDEX")
    )  # indice NAO parcial (D-b7)
    assert "UPDATE alembic_version SET version_num='5c1e7a90d2b4'" in sql
    assert "VALIDATE" not in sql


def test_offline_downgrade_sql_reverses_everything() -> None:
    sql = _alembic_sql("downgrade", "5c1e7a90d2b4:7432a1c2dd66")

    for fragment in (
        "DROP TRIGGER trg_escalation_observations_provenance_immutable",
        "DROP FUNCTION fn_escalation_observation_provenance_immutable()",
        "DROP TRIGGER trg_evidence_provenance_contexts_immutable",
        "DROP FUNCTION fn_evidence_provenance_context_immutable()",
        "DROP TABLE evidence_provenance_contexts",
        "DROP COLUMN producer_pass_id",
        "DROP COLUMN provenance_context_id",
    ):
        assert fragment in sql, fragment


# ---------------------------------------------------------------------
# 3. modelo == schema congelado
# ---------------------------------------------------------------------


def test_context_model_matches_the_frozen_schema() -> None:
    table = EvidenceProvenanceContext.__table__
    columns = {
        column.name: (type(column.type).__name__, column.nullable)
        for column in table.columns
    }

    assert columns == {
        "id": ("BigInteger", False),
        "context_digest": ("String", False),
        "context_digest_algorithm": ("String", False),
        "git_sha": ("String", False),
        "git_dirty": ("Boolean", False),
        "source_digest_algorithm": ("String", False),
        "source_digest": ("String", False),
        "source_file_count": ("Integer", False),
        "producer_spec": ("String", False),
        "producer_fingerprint_algorithm": ("String", False),
        "producer_fingerprint": ("String", False),
        "activation_floor": ("DateTime", False),
        "expected_schema_revision": ("String", False),
        "actual_database_revision": ("String", False),
        "created_at": ("DateTime", False),
    }
    assert len(columns) == 15
    assert table.c.activation_floor.type.timezone is True
    assert table.c.created_at.type.timezone is True
    uniques = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert uniques == {
        "uq_evidence_provenance_contexts_digest",
        "uq_evidence_provenance_contexts_content",
    }
    content = next(
        c
        for c in table.constraints
        if c.name == "uq_evidence_provenance_contexts_content"
    )
    assert len(content.columns) == 12
    checks = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert checks == {
        "ck_evidence_provenance_contexts_digest_hex",
        "ck_evidence_provenance_contexts_source_digest_hex",
        "ck_evidence_provenance_contexts_fingerprint_hex",
        "ck_evidence_provenance_contexts_git_sha",
        "ck_evidence_provenance_contexts_git_clean",
        "ck_evidence_provenance_contexts_algorithms",
        "ck_evidence_provenance_contexts_producer_spec",
        "ck_evidence_provenance_contexts_file_count",
        "ck_evidence_provenance_contexts_revisions",
    }


def test_observation_model_gets_two_nullable_columns_and_non_partial_indexes() -> None:
    table = EscalationObservation.__table__

    assert table.c.provenance_context_id.nullable is True
    assert table.c.producer_pass_id.nullable is True
    foreign_key = next(iter(table.c.provenance_context_id.foreign_keys))
    assert foreign_key.ondelete == "RESTRICT"
    assert foreign_key.column.table.name == "evidence_provenance_contexts"
    indexes = {
        index.name: index
        for index in table.indexes
        if "provenance" in index.name or "producer_pass" in index.name
    }
    assert set(indexes) == {
        "ix_escalation_observations_provenance_context",
        "ix_escalation_observations_producer_pass",
    }
    for index in indexes.values():
        assert index.dialect_options["postgresql"]["where"] is None


def test_existing_observation_constraints_are_untouched() -> None:
    names = {
        constraint.name for constraint in EscalationObservation.__table__.constraints
    }

    assert {
        "ck_escalation_observations_type_valid",
        "ck_escalation_observations_assessment_code_valid",
        "ck_escalation_observations_declared_by_role_valid",
        "ck_escalation_observations_type_disjoint",
        "ck_escalation_observations_idempotency_key_not_blank",
        "uq_escalation_observations_work_item_idempotency",
        "ck_escalation_observations_provenance_by_type",
    } <= names


# ---------------------------------------------------------------------
# 4. fronteiras: conftest, GET/schema/UI, infra
# ---------------------------------------------------------------------


def test_conftest_is_untouched_by_the_provenance_work() -> None:
    conftest = (BACKEND_DIR / "tests" / "conftest.py").read_text(
        encoding="utf-8"
    )

    assert "provenance" not in conftest.lower()


def test_the_public_observation_schema_does_not_expose_provenance() -> None:
    source = (APP_DIR / "schemas" / "escalation_observation.py").read_text(
        encoding="utf-8"
    )

    assert "provenance" not in source
    assert "producer_pass" not in source


def test_the_ui_does_not_know_about_provenance() -> None:
    ui_types = (
        REPO_DIR / "frontend" / "src" / "types" / "escalationObservation.ts"
    ).read_text(encoding="utf-8")

    assert "provenance" not in ui_types
    assert "producer_pass" not in ui_types


def test_the_worker_does_not_define_new_enablement_gates() -> None:
    worker = (
        APP_DIR / "core" / "escalation_payment_observation_maintenance.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "maintenance_enabled",
        "MAINTENANCE_ENABLED",
        "settings.escalation_payment_observation_activation_floor =",
    ):
        assert forbidden not in worker


def test_procedure_document_describes_the_mechanism_without_authorizing() -> None:
    text = (
        BACKEND_DIR
        / "docs"
        / "operations"
        / "EVIDENCE_ACTIVATION_PROCEDURE.md"
    ).read_text(encoding="utf-8")

    for required in (
        "NÃO autoriza ativação",
        "ATIVAÇÃO BLOQUEADA até D-2",
        "evidence_provenance_report.py report",
        "provenance_blocked",
        "NOT VALID",
        "VALIDATE CONSTRAINT",
        "abstém",
    ):
        assert required in text, required
    assert "backfill" in text.lower() or "sem backfill" in text.lower()
