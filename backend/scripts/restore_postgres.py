"""
PR-4 -- restore_postgres.py

Restaura um artefato de backup (.dump, formato -F c) em
RECOVERY_DATABASE ("auneron_recovery_drill") -- o ÚNICO alvo permitido
por este wrapper V1. Nunca restaura sobre "auneron", "auneron_test",
ou qualquer banco cuja identidade normalizada coincida com
settings.database_url.

pg_restore roda DENTRO do container Postgres já-rodando, sempre com a
revalidação padrão de constraints ativa (a flag que desliga essa
revalidação de gatilhos/constraints durante a carga é proibida pelo
Contract) -- isso garante que o PostgreSQL revalida toda constraint de
FK contra os dados restaurados como parte natural do restore; um exit
code 0 já é a prova de integridade referencial, sem checador de FK
próprio.

O banco RECOVERY_DATABASE é criado por este script antes do restore
(sempre do zero -- falha se já existir, para nunca sobrescrever um
drill anterior por engano).
"""

from __future__ import annotations

import argparse

from scripts._postgres_recovery_common import RECOVERY_DATABASE
from scripts._postgres_recovery_common import assert_safe_cleanup_target
from scripts._postgres_recovery_common import assert_safe_restore_target
from scripts._postgres_recovery_common import docker_cp
from scripts._postgres_recovery_common import docker_exec_run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Restaura um backup .dump em "
            f"{RECOVERY_DATABASE!r} -- único alvo permitido."
        )
    )
    parser.add_argument(
        "--backup-file",
        help="Caminho local do artefato .dump a restaurar.",
    )
    parser.add_argument(
        "--target-dsn",
        help=(
            "DSN completo do banco de destino -- precisa apontar "
            f"para {RECOVERY_DATABASE!r}."
        ),
    )
    parser.add_argument(
        "--target-database",
        required=True,
        help=(
            f"Precisa ser exatamente {RECOVERY_DATABASE!r}. Sem "
            "default -- nunca inferido silenciosamente."
        ),
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help=(
            "Em vez de restaurar, remove (DROP DATABASE) o banco de "
            "recovery. Passa pelo mesmo guard do restore antes de "
            "executar."
        ),
    )
    return parser.parse_args()


def run_restore(*, backup_file: str, target_dsn: str, target_database: str) -> None:
    target_identity = assert_safe_restore_target(
        target_dsn=target_dsn,
        target_database=target_database,
    )

    print(
        "Alvo: "
        f"host={target_identity.host} "
        f"port={target_identity.port} "
        f"database={target_identity.database}"
    )

    docker_exec_run(
        "psql",
        "-U",
        "auneron",
        "-d",
        "postgres",
        "-c",
        f"CREATE DATABASE {RECOVERY_DATABASE};",
    )

    container_path = f"/tmp/{RECOVERY_DATABASE}_restore.dump"
    docker_cp(backup_file, f"auneron-postgres:{container_path}")

    docker_exec_run(
        "pg_restore",
        "-U",
        "auneron",
        "-d",
        RECOVERY_DATABASE,
        "-F",
        "c",
        container_path,
    )

    docker_exec_run("rm", "-f", container_path)

    print(f"Restore concluído em {RECOVERY_DATABASE!r}.")


def run_cleanup(*, database: str) -> None:
    """
    DROP DATABASE nunca acontece num `finally` cego -- passa pela
    mesma barreira de identidade usada no restore antes de executar.
    Só remove exatamente RECOVERY_DATABASE; nunca "auneron",
    "auneron_test" ou o banco configurado da aplicação.
    """

    assert_safe_cleanup_target(database=database)

    docker_exec_run(
        "psql",
        "-U",
        "auneron",
        "-d",
        "postgres",
        "-c",
        f"DROP DATABASE IF EXISTS {database};",
    )

    print(f"Cleanup concluído: {database!r} removido.")


def main() -> None:
    args = parse_args()

    if args.cleanup:
        run_cleanup(database=args.target_database)
        return

    if not args.backup_file or not args.target_dsn:
        raise SystemExit(
            "--backup-file e --target-dsn são obrigatórios fora do "
            "modo --cleanup."
        )

    run_restore(
        backup_file=args.backup_file,
        target_dsn=args.target_dsn,
        target_database=args.target_database,
    )


if __name__ == "__main__":
    main()
