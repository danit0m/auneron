import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


BACKEND_DIR = Path(__file__).resolve().parents[1]

# Quando este arquivo é executado diretamente com
# `python scripts/e2e_frontend.py`, o Python adiciona
# `backend/scripts` ao sys.path, não `backend`.
# Incluímos explicitamente a raiz do backend para que
# imports como `from app...` funcionem localmente e no CI.
backend_path = str(BACKEND_DIR)

if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

REPOSITORY_DIR = BACKEND_DIR.parent
FRONTEND_DIR = REPOSITORY_DIR / "frontend"

BACKEND_URL = "http://127.0.0.1:8001"
FRONTEND_URL = "http://127.0.0.1:5174"

E2E_EMAIL = "e2e.frontend@example.com"
E2E_PASSWORD = "Auneron-E2E-Test-Password-2026!"


def _load_env_file(
    path: Path,
) -> dict[str, str]:
    values: dict[str, str] = {}

    if not path.exists():
        return values

    for raw_line in path.read_text(
        encoding="utf-8-sig"
    ).splitlines():
        line = raw_line.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        name, value = line.split("=", 1)
        values[name.strip()] = (
            value.strip()
            .strip('"')
            .strip("'")
        )

    return values


def _required_test_environment() -> tuple[
    str,
    str,
]:
    local_values = _load_env_file(
        BACKEND_DIR / ".env.test"
    )

    database_url = (
        os.environ.get(
            "TEST_DATABASE_URL"
        )
        or local_values.get(
            "TEST_DATABASE_URL"
        )
    )
    api_key = (
        os.environ.get(
            "TEST_API_KEY"
        )
        or local_values.get(
            "TEST_API_KEY"
        )
    )

    if not database_url:
        raise RuntimeError(
            "TEST_DATABASE_URL não está definida."
        )

    if not api_key:
        raise RuntimeError(
            "TEST_API_KEY não está definida."
        )

    return database_url, api_key


def _wait_for_url(
    url: str,
    *,
    timeout_seconds: float = 30.0,
) -> None:
    deadline = (
        time.monotonic()
        + timeout_seconds
    )
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with urlopen(
                url,
                timeout=2,
            ) as response:
                if response.status < 500:
                    return
        except (
            OSError,
            URLError,
        ) as error:
            last_error = error

        time.sleep(0.4)

    raise RuntimeError(
        f"Serviço indisponível: {url}. "
        f"Último erro: {last_error}"
    )


