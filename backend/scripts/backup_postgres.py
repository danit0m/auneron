"""
PR-4 -- backup_postgres.py

Cria um backup logico (pg_dump -F c) do banco atualmente configurado
em settings.database_url. pg_dump roda DENTRO do container Postgres
ja-rodando (mesma versao do servidor por construcao, zero instalacao
separada de cliente), copiado para o host via `docker cp`.

Read-only sobre a origem -- pg_dump nunca escreve no banco lido.
Nenhum guard de destino se aplica aqui (nao ha "destino perigoso"
para um artefato de arquivo local); mas a identidade da origem e
sempre resolvida e impressa de forma sanitizada (host/port/database),
nunca usuario/senha/DSN completo -- essa e a proveniencia que
DATABASE_BACKUP_VALIDATION.md exige.
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime
from datetime import timezone
from pathlib import Path

from scripts._postgres_recovery_common import docker_cp
from scripts._postgres_recovery_common import docker_exec_run
from scripts._postgres_recovery_common import sanitized_source_identity


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cria um backup lógico (pg_dump -F c) do banco "
            "configurado em DATABASE_URL."
        )
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Diretório local onde o artefato de backup será copiado.",
    )
    return parser.parse_args()


def run_backup(*, output_dir: Path) -> Path:
    """
    O chamador (main) é responsável por criar output_dir se
    necessário. Retorna o caminho local do artefato .dump.
    """

    identity = sanitized_source_identity()
    print(
        "Origem: "
        f"host={identity.host} "
        f"port={identity.port} "
        f"database={identity.database}"
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    container_path = f"/tmp/backup_{stamp}.dump"
    local_path = output_dir / f"backup_{stamp}.dump"

    docker_exec_run(
        "pg_dump",
        "-U",
        "auneron",
        "-d",
        identity.database,
        "-F",
        "c",
        "-f",
        container_path,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    docker_cp(
        f"auneron-postgres:{container_path}",
        str(local_path),
    )
    docker_exec_run("rm", "-f", container_path)

    checksum = hashlib.sha256(
        local_path.read_bytes()
    ).hexdigest()
    checksum_path = local_path.with_suffix(
        local_path.suffix + ".sha256"
    )
    checksum_path.write_text(
        f"{checksum}  {local_path.name}\n",
        encoding="utf-8",
    )

    size_bytes = local_path.stat().st_size

    print(f"Artefato: {local_path}")
    print(f"Tamanho: {size_bytes} bytes")
    print(f"SHA-256: {checksum}")

    return local_path


def main() -> None:
    args = parse_args()
    run_backup(output_dir=Path(args.output_dir))


if __name__ == "__main__":
    main()
