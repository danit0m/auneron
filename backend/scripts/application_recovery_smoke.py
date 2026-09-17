"""
P2 -- Application Recovery Smoke V1 (Layer B).

Sobe um container temporario da imagem real do backend, apontado para
RECOVERY_DATABASE ("auneron_recovery_drill"), com MAINTENANCE_ENABLED=false,
e prova: /health, /ready, login real de um principal ja existente no
snapshot, e uma leitura de dominio autenticada (GET /accounts/?limit=1).
Nunca cria, ajusta senha/role, ou inventa um principal -- ausencia de
credencial configurada, usuario inexistente, senha invalida ou falta de
clients.view produzem BLOCKED, nunca PASS e nunca uma falha do mecanismo
em si.

Reaproveita scripts/_postgres_recovery_common.py (RECOVERY_DATABASE,
POSTGRES_CONTAINER_NAME, docker_exec_run, assert_safe_cleanup_target) e
scripts/verify_recovery.py (_table_set/_row_counts) sem alterar nenhum
dos dois. Layer A (Database Recovery Verification) continua exigindo
igualdade integral com a origem antes de qualquer coisa aqui; este
script so compara o proprio RECOVERY_DATABASE consigo mesmo, antes e
depois do smoke autenticado, contra uma allowlist estreita.

DATABASE_URL/API_KEY do container temporario sao entregues via
Docker secret-file (bind mount para /run/secrets/<nome>), nunca via
`docker run -e`, nunca em argv/log -- mesmo mecanismo que
Settings.model_config (secrets_dir="/run/secrets") ja usa em producao
hoje (confirmado empiricamente antes deste APPLY). A senha do
principal do smoke (SMOKE_LOGIN_PASSWORD_FILE) e lida pelo processo
Python deste script, nunca passada como argumento CLI, nunca escrita
em log.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import settings
from scripts._postgres_recovery_common import (
    POSTGRES_CONTAINER_NAME,
)
from scripts._postgres_recovery_common import RECOVERY_DATABASE
from scripts._postgres_recovery_common import (
    assert_safe_cleanup_target,
)
from scripts._postgres_recovery_common import docker_exec_run
from scripts.verify_recovery import _row_counts
from scripts.verify_recovery import _table_set


SMOKE_CONTAINER_NAME = "auneron-recovery-smoke"
SMOKE_IMAGE = "auneron-backend:local"
SMOKE_HOST_PORT = 18000

# Mutacoes inerentes ao mecanismo normal de login (create_session):
# uma nova linha em auth_sessions, e users.last_login_at do principal
# usado. Nenhuma outra tabela/coluna pode mudar.
ALLOWLISTED_CARDINALITY_DELTA = {"auth_sessions": 1}


class ApplicationRecoverySmokeBlockedError(Exception):
    """
    Pre-condicao nao satisfeita (credencial ausente, principal
    inexistente/inativo, senha invalida, falta de clients.view).
    Nunca e uma falha do mecanismo -- BLOCKED, nao FAIL.
    """


class ApplicationRecoverySmokeFailedError(Exception):
    """
    Mutacao fora da allowlist, timeout, ou qualquer comportamento
    nao esperado do container/servico. FAIL real.
    """


@dataclass(frozen=True)
class SmokeCredentials:
    email: str
    password: str


@dataclass(frozen=True)
class SmokeOutcome:
    status: str  # "PASS" | "FAIL" | "BLOCKED"
    detail: str


def discover_single_network(
    container: str = POSTGRES_CONTAINER_NAME,
) -> str:
    """
    Nunca escolhe arbitrariamente entre multiplas redes. Container
    conectado a 0 ou >1 rede -> falha fechada, nunca adivinha.
    """

    result = subprocess.run(
        [
            "docker",
            "inspect",
            container,
            "--format",
            "{{json .NetworkSettings.Networks}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    networks = json.loads(result.stdout)

    if len(networks) != 1:
        raise ApplicationRecoverySmokeFailedError(
            f"Esperada exatamente 1 rede para {container!r}; "
            f"encontradas {len(networks)}: {sorted(networks)}."
        )

    return next(iter(networks))


def recovery_database_url() -> str:
    """
    Deriva a DSN do banco de recovery a partir da DSN ja configurada,
    via make_url(...).set(...) -- nunca concatenacao manual de string.

    O container temporario roda na rede Docker interna do Postgres,
    onde o host e resolvido pelo NOME DO CONTAINER (DNS interno do
    Docker), nunca "localhost" (que dentro do container se refere ao
    proprio container) -- por isso host/port tambem sao trocados para
    o nome do container Postgres na porta interna padrao (5432),
    preservando usuario/senha da DSN ja configurada.
    """

    url = make_url(settings.database_url).set(
        database=RECOVERY_DATABASE,
        host=POSTGRES_CONTAINER_NAME,
        port=5432,
    )
    return url.render_as_string(hide_password=False)


def load_smoke_credentials() -> SmokeCredentials | None:
    """
    SMOKE_LOGIN_EMAIL e SMOKE_LOGIN_PASSWORD_FILE sao obrigatorios
    juntos, sem default. Ausencia de qualquer um -> None (o chamador
    trata como BLOCKED da etapa autenticada, nunca como erro).
    """

    email = os.environ.get("SMOKE_LOGIN_EMAIL")
    password_file = os.environ.get(
        "SMOKE_LOGIN_PASSWORD_FILE"
    )

    if not email or not password_file:
        return None

    password_path = Path(password_file)

    if not password_path.is_file():
        raise ApplicationRecoverySmokeBlockedError(
            "SMOKE_LOGIN_PASSWORD_FILE não aponta para um "
            "arquivo existente."
        )

    password = password_path.read_text(
        encoding="utf-8"
    ).strip()

    if not password:
        raise ApplicationRecoverySmokeBlockedError(
            "SMOKE_LOGIN_PASSWORD_FILE está vazio."
        )

    return SmokeCredentials(
        email=email, password=password
    )


@contextmanager
def _secret_files(dsn: str, api_key: str):
    with tempfile.TemporaryDirectory(
        prefix="auneron-recovery-smoke-"
    ) as tmp_dir:
        dsn_path = Path(tmp_dir) / "database_url"
        api_key_path = Path(tmp_dir) / "api_key"

        dsn_path.write_text(dsn, encoding="utf-8")
        api_key_path.write_text(
            api_key, encoding="utf-8"
        )

        dsn_path.chmod(0o600)
        api_key_path.chmod(0o600)

        yield dsn_path, api_key_path


def start_smoke_container(
    *,
    network: str,
    dsn_secret_path: Path,
    api_key_secret_path: Path,
) -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            SMOKE_CONTAINER_NAME,
            "--network",
            network,
            "-p",
            f"127.0.0.1:{SMOKE_HOST_PORT}:8000",
            "-e",
            "APP_ENV=development",
            "-e",
            "MAINTENANCE_ENABLED=false",
            "--mount",
            (
                "type=bind,source="
                f"{dsn_secret_path},"
                "target=/run/secrets/database_url,readonly"
            ),
            "--mount",
            (
                "type=bind,source="
                f"{api_key_secret_path},"
                "target=/run/secrets/api_key,readonly"
            ),
            SMOKE_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def stop_smoke_container() -> None:
    subprocess.run(
        ["docker", "stop", SMOKE_CONTAINER_NAME],
        check=False,
        capture_output=True,
        text=True,
    )


def _wait_for_http_ok(
    url: str, *, timeout_seconds: float = 30.0
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                url, timeout=2
            ) as response:
                if response.status == 200:
                    return
        except (
            urllib.error.URLError,
            ConnectionError,
        ) as error:
            last_error = error

        time.sleep(0.5)

    raise ApplicationRecoverySmokeFailedError(
        f"Timeout aguardando {url}: {last_error}"
    )


def _base_url() -> str:
    return f"http://127.0.0.1:{SMOKE_HOST_PORT}"


def _login_and_read_accounts(
    credentials: SmokeCredentials, api_key: str
) -> None:
    """
    Faz login real (nunca cria/ajusta o usuário) e uma leitura
    representativa (GET /accounts/?limit=1). 401 no login ou 403 na
    leitura viram BLOCKED -- nunca tenta outro principal, nunca
    contorna.
    """

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar)
    )

    login_request = urllib.request.Request(
        f"{_base_url()}/auth/login",
        data=json.dumps(
            {
                "email": credentials.email,
                "password": credentials.password,
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
        method="POST",
    )

    try:
        opener.open(login_request, timeout=5)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise ApplicationRecoverySmokeBlockedError(
                "Login do principal configurado foi "
                "recusado (401)."
            ) from error
        raise ApplicationRecoverySmokeFailedError(
            f"Login falhou com status inesperado: {error.code}."
        ) from error

    accounts_request = urllib.request.Request(
        f"{_base_url()}/accounts/?limit=1",
        headers={"X-API-Key": api_key},
        method="GET",
    )

    try:
        opener.open(accounts_request, timeout=5)
    except urllib.error.HTTPError as error:
        if error.code == 403:
            raise ApplicationRecoverySmokeBlockedError(
                "Principal configurado autenticou mas não "
                "tem clients.view (403)."
            ) from error
        raise ApplicationRecoverySmokeFailedError(
            "GET /accounts/?limit=1 falhou com status "
            f"inesperado: {error.code}."
        ) from error


def _snapshot(database: str) -> dict[str, int]:
    tables = _table_set(database)
    return _row_counts(database, tables)


def _last_login_at(database: str, email: str) -> str:
    escaped_email = email.replace("'", "''")
    result = docker_exec_run(
        "psql",
        "-U",
        "auneron",
        "-d",
        database,
        "-tAc",
        (
            "SELECT COALESCE(last_login_at::text, '') "
            "FROM users WHERE email = "
            f"'{escaped_email}';"
        ),
    )
    return result.stdout.strip()


def assert_only_allowlisted_mutation(
    *,
    before: dict[str, int],
    after: dict[str, int],
) -> None:
    """
    Fail-closed: qualquer diferença de cardinalidade fora de
    ALLOWLISTED_CARDINALITY_DELTA levanta
    ApplicationRecoverySmokeFailedError. Nunca produz só um relatório.
    """

    if set(before) != set(after):
        raise ApplicationRecoverySmokeFailedError(
            "Conjunto de tabelas mudou entre antes e depois "
            "do smoke autenticado."
        )

    unexpected: dict[str, tuple[int, int]] = {}

    for table in before:
        delta = after[table] - before[table]
        allowed_delta = (
            ALLOWLISTED_CARDINALITY_DELTA.get(table, 0)
        )

        if table in ALLOWLISTED_CARDINALITY_DELTA:
            if delta != allowed_delta:
                unexpected[table] = (
                    before[table],
                    after[table],
                )
        elif delta != 0:
            unexpected[table] = (
                before[table],
                after[table],
            )

    if unexpected:
        raise ApplicationRecoverySmokeFailedError(
            "Mutação fora da allowlist detectada: "
            f"{unexpected}."
        )


def run_application_recovery_smoke() -> SmokeOutcome:
    network = discover_single_network()
    dsn = recovery_database_url()
    api_key = (
        settings.api_key.get_secret_value()
        if settings.api_key is not None
        else "auneron-recovery-smoke-key-min-32-characters"
    )

    before_snapshot = _snapshot(RECOVERY_DATABASE)

    with _secret_files(dsn, api_key) as (
        dsn_secret_path,
        api_key_secret_path,
    ):
        start_smoke_container(
            network=network,
            dsn_secret_path=dsn_secret_path,
            api_key_secret_path=api_key_secret_path,
        )

        try:
            _wait_for_http_ok(
                f"{_base_url()}/health"
            )
            _wait_for_http_ok(
                f"{_base_url()}/ready"
            )

            try:
                credentials = load_smoke_credentials()
            except ApplicationRecoverySmokeBlockedError as error:
                return SmokeOutcome(
                    status="BLOCKED",
                    detail=(
                        "/health e /ready OK; etapa "
                        f"autenticada BLOCKED — {error}"
                    ),
                )

            if credentials is None:
                return SmokeOutcome(
                    status="BLOCKED",
                    detail=(
                        "/health e /ready OK; etapa "
                        "autenticada BLOCKED — "
                        "SMOKE_LOGIN_EMAIL/"
                        "SMOKE_LOGIN_PASSWORD_FILE não "
                        "configurados."
                    ),
                )

            before_last_login = _last_login_at(
                RECOVERY_DATABASE, credentials.email
            )

            try:
                _login_and_read_accounts(
                    credentials, api_key
                )
            except ApplicationRecoverySmokeBlockedError as error:
                return SmokeOutcome(
                    status="BLOCKED",
                    detail=(
                        "/health e /ready OK; etapa "
                        f"autenticada BLOCKED — {error}"
                    ),
                )

            after_snapshot = _snapshot(
                RECOVERY_DATABASE
            )
            assert_only_allowlisted_mutation(
                before=before_snapshot,
                after=after_snapshot,
            )

            after_last_login = _last_login_at(
                RECOVERY_DATABASE, credentials.email
            )

            if after_last_login == before_last_login:
                raise ApplicationRecoverySmokeFailedError(
                    "users.last_login_at do principal não "
                    "mudou após login bem-sucedido."
                )

            return SmokeOutcome(
                status="PASS",
                detail=(
                    "/health, /ready, login e "
                    "GET /accounts/?limit=1 OK; única "
                    "mutação observada foi +1 auth_sessions "
                    "e users.last_login_at do principal."
                ),
            )
        finally:
            stop_smoke_container()


def main() -> None:
    outcome = run_application_recovery_smoke()
    print(f"Application Recovery Layer B: {outcome.status}")
    print(outcome.detail)

    if outcome.status == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
