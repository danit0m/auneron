"""
PR-4 -- verify_recovery.py

Layer A -- Database Recovery Verification. Compara o banco de origem
contra RECOVERY_DATABASE recém-restaurado: conjunto dinâmico de
tabelas (table_schema='public' AND table_type='BASE TABLE'), COUNT(*)
por tabela, e alembic_version. Fail-closed: qualquer divergência
levanta RecoveryVerificationFailedError -- nunca produz só um
relatório e segue em frente.

Não implementa checagem de FK própria -- essa garantia vem do próprio
exit code (0) de pg_restore sem --disable-triggers (ver
restore_postgres.py: o PostgreSQL revalida toda constraint contra os
dados restaurados como parte do ALTER TABLE ADD CONSTRAINT que o
pg_dump emite, a menos que --disable-triggers seja usado -- o que
nunca é o caso aqui).

Toda consulta roda via `docker exec ... psql`, nunca conexão
SQLAlchemy direta ao host -- mesma via de acesso do backup/restore,
funciona identicamente em dev e produção (onde a rede "db" é
internal, sem porta exposta ao host).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from scripts._postgres_recovery_common import RECOVERY_DATABASE
from scripts._postgres_recovery_common import docker_exec_run


TABLE_SET_QUERY = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema='public' AND table_type='BASE TABLE' "
    "ORDER BY table_name;"
)

ALEMBIC_REVISION_QUERY = "SELECT version_num FROM alembic_version;"


class RecoveryVerificationFailedError(Exception):
    """
    Fail-closed: levantada sempre que Layer A encontra qualquer
    divergência. Nunca substituída por um relatório silencioso.
    """


@dataclass(frozen=True)
class VerificationResult:
    table_count: int
    row_counts: dict[str, int]
    alembic_revision: str


def _psql_rows(database: str, query: str) -> list[str]:
    result = docker_exec_run(
        "psql", "-U", "auneron", "-d", database, "-tAc", query
    )
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _table_set(database: str) -> set[str]:
    return set(_psql_rows(database, TABLE_SET_QUERY))


def _row_counts(database: str, tables: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in sorted(tables):
        rows = _psql_rows(
            database, f'SELECT COUNT(*) FROM "{table}";'
        )
        counts[table] = int(rows[0])
    return counts


def _alembic_revision(database: str) -> str:
    rows = _psql_rows(database, ALEMBIC_REVISION_QUERY)
    return rows[0] if rows else ""


def verify_recovery(
    *,
    source_database: str,
    target_database: str,
) -> VerificationResult:
    if target_database != RECOVERY_DATABASE:
        raise RecoveryVerificationFailedError(
            f"Verificação só é permitida contra {RECOVERY_DATABASE!r}; "
            f"recebido {target_database!r}."
        )

    source_tables = _table_set(source_database)
    target_tables = _table_set(target_database)

    if source_tables != target_tables:
        missing = source_tables - target_tables
        extra = target_tables - source_tables
        raise RecoveryVerificationFailedError(
            "Conjunto de tabelas diverge. "
            f"Ausentes no destino: {sorted(missing)}. "
            f"Extras no destino: {sorted(extra)}."
        )

    source_counts = _row_counts(source_database, source_tables)
    target_counts = _row_counts(target_database, target_tables)

    if source_counts != target_counts:
        diffs = {
            table: (source_counts[table], target_counts[table])
            for table in source_counts
            if source_counts[table] != target_counts[table]
        }
        raise RecoveryVerificationFailedError(
            f"Cardinalidades divergem: {diffs}."
        )

    source_revision = _alembic_revision(source_database)
    target_revision = _alembic_revision(target_database)

    if source_revision != target_revision:
        raise RecoveryVerificationFailedError(
            "Revisão Alembic diverge: "
            f"origem={source_revision!r} destino={target_revision!r}."
        )

    return VerificationResult(
        table_count=len(target_tables),
        row_counts=target_counts,
        alembic_revision=target_revision,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Layer A -- Database Recovery Verification."
    )
    parser.add_argument("--source-database", required=True)
    parser.add_argument("--target-database", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = verify_recovery(
        source_database=args.source_database,
        target_database=args.target_database,
    )
    print(f"Tabelas verificadas: {result.table_count}")
    print(f"Revisão Alembic: {result.alembic_revision}")
    print("Layer A -- Database Recovery Verification: PASS")


if __name__ == "__main__":
    main()