def _terminate_process(
    process: subprocess.Popen,
) -> None:
    if process.poll() is not None:
        return

    if os.name == "nt":
        subprocess.run(
            [
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    process.terminate()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _configure_test_process_environment(
    database_url: str,
    api_key: str,
) -> None:
    os.environ["APP_ENV"] = "test"
    os.environ["DATABASE_URL"] = (
        database_url
    )
    os.environ["API_KEY"] = api_key


def _prepare_e2e_user() -> None:
    from app.core.authentication import (
        hash_password,
    )
    from app.database.database import (
        SessionLocal,
    )
    from app.models.auth_session import (
        AuthSession,
    )
    from app.models.user import User

    db = SessionLocal()

    try:
        existing = (
            db.query(User)
            .filter(
                User.email == E2E_EMAIL
            )
            .one_or_none()
        )

        if existing is not None:
            (
                db.query(AuthSession)
                .filter(
                    AuthSession.user_id
                    == existing.id
                )
                .delete(
                    synchronize_session=False
                )
            )
            db.delete(existing)
            db.commit()

        user = User(
            name="E2E Frontend",
            email=E2E_EMAIL,
            password_hash=hash_password(
                E2E_PASSWORD
            ),
            role="developer",
            active=True,
        )

        db.add(user)
        db.commit()
    finally:
        db.close()


def _cleanup_e2e_user() -> None:
    from app.database.database import (
        SessionLocal,
    )
    from app.models.auth_session import (
        AuthSession,
    )
    from app.models.user import User

    db = SessionLocal()

    try:
        user = (
            db.query(User)
            .filter(
                User.email == E2E_EMAIL
            )
            .one_or_none()
        )

        if user is None:
            return

        (
            db.query(AuthSession)
            .filter(
                AuthSession.user_id
                == user.id
            )
            .delete(
                synchronize_session=False
            )
        )
        db.delete(user)
        db.commit()
    finally:
        db.close()


def main() -> None:
    database_url, api_key = (
        _required_test_environment()
    )

    _configure_test_process_environment(
        database_url,
        api_key,
    )
    _prepare_e2e_user()

    backend_env = os.environ.copy()
    frontend_env = os.environ.copy()
    frontend_env.update({
        "AUNERON_BACKEND_URL": BACKEND_URL,
        "AUNERON_API_KEY": api_key,
    })

    npm_command = (
        "npm.cmd"
        if os.name == "nt"
        else "npm"
    )

    backend_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
        ],
        cwd=BACKEND_DIR,
        env=backend_env,
    )

    frontend_process: subprocess.Popen | None = None

    try:
        _wait_for_url(
            f"{BACKEND_URL}/health"
        )

        frontend_process = subprocess.Popen(
            [
                npm_command,
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                "5174",
                "--strictPort",
            ],
            cwd=FRONTEND_DIR,
            env=frontend_env,
        )

        _wait_for_url(
            FRONTEND_URL
        )

        responses: list[
            tuple[str, int]
        ] = []

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True
                )
                page = browser.new_page()

                page.on(
                    "response",
                    lambda response: responses.append(
                        (
                            response.url,
                            response.status,
                        )
                    ),
                )

                page.goto(
                    FRONTEND_URL,
                    wait_until="domcontentloaded",
                )

                page.get_by_role(
                    "heading",
                    name="Entrar no Auneron",
                ).wait_for(
                    timeout=15000
                )

                page.get_by_label(
                    "E-mail",
                    exact=True,
                ).fill(E2E_EMAIL)

                page.get_by_label(
                    "Senha",
                    exact=True,
                ).fill(E2E_PASSWORD)

                page.get_by_role(
                    "button",
                    name="Entrar",
                    exact=True,
                ).click()

                page.get_by_text(
                    "Total de clientes",
                    exact=True,
                ).wait_for(
                    timeout=15000
                )

                login_statuses = [
                    status
                    for url, status in responses
                    if "/api/auth/login" in url
                ]

                if (
                    not login_statuses
                    or 200 not in login_statuses
                ):
                    raise RuntimeError(
                        "Login não recebeu HTTP 200 "
                        "através do proxy seguro."
                    )

                dashboard_statuses = [
                    status
                    for url, status in responses
                    if "/api/dashboard/" in url
                ]

                if (
                    not dashboard_statuses
                    or 200 not in dashboard_statuses
                ):
                    raise RuntimeError(
                        "Dashboard não recebeu HTTP 200 "
                        "após autenticação."
                    )

                page.reload(
                    wait_until="domcontentloaded"
                )

                page.get_by_text(
                    "Total de clientes",
                    exact=True,
                ).wait_for(
                    timeout=15000
                )

                with page.expect_response(
                    lambda response: (
                        "/api/accounts/" in response.url
                        and response.status == 200
                    ),
                    timeout=15000,
                ) as accounts_response:
                    page.goto(
                        f"{FRONTEND_URL}/clientes",
                        wait_until="domcontentloaded",
                    )

                page.get_by_text(
                    "Gestão de Clientes",
                    exact=True,
                ).wait_for(
                    timeout=15000
                )

                page.get_by_text(
                    "Carteira de clientes",
                    exact=True,
                ).wait_for(
                    timeout=15000
                )

                if accounts_response.value.status != 200:
                    raise RuntimeError(
                        "Clientes não recebeu HTTP 200 "
                        "após autenticação."
                    )

                browser.close()

        except PlaywrightError as error:
            if "Executable doesn't exist" in str(error):
                raise RuntimeError(
                    "Chromium do Playwright não está "
                    "instalado. Execute: "
                    "python -m playwright install chromium"
                ) from error
            raise

        print("E2E frontend: OK")
        print("Login real via /api/auth/login: HTTP 200")
        print("Dashboard autenticado via /api: HTTP 200")
        print("Sessão restaurada após reload: OK")
        print("Clientes autenticado via /api: HTTP 200")
        print("API key permaneceu no proxy do Vite.")

    finally:
        if frontend_process is not None:
            _terminate_process(
                frontend_process
            )

        _terminate_process(
            backend_process
        )

        _cleanup_e2e_user()


if __name__ == "__main__":
    main()
