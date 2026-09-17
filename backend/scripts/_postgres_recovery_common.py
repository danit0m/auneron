"""
PR-4 -- Production Backup / Restore / Recovery Drill V1. Modulo
compartilhado entre backup_postgres.py, restore_postgres.py e
verify_recovery.py.

Congelado em Architecture Freeze / PRE-APPLY Mechanical-Backup-Restore
Safety Contract Gate / Executable Contract (com as emendas finais:
target unico auneron_recovery_drill sem parametrizacao de nome,
checagem DSN x --target-database, DatabaseIdentity como dataclass
imutavel, guard tambem no cleanup) com Tomaz em 16/09/2026, a partir
do baseline `b9f83ec`.

Autenticacao: docker exec dentro do container ja-rodando do Postgres
nao exige senha (confirmado mecanicamente no PRE-APPLY Gate -- trust
local padrao da imagem oficial postgres:17-alpine, identica em dev e
producao). Nenhum wrapper le, monta ou imprime senha/DSN completo em
nenhum momento -- so identidade sanitizada (host, port, database).

RECOVERY_DATABASE e o UNICO alvo de restore/cleanup permitido pelo V1
-- deliberadamente estreito, nao um restaurador PostgreSQL generico.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from sqlalchemy.engine import make_url

from app.core.config import settings


RECOVERY_DATABASE = "auneron_recovery_drill"

FORBIDDEN_TARGET_DATABASES = frozenset({"auneron", "auneron_test"})

POSTGRES_CONTAINER_NAME = "auneron-postgres"


class RestoreTargetForbiddenError(Exception):
    """
    Levantado sempre que um alvo de restore ou cleanup nao satisfaz
    TODAS as condicoes do RESTORE TARGET CONTRACT congelado no
    Executable Contract. Nunca capturada silenciosamente -- deve
    abortar o processo.
    """


@dataclass(frozen=True)
class DatabaseIdentity:
    host: str
    port: int
    database: str


def normalized_identity(dsn: str) -> DatabaseIdentity:
    """
    Mesmo mecanismo ja usado em app/core/config.py (Settings.database_name,
    via sqlalchemy.engine.make_url) -- reuso, nao uma segunda forma de
    parsear URL de banco.
    """

    url = make_url(dsn)
    return DatabaseIdentity(
        host=url.host or "",
        port=url.port or 5432,
        database=url.database or "",
    )


def assert_safe_restore_target(
    *,
    target_dsn: str,
    target_database: str,
) -> DatabaseIdentity:
    """
    RESTORE TARGET CONTRACT (as 4 condicoes congeladas):

    1. target_database precisa ter sido informado explicitamente
       (garantido pelo chamador via argparse required=True).
    2. O unico target permitido pelo wrapper V1 e RECOVERY_DATABASE --
       nenhum outro nome, mesmo que "pareca" seguro.
    3. A identidade normalizada do DSN de destino nao pode ser igual a
       identidade normalizada de settings.database_url (o banco que a
       aplicacao usa agora, seja dev ou producao).
    4. O nome do target nao pode ser "auneron" nem "auneron_test",
       redundante com (2) mas verificado separadamente -- defesa em
       profundidade, nao a mesma checagem reescrita.

    Adicionalmente: o nome do banco embutido no DSN e o valor de
    --target-database precisam concordar -- nunca duas fontes de
    verdade divergentes sobre qual banco esta sendo restaurado.
    """

    target_identity = normalized_identity(target_dsn)

    if target_identity.database != target_database:
        raise RestoreTargetForbiddenError(
            "Target DSN database and --target-database disagree: "
            f"dsn={target_identity.database!r} "
            f"vs --target-database={target_database!r}."
        )

    if target_database != RECOVERY_DATABASE:
        raise RestoreTargetForbiddenError(
            f"O unico alvo permitido e {RECOVERY_DATABASE!r}; "
            f"recebido {target_database!r}."
        )

    if target_database in FORBIDDEN_TARGET_DATABASES:
        raise RestoreTargetForbiddenError(
            f"{target_database!r} nunca pode ser alvo de restore."
        )

    configured_identity = normalized_identity(settings.database_url)

    if target_identity == configured_identity:
        raise RestoreTargetForbiddenError(
            "O alvo do restore e identico ao banco configurado em uso "
            "por esta aplicacao."
        )

    return target_identity


def assert_safe_cleanup_target(*, database: str) -> None:
    """
    Mesma barreira usada no restore, reaplicada no cleanup -- um DROP
    DATABASE nunca deve confiar em um `finally` cego. So pode remover
    exatamente RECOVERY_DATABASE.
    """

    if database != RECOVERY_DATABASE:
        raise RestoreTargetForbiddenError(
            f"Cleanup recusado: so {RECOVERY_DATABASE!r} pode ser "
            f"removido por esta ferramenta; recebido {database!r}."
        )

    if database in {"auneron", "auneron_test"}:
        raise RestoreTargetForbiddenError(
            f"Cleanup recusado: {database!r} nunca pode ser removido "
            "por esta ferramenta."
        )


def sanitized_source_identity() -> DatabaseIdentity:
    """
    Identidade do banco de origem do backup (settings.database_url),
    sanitizada -- nunca inclui usuario/senha/DSN completo. E o que
    entra em qualquer log/evidencia (ex.: DATABASE_BACKUP_VALIDATION.md).
    """

    return normalized_identity(settings.database_url)


def docker_exec_run(
    *args: str,
    container: str = POSTGRES_CONTAINER_NAME,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """
    Wrapper fino sobre `docker exec`. SEMPRE lista de argumentos
    estruturada, NUNCA delega para um shell interpretar a linha de
    comando, NUNCA concatena senha/DSN em uma command string. Nenhuma
    credencial e passada aqui -- a autenticacao local dentro do
    container ja-rodando do Postgres nao exige senha (confirmado
    mecanicamente no PRE-APPLY Gate).
    """

    command = ["docker", "exec", container, *args]

    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
    )


def docker_cp(
    source: str,
    destination: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """
    Wrapper fino sobre `docker cp`. Mesma disciplina de docker_exec_run
    -- lista de argumentos estruturada, nunca delega para um shell.
    `source`/`destination` seguem a sintaxe do proprio `docker cp`
    (`<container>:<path>` para o lado do container, caminho local puro
    para o lado do host).
    """

    return subprocess.run(
        ["docker", "cp", source, destination],
        check=check,
        capture_output=True,
        text=True,
    )
