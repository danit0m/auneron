"""
VALUE-3.4D-2b -- relatorio de auditoria da proveniencia da evidencia
(ESTRITAMENTE SOMENTE LEITURA).

    python scripts/evidence_provenance_report.py report [--limit N]

Apresenta, em JSON:

* identidade de schema: revisao ESPERADA (codigo) x REAL (banco);
* produtor: spec, fingerprint medido x pin;
* contextos de proveniencia (mais recentes primeiro, ate --limit), cada um
  com a verificacao de integridade (o `epc1` armazenado == recomputado das
  colunas), contagem de observations e de passes distintos;
* contagens: observed_fact COM proveniencia, observed_fact LEGADO
  (proveniencia NULL, pre-D-2b), human_assessment (nao recebe proveniencia
  automatica; qualquer um COM proveniencia seria violacao).

Saidas: 0 = relatorio gerado e todas as linhas integras; 2 = integridade
violada (digest != recomputado, ou human_assessment com proveniencia);
3 = banco indisponivel (fail-closed). Nunca imprime traceback.

Este script NAO corrige contexto, NAO faz backfill, NAO valida constraint,
NAO altera floor, NAO ativa worker e NAO migra banco. A conexao usa
`SET TRANSACTION READ ONLY` e emite somente SELECT.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

EXIT_OK = 0
EXIT_INTEGRITY = 2
EXIT_UNAVAILABLE = 3


class DatabaseUnavailableError(RuntimeError):
    pass


def _schema_section(connection) -> dict:
    from sqlalchemy import text

    from app.core.schema_identity import CODE_MISMATCH
    from app.core.schema_identity import SchemaIdentityError
    from app.core.schema_identity import expected_schema_revision
    from app.core.schema_identity import is_valid_revision
    from app.core.schema_identity import revision_view

    expected = actual = None
    code = None
    try:
        expected = expected_schema_revision()
    except SchemaIdentityError as error:
        code = error.code
    try:
        rows = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).fetchall()
        if len(rows) == 1 and is_valid_revision(rows[0][0]):
            actual = str(rows[0][0])
        elif code is None:
            code = "actual_revision_unavailable"
    except Exception:
        connection.rollback()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        if code is None:
            code = "actual_revision_unavailable"

    match = expected is not None and expected == actual
    if code is None and not match:
        code = CODE_MISMATCH
    return {
        "expected_schema_revision": revision_view(expected),
        "actual_database_revision": revision_view(actual),
        "match": match,
        "code": code,
    }


def _producer_section() -> dict:
    from app.core.evidence_producer_spec import PINNED_PRODUCER_FINGERPRINT
    from app.core.evidence_producer_spec import PRODUCER_SPEC
    from app.core.evidence_producer_spec import (
        diagnose_producer_fingerprint,
    )

    diagnosis = diagnose_producer_fingerprint()
    return {
        "producer_spec": PRODUCER_SPEC,
        "pinned_fingerprint": PINNED_PRODUCER_FINGERPRINT,
        "measured_fingerprint": (
            diagnosis.measured.digest if diagnosis.measured else None
        ),
        "state": diagnosis.state,
    }


def build_report(connection, *, limit: int) -> dict:
    from sqlalchemy import func
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.models.escalation_observation import EscalationObservation
    from app.models.evidence_provenance_context import (
        EvidenceProvenanceContext,
    )
    from app.services.evidence_provenance_service import (
        verify_row_integrity,
    )

    session = Session(bind=connection)
    try:
        counts = dict(
            session.execute(
                select(
                    EscalationObservation.observation_type,
                    func.count(),
                ).group_by(EscalationObservation.observation_type)
            ).all()
        )
        observed_with = session.execute(
            select(func.count()).where(
                EscalationObservation.observation_type
                == "observed_fact",
                EscalationObservation.provenance_context_id.is_not(None),
            )
        ).scalar_one()
        observed_legacy = session.execute(
            select(func.count()).where(
                EscalationObservation.observation_type
                == "observed_fact",
                EscalationObservation.provenance_context_id.is_(None),
            )
        ).scalar_one()
        human_with = session.execute(
            select(func.count()).where(
                EscalationObservation.observation_type
                == "human_assessment",
                (
                    EscalationObservation.provenance_context_id.is_not(
                        None
                    )
                    | EscalationObservation.producer_pass_id.is_not(None)
                ),
            )
        ).scalar_one()

        per_context = {
            context_id: (observations, passes)
            for context_id, observations, passes in session.execute(
                select(
                    EscalationObservation.provenance_context_id,
                    func.count(),
                    func.count(
                        func.distinct(EscalationObservation.producer_pass_id)
                    ),
                )
                .where(
                    EscalationObservation.provenance_context_id.is_not(
                        None
                    )
                )
                .group_by(EscalationObservation.provenance_context_id)
            ).all()
        }

        total_contexts = session.execute(
            select(func.count()).select_from(EvidenceProvenanceContext)
        ).scalar_one()
        rows = (
            session.execute(
                select(EvidenceProvenanceContext)
                .order_by(EvidenceProvenanceContext.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

        integrity_failures = 0
        contexts = []
        for row in rows:
            integrity_ok = verify_row_integrity(row)
            integrity_failures += 0 if integrity_ok else 1
            observations, passes = per_context.get(row.id, (0, 0))
            contexts.append(
                {
                    "id": row.id,
                    "context_digest": row.context_digest,
                    "context_digest_algorithm": (
                        row.context_digest_algorithm
                    ),
                    "integrity_ok": integrity_ok,
                    "git_sha": row.git_sha,
                    "git_dirty": row.git_dirty,
                    "source_digest_algorithm": row.source_digest_algorithm,
                    "source_digest": row.source_digest,
                    "source_file_count": row.source_file_count,
                    "producer_spec": row.producer_spec,
                    "producer_fingerprint_algorithm": (
                        row.producer_fingerprint_algorithm
                    ),
                    "producer_fingerprint": row.producer_fingerprint,
                    "activation_floor": row.activation_floor.isoformat(),
                    "expected_schema_revision": (
                        row.expected_schema_revision
                    ),
                    "actual_database_revision": (
                        row.actual_database_revision
                    ),
                    "created_at": row.created_at.isoformat(),
                    "observation_count": observations,
                    "pass_count": passes,
                }
            )
    finally:
        session.close()

    if human_with:
        integrity_failures += 1

    return {
        "schema": _schema_section(connection),
        "producer": _producer_section(),
        "context_total": total_contexts,
        "contexts_listed": len(contexts),
        "contexts": contexts,
        "observations": {
            "observed_fact_with_provenance": observed_with,
            "observed_fact_legacy_without_provenance": observed_legacy,
            "human_assessment": counts.get("human_assessment", 0),
            "human_assessment_with_provenance_VIOLATION": human_with,
        },
        "integrity_failures": integrity_failures,
    }


def _connect_and_report(limit: int) -> dict:
    # Import tardio: ambiente ausente/invalido vira fail-closed.
    try:
        from sqlalchemy import text

        from app.database.database import engine
    except Exception as error:
        raise DatabaseUnavailableError(
            f"settings_unavailable:{type(error).__name__}"
        ) from None

    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            report = build_report(connection, limit=limit)
            report["database"] = engine.url.database
            return report
    except DatabaseUnavailableError:
        raise
    except Exception as error:
        raise DatabaseUnavailableError(
            f"database_unavailable:{type(error).__name__}"
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evidence_provenance_report.py",
        description="Auditoria somente leitura da proveniencia (D-2b).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report")
    report.add_argument("--limit", type=int, default=20)
    arguments = parser.parse_args(argv)

    if not 1 <= arguments.limit <= 1000:
        print("EVIDENCE PROVENANCE REPORT FAIL: invalid_limit")
        return EXIT_INTEGRITY

    try:
        result = _connect_and_report(arguments.limit)
    except DatabaseUnavailableError as error:
        print(f"EVIDENCE PROVENANCE REPORT UNAVAILABLE: {error}")
        return EXIT_UNAVAILABLE

    print(json.dumps(result, sort_keys=True, default=str))
    return EXIT_OK if result["integrity_failures"] == 0 else EXIT_INTEGRITY


if __name__ == "__main__":
    sys.exit(main())
